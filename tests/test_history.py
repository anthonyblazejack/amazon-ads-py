from __future__ import annotations

import json
from datetime import UTC, datetime

import respx
from httpx import Response

from amazon_ads import HistoryEvent, ProfileClient
from amazon_ads.history import to_epoch_ms
from tests.conftest import NA

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)


def event(entity_id: str, change: str, old: str, new: str, hours: int, **kw: str) -> dict:  # type: ignore[type-arg]
    return {
        "entityType": kw.get("entity_type", "KEYWORD"),
        "entityId": entity_id,
        "changeType": change,
        "previousValue": old,
        "newValue": new,
        "timestamp": to_epoch_ms(T0) + hours * 3_600_000,
        "metadata": {"keyword": "mystery novels", "keywordType": "KEYWORD_EXACT"},
    }


def test_whole_account_query_asks_for_the_advertiser(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # Amazon returns no events at all for a selector without ids or parents.
    route = api.post(f"{NA}/history").mock(return_value=Response(200, json={"events": []}))
    us.history.list(since=T0, entity_types=["KEYWORD"])
    body = json.loads(route.calls.last.request.content)
    assert body["eventTypes"] == {"KEYWORD": {"parents": [{"useProfileIdAdvertiser": True}]}}


def test_results_are_filtered_by_type_and_change_locally(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.post(f"{NA}/history").mock(
        return_value=Response(
            200,
            json={
                "events": [
                    event("1", "BID_AMOUNT", "0.5", "0.6", 1),
                    event("1", "CREATED", "", "", 0),
                    event("t", "BID_AMOUNT", "0.2", "0.3", 1, entity_type="PRODUCT_TARGETING"),
                ]
            },
        )
    )
    found = us.history.list(since=T0, entity_types=["KEYWORD"], changes=["BID_AMOUNT"])
    assert [(e.entity_id, e.change_type) for e in found] == [("1", "BID_AMOUNT")]


def test_history_pages_through_next_token(api: respx.MockRouter, us: ProfileClient) -> None:
    route = api.post(f"{NA}/history").mock(
        side_effect=[
            Response(
                200, json={"events": [event("1", "BID_AMOUNT", "1", "2", 1)], "nextToken": "n"}
            ),
            Response(200, json={"events": [event("2", "BID_AMOUNT", "1", "2", 2)]}),
        ]
    )
    assert len(us.history.list(since=T0, entity_types=["KEYWORD"])) == 2
    assert json.loads(route.calls[1].request.content)["nextToken"] == "n"


def test_value_at_answers_what_a_bid_was_on_a_past_day(us: ProfileClient) -> None:
    events = [
        HistoryEvent.model_validate(event("1", "BID_AMOUNT", "0.50", "0.60", 1)),
        HistoryEvent.model_validate(event("1", "BID_AMOUNT", "0.60", "0.45", 10)),
    ]

    def at(hours: int) -> str | None:
        moment = datetime.fromtimestamp((to_epoch_ms(T0) + hours * 3_600_000) / 1000, UTC)
        return us.history.value_at("KEYWORD", "1", "BID_AMOUNT", moment, events=events)

    assert at(0) == "0.50"
    assert at(5) == "0.60"
    assert at(20) == "0.45"


def test_rollback_plan_restores_value_before_first_change(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.post(f"{NA}/history").mock(
        return_value=Response(
            200,
            json={
                "events": [
                    event("1", "BID_AMOUNT", "0.5", "0.6", 1),
                    event("1", "BID_AMOUNT", "0.6", "0.4", 2),
                    event("1", "STATUS", "ENABLED", "PAUSED", 3),
                    event("2", "BID_AMOUNT", "0.3", "0.9", 1),
                    event("2", "BID_AMOUNT", "0.9", "0.3", 2),
                ]
            },
        )
    )
    plan = us.history.rollback_plan(T0, entity_types=["KEYWORD"])
    [change] = plan.changes  # keyword 2 ended where it started, so nothing to restore
    assert change.id == "1"
    assert change.after == {"bid": 0.5, "state": "ENABLED"}
    assert change.before == {"bid": 0.4, "state": "PAUSED"}
    assert change.label == "mystery novels [EXACT]"
