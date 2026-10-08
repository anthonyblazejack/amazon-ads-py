from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from amazon_ads import ChangePlan, PlanDriftError, PlannedChange, ProfileClient
from tests.conftest import NA, US_PROFILE


def bid_plan(*changes: tuple[str, float | None, float]) -> ChangePlan:
    return ChangePlan.new(
        profile_id=US_PROFILE,
        country_code="US",
        changes=[
            PlannedChange(kind="sp.keywords", id=i, before={"bid": b}, after={"bid": a})
            for i, b, a in changes
        ],
    )


def test_inverse_restores_before_values() -> None:
    plan = bid_plan(("1", 0.5, 0.7))
    [change] = plan.inverse().changes
    assert (change.before, change.after) == ({"bid": 0.7}, {"bid": 0.5})


def test_inverse_skips_fields_with_no_previous_value() -> None:
    # A keyword with no bid of its own uses the ad group default; an update cannot put
    # "no bid" back, so the inverse must not pretend it can.
    inverse = bid_plan(("1", None, 0.7)).inverse()
    assert inverse.changes == []
    assert "no previous value for bid" in inverse.warnings[0]


def test_saved_plan_round_trips_and_detects_tampering(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plan = bid_plan(("1", 0.5, 0.7))
    path = plan.save(tmp_path)
    assert ChangePlan.load(path).fingerprint == plan.fingerprint

    data = json.loads(path.read_text())
    data["changes"][0]["after"]["bid"] = 7.0
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="edited after it was saved"):
        ChangePlan.load(path)


def test_fingerprint_ignores_id_and_time_but_not_content() -> None:
    a, b = bid_plan(("1", 0.5, 0.7)), bid_plan(("1", 0.5, 0.7))
    assert a.id != b.id
    assert a.fingerprint == b.fingerprint
    assert bid_plan(("1", 0.5, 0.8)).fingerprint != a.fingerprint


def test_apply_refuses_a_plan_for_another_profile(us: ProfileClient) -> None:
    plan = bid_plan(("1", 0.5, 0.7))
    plan.profile_id = 42
    with pytest.raises(ValueError, match="built for profile 42"):
        plan.apply(us)


