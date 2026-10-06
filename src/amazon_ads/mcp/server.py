"""An MCP server that lets Claude (or any MCP client) read and change an Amazon Ads account.

Reads are direct. Every write goes through a change plan in two steps, so nothing changes
until a person has seen exactly what will change:

1. A ``plan_*`` tool builds the plan, saves it, and returns its table and fingerprint.
2. ``apply_plan`` sends it, and only when given that same fingerprint, so the plan that
   runs is byte for byte the plan that was shown. It saves the result and a rollback
   plan, which is applied the same way to undo the change.

Configuration comes from the environment:

``AMAZON_ADS_CLIENT_ID``, ``AMAZON_ADS_CLIENT_SECRET``, ``AMAZON_ADS_REFRESH_TOKEN``
    Credentials (or put them in the file named by ``AMAZON_ADS_ENV_FILE``).
``AMAZON_ADS_MCP_READ_ONLY=1``
    Register no write tools at all.
``AMAZON_ADS_MCP_PLAN_DIR``
    Where plans, results and rollback plans are kept. Default
    ``~/.local/state/amazon-ads/plans``.
``AMAZON_ADS_MCP_DB``
    SQLite warehouse path; enables ``sync_reports`` and ``query_reports``.
"""

from __future__ import annotations

import functools
import json
import os
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from amazon_ads import catalog
from amazon_ads.bids import Expression, expression_for_keyword, expression_for_target
from amazon_ads.client import AmazonAds, ProfileClient
from amazon_ads.config import Credentials
from amazon_ads.errors import AmazonAdsError, PlanDriftError
from amazon_ads.models import ApiModel
from amazon_ads.notes import API_NOTES
from amazon_ads.plans import ChangePlan
from amazon_ads.reports import PRESETS
from amazon_ads.sp.resource import SpResource
from amazon_ads.warehouse import Warehouse

ENTITIES = (
    "campaigns",
    "ad_groups",
    "keywords",
    "targets",
    "negative_keywords",
    "negative_targets",
    "campaign_negative_keywords",
    "campaign_negative_targets",
    "product_ads",
    "portfolios",
    "sb_campaigns",
    "sb_ad_groups",
    "sb_ads",
    "sb_keywords",
    "sb_negative_keywords",
)

INSTRUCTIONS = """\
Amazon Ads API access for one advertiser. Markets are country codes (US, UK, CA, AU, ...).

Reading: list_* tools, get_suggested_bids, get_change_history, run_report. Read
api_notes once per session: it lists API behavior the documentation does not state.

Changing anything is two steps and needs the user's explicit approval in between:
1. Call plan_update / plan_create / plan_archive / plan_restore_from_history. Show the
   user the returned table (every row) and wait for a clear yes.
2. Only then call apply_plan with the plan id and the fingerprint the plan tool
   returned. Report the result, including any failed items and the rollback plan id.
Never apply a plan the user has not seen and approved in this conversation. Undo a
change by showing and applying its rollback plan the same way.
"""