def test_apply_with_drift_check_refuses_stale_plan(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.post(f"{NA}/sp/keywords/list").mock(
        return_value=Response(200, json={"keywords": [{"keywordId": "1", "bid": 0.55}]})
    )
    update = api.put(f"{NA}/sp/keywords")
    with pytest.raises(PlanDriftError) as info:
        bid_plan(("1", 0.5, 0.7)).apply(us, check_drift=True)
    assert info.value.drifted[0]["actual"] == 0.55
    assert not update.called


def test_rollback_reverses_only_what_amazon_accepted(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.put(f"{NA}/sp/keywords").mock(
        return_value=Response(
            207,
            json={
                "keywords": {
                    "success": [{"index": 0, "keywordId": "1"}],
                    "error": [{"index": 1, "errors": [{"errorType": "rangeError"}]}],
                }
            },
        )
    )
    api.post(f"{NA}/sp/negativeKeywords").mock(
        return_value=Response(
            207, json={"negativeKeywords": {"success": [{"index": 0, "negativeKeywordId": "77"}]}}
        )
    )
    plan = bid_plan(("1", 0.5, 0.7), ("2", 0.3, 9.9))
    plan.changes.append(
        PlannedChange(
            kind="sp.negative_keywords",
            id=None,
            before={},
            after={"campaignId": "c", "keywordText": "free", "state": "ENABLED"},
            op="create",
        )
    )
    result = plan.apply(us)
    assert not result.ok
    rollback = result.rollback_plan()
    ops = {(c.op, c.kind, c.id): c.after for c in rollback.changes}
    assert ops == {
        ("update", "sp.keywords", "1"): {"bid": 0.5},
        ("archive", "sp.negative_keywords", "77"): {"state": "ARCHIVED"},
    }
    assert rollback.reverses == plan.id


def test_archive_changes_are_sent_to_the_delete_endpoint(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.post(f"{NA}/sp/keywords/delete").mock(
        return_value=Response(207, json={"keywords": {"success": [{"index": 0, "keywordId": "1"}]}})
    )
    plan = ChangePlan.new(
        profile_id=US_PROFILE,
        country_code="US",
        changes=[
            PlannedChange(
                kind="sp.keywords",
                id="1",
                before={"state": "ENABLED"},
                after={"state": "ARCHIVED"},
                op="archive",
            )
        ],
    )
    plan.apply(us).raise_for_errors()
    assert json.loads(route.calls.last.request.content) == {"keywordIdFilter": {"include": ["1"]}}


def test_save_to_a_new_directory_puts_the_plan_inside_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plan = bid_plan(("1", 0.5, 0.7))
    path = plan.save(tmp_path / "rollbacks")
    assert path == tmp_path / "rollbacks" / f"plan-{plan.id}.json"
    assert ChangePlan.load(path).changes == plan.changes


def campaign_list(api: respx.MockRouter, budget: dict[str, object]) -> None:
    api.post(f"{NA}/sp/campaigns/list").mock(
        return_value=Response(
            200, json={"campaigns": [{"campaignId": "9", "name": "Mysteries", "budget": budget}]}
        )
    )


def test_a_number_budget_plans_the_object_amazon_requires_on_both_sides(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # A plan whose before was an object and whose after was a bare number was built
    # happily and then rejected by Amazon with a 400 that named no field. The mismatched
    # shapes in the table were the only clue, so the plan must show both the same way.
    campaign_list(api, {"budget": 10.0, "budgetType": "DAILY"})
    plan = us.sp.campaigns.plan_update([{"campaignId": "9", "budget": 20}])
    [row] = plan.table()
    assert row["before"] == {"budget": 10.0, "budgetType": "DAILY"}
    assert row["after"] == {"budget": 20.0, "budgetType": "DAILY"}

    route = api.put(f"{NA}/sp/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "9"}]}}
        )
    )
    plan.apply(us)
    [campaign] = json.loads(route.calls.last.request.content)["campaigns"]
    assert campaign == {"campaignId": "9", "budget": {"budget": 20.0, "budgetType": "DAILY"}}


def test_a_number_budget_keeps_the_campaigns_own_budget_type(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # Defaulting to DAILY here would quietly turn a lifetime budget into a daily one.
    campaign_list(api, {"budget": 500.0, "budgetType": "LIFETIME"})
    plan = us.sp.campaigns.plan_update([{"campaignId": "9", "budget": 800}])
    [change] = plan.changes
    assert change.after == {"budget": {"budget": 800.0, "budgetType": "LIFETIME"}}


def test_a_number_budget_equal_to_the_current_one_plans_nothing(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # The scalar must be compared in the coerced shape, or every budget would look changed.
    campaign_list(api, {"budget": 10.0, "budgetType": "DAILY"})
    assert us.sp.campaigns.plan_update([{"campaignId": "9", "budget": 10}]).changes == []


def test_a_budget_plan_reverses_to_the_whole_object(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    campaign_list(api, {"budget": 10.0, "budgetType": "DAILY"})
    plan = us.sp.campaigns.plan_update([{"campaignId": "9", "budget": 20}])
    [change] = plan.inverse().changes
    assert change.after == {"budget": {"budget": 10.0, "budgetType": "DAILY"}}


def test_plan_create_plans_the_budget_shape_it_will_send(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # A plan that showed a bare number and then sent an object would put the same
    # plan-versus-request mismatch back, one approval step earlier.
    plan = us.sp.campaigns.plan_create([{"name": "Mysteries", "state": "PAUSED", "budget": 15}])
    [row] = [r for r in plan.table() if r["field"] == "budget"]
    assert row["after"] == {"budget": 15.0, "budgetType": "DAILY"}

    route = api.post(f"{NA}/sp/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "9"}]}}
        )
    )
    plan.apply(us)
    [campaign] = json.loads(route.calls.last.request.content)["campaigns"]
    assert campaign["budget"] == row["after"]


def test_plan_create_refuses_an_impossible_budget_before_approval(us: ProfileClient) -> None:
    with pytest.raises(ValueError, match=r"sp\.campaigns budget takes a number"):
        us.sp.campaigns.plan_create([{"name": "Mysteries", "budget": ["15"]}])


def test_plan_update_refuses_a_budget_object_with_no_amount(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # plan_update is the path the MCP server and the CLI take, so it has to refuse as
    # well, rather than planning a budget Amazon would answer with a bare 400.
    campaign_list(api, {"budget": 10.0, "budgetType": "DAILY"})
    with pytest.raises(ValueError, match=r"sp\.campaigns budget is not a valid Budget"):
        us.sp.campaigns.plan_update([{"campaignId": "9", "budget": {"budgetType": "LIFETIME"}}])