class AdsTools:
    """The tool implementations, independent of MCP so they can be tested directly."""

    def __init__(
        self,
        ads: AmazonAds | None = None,
        *,
        plan_dir: str | Path | None = None,
        db_path: str | Path | None = None,
        row_limit: int = 200,
    ) -> None:
        self._ads = ads
        self.plan_dir = Path(
            plan_dir
            or os.environ.get("AMAZON_ADS_MCP_PLAN_DIR")
            or "~/.local/state/amazon-ads/plans"
        ).expanduser()
        db = db_path or os.environ.get("AMAZON_ADS_MCP_DB")
        self.db_path = Path(db).expanduser() if db else None
        self.row_limit = row_limit

    @property
    def ads(self) -> AmazonAds:
        if self._ads is None:
            self._ads = AmazonAds(
                Credentials.from_env(env_file=os.environ.get("AMAZON_ADS_ENV_FILE") or None)
            )
        return self._ads

    def _client(self, market: str) -> ProfileClient:
        return self.ads.profile(market)

    def _resource(self, market: str, entity: str) -> SpResource[Any]:
        if entity not in ENTITIES:
            raise ValueError(f"entity must be one of {', '.join(ENTITIES)}")
        client = self._client(market)
        if entity == "portfolios":
            return client.portfolios
        if entity.startswith("sb_"):
            sb_resource: SpResource[Any] = getattr(client.sb, entity.removeprefix("sb_"))
            return sb_resource
        resource: SpResource[Any] = getattr(client.sp, entity)
        return resource

    def _page(self, rows: Sequence[Any], label: str = "rows") -> dict[str, Any]:
        records = [r.to_api() if isinstance(r, ApiModel) else r for r in rows]
        out: dict[str, Any] = {"count": len(records), label: records[: self.row_limit]}
        if len(records) > self.row_limit:
            out["truncated"] = (
                f"Showing {self.row_limit} of {len(records)}. Narrow the filters to see the rest."
            )
        return out

    # --- reads --------------------------------------------------------------------------

    def api_notes(self) -> str:
        """Amazon Ads API behavior the official docs do not state. Read once per session."""
        return API_NOTES

    def list_profiles(self) -> list[dict[str, Any]]:
        """Every advertising profile (account x marketplace) the credentials reach."""
        return [
            {
                "market": p.country_code,
                "profile_id": p.profile_id,
                "currency": p.currency_code,
                "timezone": p.timezone,
                "account_type": p.account_info.type if p.account_info else None,
                "account_sub_type": p.account_info.sub_type if p.account_info else None,
                "account_name": p.account_info.name if p.account_info else None,
            }
            for p in self.ads.profiles()
        ]

    def list_entities(
        self,
        market: str,
        entity: str,
        campaign_ids: list[str] | None = None,
        ad_group_ids: list[str] | None = None,
        ids: list[str] | None = None,
        states: list[str] | None = None,
        include_archived: bool = False,
        text_contains: str | None = None,
    ) -> dict[str, Any]:
        """List Sponsored Products or Sponsored Brands entities in one market.

        entity: campaigns, ad_groups, keywords, targets, negative_keywords,
        negative_targets, campaign_negative_keywords, campaign_negative_targets,
        product_ads, portfolios, sb_campaigns, sb_ad_groups, sb_ads, sb_keywords or
        sb_negative_keywords.
        States default to enabled and paused (enabled only for sb_negative_keywords);
        include_archived lists every state. Sponsored Brands keyword states are lower
        case. text_contains filters keywords, names and ASINs case-insensitively after
        fetching.
        """
        resource = self._resource(market, entity)
        kwargs: dict[str, Any] = {}
        if ids:
            kwargs["ids"] = ids
        if campaign_ids and entity != "portfolios":
            kwargs["campaign_ids"] = campaign_ids
        if ad_group_ids and entity not in {
            "portfolios",
            "campaigns",
            "sb_campaigns",
            "sb_ad_groups",
        }:
            kwargs["ad_group_ids"] = ad_group_ids
        if include_archived:
            kwargs["states"] = None
        elif states:
            kwargs["states"] = states
        rows = [r.to_api() for r in resource.list(**kwargs)]
        if text_contains:
            needle = text_contains.lower()
            rows = [r for r in rows if needle in json.dumps(r).lower()]
        return self._page(rows, entity)

    def get_suggested_bids(
        self,
        market: str,
        kind: str,
        campaign_ids: list[str] | None = None,
        ad_group_ids: list[str] | None = None,
        ids: list[str] | None = None,
        theme: str = "CONVERSION_OPPORTUNITIES",
    ) -> dict[str, Any]:
        """Amazon's suggested low/median/high bid next to the live bid, for existing
        enabled keywords or targets (kind: "keywords" or "targets")."""
        client = self._client(market)
        filters: dict[str, Any] = {"states": ["ENABLED"]}
        if campaign_ids:
            filters["campaign_ids"] = campaign_ids
        if ad_group_ids:
            filters["ad_group_ids"] = ad_group_ids
        if ids:
            filters["ids"] = ids
        rows: list[dict[str, Any]] = []
        if kind == "keywords":
            keywords = client.sp.keywords.list(**filters)
            found = client.bids.for_keywords(keywords, theme=theme)
            for kw in keywords:
                s = found.get(kw.keyword_id or "")
                rows.append(
                    {
                        "keyword_id": kw.keyword_id,
                        "keyword": kw.keyword_text,
                        "match": kw.match_type,
                        "bid": kw.bid,
                        "low": s.low if s else None,
                        "median": s.median if s else None,
                        "high": s.high if s else None,
                    }
                )
        elif kind == "targets":
            targets = client.sp.targets.list(**filters)
            result = client.bids.for_targets(targets, theme=theme)
            by_value = result.by_value()
            for t in targets:
                expr = expression_for_target(t)
                s = by_value.get(expr.key) if expr else None
                rows.append(
                    {
                        "target_id": t.target_id,
                        "expression": [e.to_api() for e in t.expression or []],
                        "bid": t.bid,
                        "low": s.low if s else None,
                        "median": s.median if s else None,
                        "high": s.high if s else None,
                        "note": None if expr else "Amazon gives no suggestion for this type",
                    }
                )
        else:
            raise ValueError('kind must be "keywords" or "targets"')
        return {"currency": client.currency, "theme": theme, **self._page(rows, "bids")}

    def get_suggested_bids_for_new(
        self,
        market: str,
        asins: list[str],
        keywords: list[dict[str, str]] | None = None,
        target_asins: list[str] | None = None,
        theme: str = "CONVERSION_OPPORTUNITIES",
    ) -> dict[str, Any]:
        """Suggested bids before an ad group exists: for the book(s) in ``asins``,
        price ``keywords`` ([{"text": ..., "match": "EXACT"|"PHRASE"|"BROAD"}]) and
        product targets on ``target_asins``."""
        client = self._client(market)
        exprs: list[Expression] = [
            expression_for_keyword(k["text"], k.get("match", "EXACT")) for k in keywords or []
        ]
        exprs += [Expression("PAT_ASIN", a) for a in target_asins or []]
        found = client.bids.for_new_ad_group(asins, exprs, theme=theme)
        return {
            "currency": client.currency,
            "theme": theme,
            "bids": [s.model_dump(exclude={"raw"}) for s in found],
        }

    def get_change_history(
        self,
        market: str,
        since: str | None = None,
        days: int = 7,
        entity_types: list[str] | None = None,
        changes: list[str] | None = None,
        entity_ids: list[str] | None = None,
        campaign_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """What changed, from what, to what and when (up to 90 days back).

        since: ISO date or datetime; otherwise the last ``days`` days. entity_types:
        CAMPAIGN, AD_GROUP, AD, KEYWORD, NEGATIVE_KEYWORD, PRODUCT_TARGETING. changes:
        BID_AMOUNT, STATUS, BUDGET_AMOUNT, CREATED, PLACEMENT_GROUP, ...
        """
        client = self._client(market)
        start = _parse_moment(since) if since else datetime.now(UTC) - timedelta(days=days)
        kwargs: dict[str, Any] = {"since": start}
        if entity_types:
            kwargs["entity_types"] = [t.upper() for t in entity_types]
        if changes:
            kwargs["changes"] = changes
        if entity_ids:
            kwargs["entity_ids"] = entity_ids
        if campaign_ids:
            kwargs["campaign_ids"] = campaign_ids
        rows = [
            {
                "when": datetime.fromtimestamp((e.timestamp or 0) / 1000, UTC).isoformat(
                    timespec="seconds"
                ),
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "change": e.change_type,
                "from": e.previous_value,
                "to": e.new_value,
                "context": e.metadata,
            }
            for e in client.history.events(**kwargs)
        ]
        return self._page(rows, "events")

    def run_report(
        self,
        market: str,
        preset: str,
        start: str,
        end: str,
        group_by: list[str] | None = None,
        save_to: str | None = None,
    ) -> dict[str, Any]:
        """Run a report (dates YYYY-MM-DD, any length) and return rows or totals.

        preset: sp_campaigns, sp_placement, sp_ad_groups, sp_targeting, sp_search_terms,
        sp_advertised_products, sp_purchased_products, sb_campaigns, sb_targeting,
        sb_search_terms, sb_purchased_products (which product a Sponsored Brands click
        actually sold). group_by (e.g. ["campaignName",
        "placementClassification"]) sums numeric columns per group instead of returning
        daily rows. save_to writes every row to a JSON file.
        """
        if preset not in PRESETS:
            raise ValueError(f"preset must be one of {', '.join(sorted(PRESETS))}")
        client = self._client(market)
        result = client.reports.run(preset, date.fromisoformat(start), date.fromisoformat(end))
        out: dict[str, Any] = {"report_ids": [c.report_id for c in result.chunks]}
        if save_to:
            path = Path(save_to).expanduser()
            path.write_text(json.dumps(result.rows, indent=1, default=str) + "\n")
            out["saved_to"] = str(path)
        rows = aggregate(result.rows, group_by) if group_by else result.rows
        return {**out, **self._page(rows, "rows")}

    def sync_reports(self, markets: list[str], reports: list[str] | None = None) -> dict[str, Any]:
        """Pull reports into the local warehouse (AMAZON_ADS_MCP_DB), re-pulling the last
        42 days that Amazon may still restate."""
        store = self._warehouse()
        chosen = reports or ["sp_placement", "sp_targeting", "sp_search_terms"]
        outcomes = []
        for market in markets:
            outcomes += [str(o) for o in store.sync(self._client(market), chosen)]
        return {"synced": outcomes, "coverage": store.coverage()}

    def query_reports(self, sql: str) -> dict[str, Any]:
        """Read-only SQL over the warehouse. Views v_<preset> (e.g. v_sp_placement) expose
        every report column plus country_code and date."""
        return self._page(self._warehouse().query(sql), "rows")

    def search_operations(self, text: str, api: str | None = None) -> list[dict[str, Any]]:
        """Find any of the ~1,200 operations in Amazon's published specs by keyword."""
        return [
            {
                "id": op.id,
                "method": op.method,
                "path": op.path,
                "summary": op.summary,
                "read_only": op.read_only,
            }
            for op in catalog.search(text, api=api, limit=40)
        ]

    def call_operation(
        self,
        market: str,
        operation: str,
        body: dict[str, Any] | list[Any] | None = None,
        path_params: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        """Call a read-only operation from Amazon's specs by id (see search_operations).
        Operations that can change data are refused here; use a plan tool instead."""
        op = catalog.get(operation)
        if not op.read_only:
            raise PermissionError(
                f"{op.id} ({op.method} {op.path}) can change data and cannot be called "
                "directly. Use plan_update, plan_create or plan_archive."
            )
        return self._client(market).call(op.id, body, path_params=path_params, query=query)

    # --- plans --------------------------------------------------------------------------

    def plan_update(
        self, market: str, entity: str, changes: list[dict[str, Any]], note: str | None = None
    ) -> dict[str, Any]:
        """Build (not apply) an update: each change is the entity id plus new values,
        e.g. {"keywordId": "123", "bid": 0.45} or {"targetId": "9", "state": "PAUSED"}.
        entity takes the same names as list_entities, including sb_campaigns (budget,
        state, and "bidding" for placement premiums: send the whole bidding object),
        sb_keywords (bid, state) and sb_negative_keywords (state)."""
        plan = self._resource(market, entity).plan_update(changes, note=note)
        return self._present(plan)

    def plan_create(
        self, market: str, entity: str, items: list[dict[str, Any]], note: str | None = None
    ) -> dict[str, Any]:
        """Build (not apply) a create, e.g. keywords [{"campaignId", "adGroupId",
        "keywordText", "matchType": "EXACT", "bid": 0.5, "state": "ENABLED"}], or
        sb_negative_keywords [{"campaignId", "adGroupId", "keywordText",
        "matchType": "negativeExact"}] (Sponsored Brands match types are camelCase).

        A Sponsored Brands campaign only serves once all three v4 pieces exist, created in
        order: sb_campaigns, then sb_ad_groups [{"campaignId", "name", "state"}], then
        sb_ads [{"adGroupId", "name", "state", "adFormat": "productCollection",
        "creative": {"brandName", "headline", "asins"},
        "landingPage": {"pageType": "DETAIL_PAGE", "asins"}}]. adFormat picks the create
        endpoint and defaults to productCollection, the console's Collections layout; one
        call cannot mix formats."""
        plan = self._resource(market, entity).plan_create(items, note=note)
        return self._present(plan)

    def plan_archive(
        self, market: str, entity: str, ids: list[str], note: str | None = None
    ) -> dict[str, Any]:
        """Build (not apply) an archive. Archiving cannot be undone; prefer pausing."""
        plan = self._resource(market, entity).plan_archive(ids, note=note)
        return self._present(plan)

    def plan_restore_from_history(
        self, market: str, since: str, campaign_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """Build (not apply) a plan restoring bids and states to what they were at
        ``since`` (ISO datetime, UTC if no offset), from Amazon's change history."""
        plan = self._client(market).history.rollback_plan(
            _parse_moment(since), campaign_ids=campaign_ids
        )
        return self._present(plan)

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        """A saved plan's table, fingerprint and status (applied or not)."""
        plan = self._load_plan(plan_id)
        return self._present(plan, save=False)

    def list_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent plans, newest first, with whether each was applied."""
        if not self.plan_dir.exists():
            return []
        files = sorted(
            (p for p in self.plan_dir.glob("plan-*.json") if ".result" not in p.name),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        out = []
        for path in files:
            plan = ChangePlan.load(path)
            out.append(
                {
                    "plan_id": plan.id,
                    "created_at": plan.created_at,
                    "market": plan.country_code,
                    "changes": len(plan),
                    "note": plan.note,
                    "reverses": plan.reverses,
                    "applied": self._result_path(plan.id).exists(),
                }
            )
        return out

    def apply_plan(
        self, plan_id: str, fingerprint: str, check_drift: bool = True
    ) -> dict[str, Any]:
        """Apply a saved plan. Only call after the user approved this exact plan.

        fingerprint must equal the one returned when the plan was built or fetched, which
        guarantees the plan being applied is the one that was shown. With check_drift
        (default) the plan is refused if any value changed since it was built.
        """
        plan = self._load_plan(plan_id)
        if fingerprint != plan.fingerprint:
            raise PermissionError(
                "Fingerprint does not match this plan. Show the user the current plan "
                "(get_plan) and use its fingerprint once they approve it."
            )
        if self._result_path(plan.id).exists():
            raise PermissionError(f"Plan {plan.id} was already applied")
        client = self.ads.profile(str(plan.profile_id))
        try:
            result = plan.apply(client, check_drift=check_drift)
        except PlanDriftError as exc:
            return {"applied": False, "reason": str(exc), "drifted": exc.drifted}
        rollback = result.rollback_plan()
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        rollback.save(self.plan_dir)
        changes = plan.grouped()
        errors = []
        for key, r in result.results.items():
            for e in r.errors:
                change = changes[key][e.index] if 0 <= e.index < len(changes[key]) else None
                errors.append(
                    {
                        "operation": key,
                        "id": change.id if change else None,
                        "label": change.label if change else None,
                        "sent": change.after if change else None,
                        "error_type": e.error_type,
                        "code": e.code,
                        "message": e.message,
                        "allowed_range": [e.lower_limit, e.upper_limit]
                        if e.lower_limit is not None or e.upper_limit is not None
                        else None,
                        "what_to_do": e.hint,
                    }
                )
        created = {key: r.ids for key, r in result.results.items() if key.startswith("create")}
        record = {
            "plan_id": plan.id,
            "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "summary": result.summary(),
            "ok": result.ok,
            "errors": errors,
            "created_ids": created,
            "rollback_plan_id": rollback.id if rollback.changes else None,
            "rollback_warnings": rollback.warnings,
        }
        self._result_path(plan.id).write_text(json.dumps(record, indent=2) + "\n")
        return record

    # --- helpers ------------------------------------------------------------------------

    def _present(self, plan: ChangePlan, *, save: bool = True) -> dict[str, Any]:
        if save:
            self.plan_dir.mkdir(parents=True, exist_ok=True)
            plan.save(self.plan_dir)
        return {
            "plan_id": plan.id,
            "fingerprint": plan.fingerprint,
            "changes": len(plan),
            "applied": self._result_path(plan.id).exists(),
            "table": plan.to_markdown(),
            "missing": plan.missing,
            "warnings": plan.warnings,
            "next_step": "Show the table to the user. Apply only after they approve, with "
            "apply_plan(plan_id, fingerprint).",
        }

    def _load_plan(self, plan_id: str) -> ChangePlan:
        if not plan_id.isalnum():
            raise ValueError("plan_id must be alphanumeric")
        path = self.plan_dir / f"plan-{plan_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No plan {plan_id} in {self.plan_dir}")
        return ChangePlan.load(path)

    def _result_path(self, plan_id: str) -> Path:
        return self.plan_dir / f"plan-{plan_id}.result.json"

    def _warehouse(self) -> Warehouse:
        if self.db_path is None:
            raise RuntimeError("Set AMAZON_ADS_MCP_DB to a SQLite path to use the warehouse")
        return Warehouse(self.db_path)


def aggregate(rows: Sequence[dict[str, Any]], group_by: Sequence[str]) -> list[dict[str, Any]]:
    """Sum numeric columns per group; derive CTR, CPC and ACOS where the inputs exist."""
    totals: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(dict)
    for row in rows:
        key = tuple(row.get(c) for c in group_by)
        bucket = totals[key]
        for col, value in row.items():
            if col in group_by or col == "date":
                continue
            if (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and not col.endswith(("Id", "Bid", "Amount", "Share"))
            ):
                bucket[col] = bucket.get(col, 0) + value
    out = []
    for key, sums in totals.items():
        record = dict(zip(group_by, key, strict=True))
        record.update({k: round(v, 4) if isinstance(v, float) else v for k, v in sums.items()})
        clicks, impressions, cost = sums.get("clicks"), sums.get("impressions"), sums.get("cost")
        if impressions:
            record["ctr"] = round((clicks or 0) / impressions, 4)
        if clicks:
            record["cpc"] = round((cost or 0) / clicks, 4)
        sales = sums.get("sales14d")
        if sales:
            record["acos14d"] = round((cost or 0) / sales, 4)
        out.append(record)
    out.sort(key=lambda r: -(r.get("cost") or 0))
    return out


def _parse_moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


READ_TOOLS: tuple[str, ...] = (
    "api_notes",
    "list_profiles",
    "list_entities",
    "get_suggested_bids",
    "get_suggested_bids_for_new",
    "get_change_history",
    "run_report",
    "search_operations",
    "call_operation",
    "get_plan",
    "list_plans",
)
PLAN_TOOLS: tuple[str, ...] = (
    "plan_update",
    "plan_create",
    "plan_archive",
    "plan_restore_from_history",
)
WAREHOUSE_TOOLS: tuple[str, ...] = ("sync_reports", "query_reports")


def build_server(tools: AdsTools | None = None, *, read_only: bool | None = None) -> Any:
    """Create the MCP server. ``read_only`` defaults to ``AMAZON_ADS_MCP_READ_ONLY``."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError("The MCP server needs: pip install 'amazon-ads-py[mcp]'") from exc

    tools = tools or AdsTools()
    if read_only is None:
        read_only = os.environ.get("AMAZON_ADS_MCP_READ_ONLY", "").lower() in {"1", "true", "yes"}

    server = MCPServer(
        name="amazon-ads",
        instructions=INSTRUCTIONS if not read_only else INSTRUCTIONS.split("Changing")[0],
    )

    def register(name: str, *, read: bool, destructive: bool = False) -> None:
        fn = _surface_errors(getattr(tools, name), ToolError)
        server.tool(
            name=name,
            description=(fn.__doc__ or "").strip(),
            annotations=ToolAnnotations(
                read_only_hint=read, destructive_hint=destructive, open_world_hint=True
            ),
        )(fn)

    for name in READ_TOOLS:
        register(name, read=True)
    if tools.db_path is not None:
        register("query_reports", read=True)
        register("sync_reports", read=False)
    if not read_only:
        for name in PLAN_TOOLS:
            register(name, read=False)
        register("apply_plan", read=False, destructive=True)

    @server.resource("amazon-ads://notes", name="api_notes", mime_type="text/markdown")
    def notes() -> str:
        return API_NOTES

    return server


# Failures the caller can act on. Their message goes back to the model as the tool
# result instead of a generic "Error executing tool" with the reason lost.
EXPECTED_ERRORS = (
    AmazonAdsError,
    PermissionError,
    ValueError,
    KeyError,
    FileNotFoundError,
    RuntimeError,
)


def _surface_errors(fn: Callable[..., Any], tool_error: type[Exception]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EXPECTED_ERRORS as exc:
            raise tool_error(f"{type(exc).__name__}: {exc}") from exc

    return wrapper


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
