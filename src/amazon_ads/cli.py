"""``adsctl``: the command line interface.

Every read command prints a table by default and JSON with ``--json`` (or ``--format
csv``/``jsonl``), so the output can feed another program. Writes go through change plans:
``adsctl plan update`` builds and saves a plan, ``adsctl plan apply`` shows it, asks, sends
it and saves the rollback plan next to it.
"""

from __future__ import annotations

import csv
import getpass
import io
import json
import os
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import click
except ImportError:  # pragma: no cover - exercised only without the extra installed
    sys.stderr.write("adsctl needs the CLI extra: pip install 'amazon-ads-py[cli]'\n")
    raise SystemExit(1) from None

from amazon_ads import catalog
from amazon_ads.auth import authorization_url, exchange_code
from amazon_ads.client import AmazonAds, ProfileClient
from amazon_ads.config import Credentials
from amazon_ads.errors import AmazonAdsError, PartialFailureError
from amazon_ads.models import ApiModel
from amazon_ads.plans import ChangePlan
from amazon_ads.reports import PRESETS
from amazon_ads.warehouse import Warehouse

ENTITIES = {
    "campaigns": "campaigns",
    "ad-groups": "ad_groups",
    "keywords": "keywords",
    "targets": "targets",
    "negative-keywords": "negative_keywords",
    "negative-targets": "negative_targets",
    "campaign-negative-keywords": "campaign_negative_keywords",
    "campaign-negative-targets": "campaign_negative_targets",
    "product-ads": "product_ads",
    "portfolios": "portfolios",
}

# Columns shown in table output; --json always prints every field.
TABLE_COLUMNS = {
    "campaigns": ["campaignId", "name", "state", "targetingType", "budget", "dynamicBidding"],
    "ad_groups": ["adGroupId", "campaignId", "name", "state", "defaultBid"],
    "keywords": ["keywordId", "adGroupId", "keywordText", "matchType", "state", "bid"],
    "targets": ["targetId", "adGroupId", "expression", "state", "bid"],
    "negative_keywords": ["keywordId", "adGroupId", "keywordText", "matchType", "state"],
    "negative_targets": ["targetId", "adGroupId", "expression", "state"],
    "campaign_negative_keywords": ["keywordId", "campaignId", "keywordText", "matchType", "state"],
    "campaign_negative_targets": ["targetId", "campaignId", "expression", "state"],
    "product_ads": ["adId", "adGroupId", "asin", "sku", "state"],
    "portfolios": ["portfolioId", "name", "state", "budget"],
}


class Context:
    def __init__(self, env_file: str | None, sandbox: bool, fmt: str) -> None:
        self.env_file = env_file
        self.sandbox = sandbox
        self.format = fmt
        self._ads: AmazonAds | None = None

    @property
    def ads(self) -> AmazonAds:
        if self._ads is None:
            self._ads = AmazonAds(
                Credentials.from_env(env_file=self.env_file), sandbox=self.sandbox
            )
        return self._ads

    def profile(self, market: str) -> ProfileClient:
        return self.ads.profile(market)


pass_ctx = click.make_pass_decorator(Context)


def _default_env_file() -> str | None:
    explicit = os.environ.get("AMAZON_ADS_ENV_FILE")
    if explicit:
        return explicit
    return ".env" if Path(".env").is_file() else None


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False),
    default=_default_env_file,
    help="Dotenv file with AMAZON_ADS_* credentials. Default: $AMAZON_ADS_ENV_FILE or ./.env.",
)
@click.option("--sandbox", is_flag=True, help="Use Amazon's sandbox host.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json", "jsonl", "csv"]),
    default="table",
    help="Output format for read commands.",
)
@click.option("--json", "as_json", is_flag=True, help="Shorthand for --format json.")
@click.version_option(package_name="amazon-ads-py")
@click.pass_context
def cli(ctx: click.Context, env_file: str | None, sandbox: bool, fmt: str, as_json: bool) -> None:
    """Amazon Ads API from the command line."""
    ctx.obj = Context(env_file, sandbox, "json" if as_json else fmt)


def main() -> None:
    try:
        cli(standalone_mode=True)
    except AmazonAdsError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# --- output ----------------------------------------------------------------------------


def emit(ctx: Context, rows: Sequence[Any], columns: Sequence[str] | None = None) -> None:
    records = [_plain(r) for r in rows]
    if ctx.format == "json":
        click.echo(json.dumps(records, indent=2, default=str))
    elif ctx.format == "jsonl":
        for r in records:
            click.echo(json.dumps(r, default=str))
    elif ctx.format == "csv":
        cols = list(columns or _all_keys(records))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k: _cell(r.get(k)) for k in cols})
        click.echo(buffer.getvalue().rstrip("\n"))
    else:
        click.echo(table(records, columns))


def table(records: Sequence[dict[str, Any]], columns: Sequence[str] | None = None) -> str:
    if not records:
        return "(no rows)"
    cols = list(columns or _all_keys(records))
    cells = [[_cell(r.get(c)) for c in cols] for r in records]
    widths = [min(max(len(c), *(len(row[i]) for row in cells)), 60) for i, c in enumerate(cols)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True))]
    lines.append("  ".join("-" * w for w in widths))
    for row in cells:
        lines.append("  ".join(v[:w].ljust(w) for v, w in zip(row, widths, strict=True)))
    lines.append(f"({len(records)} rows)")
    return "\n".join(lines)


def _plain(value: Any) -> dict[str, Any]:
    if isinstance(value, ApiModel):
        return value.to_api()
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"value": value}


def _all_keys(records: Iterable[dict[str, Any]]) -> list[str]:
    keys: dict[str, None] = {}
    for r in records:
        keys.update(dict.fromkeys(r))
    return list(keys)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def _parse_day(value: str) -> date:
    if value.startswith("-") and value[1:].isdigit():
        return date.today() - timedelta(days=int(value[1:]))
    return date.fromisoformat(value)


# --- auth ------------------------------------------------------------------------------


@cli.group()
def auth() -> None:
    """Consent, token exchange and credential checks."""


@auth.command("url")
@click.option("--client-id", envvar="AMAZON_ADS_CLIENT_ID", required=True)
@click.option("--redirect-uri", envvar="AMAZON_ADS_REDIRECT_URI", required=True)
def auth_url(client_id: str, redirect_uri: str) -> None:
    """Print the consent URL to open while signed in to the ads account."""
    click.echo(authorization_url(client_id, redirect_uri))


@auth.command("exchange")
@click.option("--client-id", envvar="AMAZON_ADS_CLIENT_ID", required=True)
@click.option("--client-secret", envvar="AMAZON_ADS_CLIENT_SECRET", required=True)
@click.option("--redirect-uri", envvar="AMAZON_ADS_REDIRECT_URI", required=True)
@click.option(
    "--write-env",
    type=click.Path(dir_okay=False),
    help="Write AMAZON_ADS_REFRESH_TOKEN and AMAZON_ADS_CONSENT_DATE into this dotenv file "
    "instead of printing the token.",
)
def auth_exchange(
    client_id: str, client_secret: str, redirect_uri: str, write_env: str | None
) -> None:
    """Trade the code from the consent redirect for a refresh token.

    Paste either the code or the whole redirect URL; input is not echoed, so the code
    never lands in terminal scrollback.
    """
    raw = getpass.getpass("Code or redirect URL: ").strip()
    code = raw
    if "code=" in raw:
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(raw).query).get("code", [""])[0]
    tokens = exchange_code(
        code, client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
    )
    refresh = str(tokens["refresh_token"])
    if write_env:
        _write_env(
            Path(write_env),
            {
                "AMAZON_ADS_REFRESH_TOKEN": refresh,
                "AMAZON_ADS_CONSENT_DATE": date.today().isoformat(),
            },
        )
        click.echo(f"Refresh token written to {write_env}")
    else:
        click.echo(refresh)


def _write_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    lines += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(lines) + "\n")


@auth.command("check")
@pass_ctx
def auth_check(ctx: Context) -> None:
    """Mint an access token and report when the refresh token expires."""
    ctx.ads.tokens.token()
    click.echo("Access token issued: credentials work.")
    expires = ctx.ads.credentials.refresh_token_expires
    if expires is None:
        click.echo("Set AMAZON_ADS_CONSENT_DATE to track the refresh token's 365-day expiry.")
        return
    days = (expires - date.today()).days
    click.echo(f"Refresh token expires {expires} ({days} days).")
    if days <= 30:
        click.echo("Repeat the consent soon: `adsctl auth url`.", err=True)


# --- reads -----------------------------------------------------------------------------


@cli.command()
@pass_ctx
def profiles(ctx: Context) -> None:
    """List every profile the credentials can reach."""
    rows = [
        {
            "country": p.country_code,
            "profileId": p.profile_id,
            "currency": p.currency_code,
            "type": p.account_info.type if p.account_info else None,
            "subType": p.account_info.sub_type if p.account_info else None,
            "name": p.account_info.name if p.account_info else None,
        }
        for p in ctx.ads.profiles()
    ]
    emit(ctx, rows)


@cli.command("list")
@click.argument("entity", type=click.Choice(sorted(ENTITIES)))
@click.option("-m", "--market", required=True, help="Country code (US, UK, ...) or profile id.")
@click.option("--campaign-id", "campaign_ids", multiple=True)
@click.option("--ad-group-id", "ad_group_ids", multiple=True)
@click.option("--id", "ids", multiple=True, help="Entity id (repeatable).")
@click.option("--state", "states", multiple=True, help="ENABLED, PAUSED, ARCHIVED (repeatable).")
@click.option("--all-states", is_flag=True, help="Include archived entities.")
@pass_ctx
def list_entities(
    ctx: Context,
    entity: str,
    market: str,
    campaign_ids: tuple[str, ...],
    ad_group_ids: tuple[str, ...],
    ids: tuple[str, ...],
    states: tuple[str, ...],
    all_states: bool,
) -> None:
    """List campaigns, ad groups, keywords, targets, negatives, ads or portfolios."""
    client = ctx.profile(market)
    attr = ENTITIES[entity]
    resource = client.portfolios if attr == "portfolios" else getattr(client.sp, attr)
    kwargs: dict[str, Any] = {}
    if ids:
        kwargs["ids"] = ids
    if campaign_ids and attr != "portfolios":
        kwargs["campaign_ids"] = campaign_ids
    if ad_group_ids and attr not in {"portfolios", "campaigns"}:
        kwargs["ad_group_ids"] = ad_group_ids
    if all_states:
        kwargs["states"] = None
    elif states:
        kwargs["states"] = states
    emit(ctx, resource.list(**kwargs), TABLE_COLUMNS.get(attr))


@cli.command()
@click.argument("kind", type=click.Choice(["keywords", "targets"]))
@click.option("-m", "--market", required=True)
@click.option("--campaign-id", "campaign_ids", multiple=True)
@click.option("--ad-group-id", "ad_group_ids", multiple=True)
@click.option("--theme", default="CONVERSION_OPPORTUNITIES", show_default=True)
@pass_ctx
def bids(
    ctx: Context,
    kind: str,
    market: str,
    campaign_ids: tuple[str, ...],
    ad_group_ids: tuple[str, ...],
    theme: str,
) -> None:
    """Amazon's suggested bids next to the live bid for existing keywords or targets."""
    client = ctx.profile(market)
    filters: dict[str, Any] = {"states": ["ENABLED"]}
    if campaign_ids:
        filters["campaign_ids"] = campaign_ids
    if ad_group_ids:
        filters["ad_group_ids"] = ad_group_ids
    rows = []
    if kind == "keywords":
        keywords = client.sp.keywords.list(**filters)
        suggestions = client.bids.for_keywords(keywords, theme=theme)
        for kw in keywords:
            s = suggestions.get(kw.keyword_id or "")
            rows.append(
                {
                    "keywordId": kw.keyword_id,
                    "keyword": kw.keyword_text,
                    "match": kw.match_type,
                    "bid": kw.bid,
                    "low": s.low if s else None,
                    "median": s.median if s else None,
                    "high": s.high if s else None,
                }
            )
    else:
        targets = client.sp.targets.list(**filters)
        result = client.bids.for_targets(targets, theme=theme)
        by_value = result.by_value()
        from amazon_ads.bids import expression_for_target

        for target in targets:
            expr = expression_for_target(target)
            s = by_value.get(expr.key) if expr else None
            rows.append(
                {
                    "targetId": target.target_id,
                    "expression": _cell([e.to_api() for e in target.expression or []]),
                    "bid": target.bid,
                    "low": s.low if s else None,
                    "median": s.median if s else None,
                    "high": s.high if s else None,
                    "note": "" if expr else "no recommendation for this target type",
                }
            )
    emit(ctx, rows)


@cli.command()
@click.option("-m", "--market", required=True)
@click.option("--since", default="-7", show_default=True, help="YYYY-MM-DD or -N days.")
@click.option("--until", default=None, help="YYYY-MM-DD. Default: now.")
@click.option("--entity-type", "entity_types", multiple=True, help="KEYWORD, CAMPAIGN, ...")
@click.option("--change", "changes", multiple=True, help="BID_AMOUNT, STATUS, ...")
@click.option("--id", "ids", multiple=True, help="Entity id (repeatable).")
@click.option("--campaign-id", "campaign_ids", multiple=True)
@pass_ctx
def history(
    ctx: Context,
    market: str,
    since: str,
    until: str | None,
    entity_types: tuple[str, ...],
    changes: tuple[str, ...],
    ids: tuple[str, ...],
    campaign_ids: tuple[str, ...],
) -> None:
    """Change history: what changed, from what, to what, when (last 90 days)."""
    client = ctx.profile(market)
    kwargs: dict[str, Any] = {"since": _parse_day(since)}
    if until:
        kwargs["until"] = _parse_day(until)
    if entity_types:
        kwargs["entity_types"] = [t.upper() for t in entity_types]
    if changes:
        kwargs["changes"] = changes
    if ids:
        kwargs["entity_ids"] = list(ids)
    if campaign_ids:
        kwargs["campaign_ids"] = list(campaign_ids)
    rows = [
        {
            "when": datetime.fromtimestamp((e.timestamp or 0) / 1000, UTC).isoformat(
                timespec="seconds"
            ),
            "entityType": e.entity_type,
            "entityId": e.entity_id,
            "change": e.change_type,
            "from": e.previous_value,
            "to": e.new_value,
            "what": (e.metadata or {}).get("keyword")
            or (e.metadata or {}).get("targetingExpression"),
        }
        for e in client.history.events(**kwargs)
    ]
    emit(ctx, rows)


@cli.command()
@click.argument("preset", type=click.Choice(sorted(PRESETS)))
@click.option("-m", "--market", required=True)
@click.option("--start", default="-7", show_default=True, help="YYYY-MM-DD or -N days.")
@click.option("--end", default="-1", show_default=True, help="YYYY-MM-DD or -N days.")
@click.option("--summary", is_flag=True, help="Totals per chunk instead of daily rows.")
@click.option("--out", type=click.Path(dir_okay=False), help="Write rows to this JSON file.")
@pass_ctx
def report(
    ctx: Context, preset: str, market: str, start: str, end: str, summary: bool, out: str | None
) -> None:
    """Run a report and print (or save) its rows. Ranges over 31 days are split."""
    client = ctx.profile(market)
    result = client.reports.run(
        preset,
        _parse_day(start),
        _parse_day(end),
        time_unit="SUMMARY" if summary else "DAILY",
    )
    if out:
        Path(out).write_text(json.dumps(result.rows, indent=1, default=str) + "\n")
        click.echo(f"{len(result.rows)} rows written to {out}", err=True)
        return
    emit(ctx, result.rows)


@cli.command()
@click.option("--db", default="amazon-ads.sqlite", show_default=True, type=click.Path())
@click.option("-m", "--market", "markets", multiple=True, required=True)
@click.option(
    "--report",
    "reports",
    multiple=True,
    type=click.Choice(sorted(PRESETS)),
    help="Default: sp_placement, sp_targeting, sp_search_terms.",
)
@click.option("--restate-days", default=42, show_default=True)
@pass_ctx
def sync(
    ctx: Context, db: str, markets: tuple[str, ...], reports: tuple[str, ...], restate_days: int
) -> None:
    """Pull reports into a local SQLite store that outlives Amazon's retention."""
    chosen = reports or ("sp_placement", "sp_targeting", "sp_search_terms")
    with Warehouse(db) as store:
        for market in markets:
            for outcome in store.sync(ctx.profile(market), chosen, restate_days=restate_days):
                click.echo(str(outcome))
        emit(ctx, store.coverage())


# --- plans -----------------------------------------------------------------------------


@cli.group()
def plan() -> None:
    """Build, review, apply and roll back change plans."""


@plan.command("update")
@click.argument("entity", type=click.Choice(sorted(ENTITIES)))
@click.argument("changes_file", type=click.Path(exists=True, dir_okay=False))
@click.option("-m", "--market", required=True)
@click.option("--note", help="Why this change is being made; saved in the plan.")
@click.option("--out", type=click.Path(), default=".", show_default=True, help="File or dir.")
@pass_ctx
def plan_update(
    ctx: Context, entity: str, changes_file: str, market: str, note: str | None, out: str
) -> None:
    """Build an update plan from a JSON list or CSV of changes (id column plus fields).

    Example CSV for keyword bids:  keywordId,bid
    """
    client = ctx.profile(market)
    attr = ENTITIES[entity]
    resource = client.portfolios if attr == "portfolios" else getattr(client.sp, attr)
    items = _read_changes(Path(changes_file))
    built = resource.plan_update(items, note=note)
    path = built.save(out)
    click.echo(built.to_markdown())
    click.echo(f"\nSaved {path}. Apply with: adsctl plan apply {path}")


def _read_changes(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        return [{k: _coerce_csv(k, v) for k, v in row.items() if v != ""} for row in rows]
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise click.BadParameter("JSON changes must be a list of objects")
    return data


NUMERIC_FIELDS = {"bid", "defaultBid"}


def _coerce_csv(key: str, value: str) -> Any:
    """CSV cells are text; bids must reach Amazon as numbers, ids stay strings."""
    if key in NUMERIC_FIELDS:
        try:
            return float(value)
        except ValueError:
            raise click.BadParameter(f"{key} must be a number, got {value!r}") from None
    if key == "state":
        return value.upper()
    return value


@plan.command("show")
@click.argument("plan_file", type=click.Path(exists=True, dir_okay=False))
def plan_show(plan_file: str) -> None:
    """Print a saved plan."""
    click.echo(ChangePlan.load(plan_file).to_markdown())


@plan.command("apply")
@click.argument("plan_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--check-drift/--no-check-drift", default=True, show_default=True)
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@pass_ctx
def plan_apply(ctx: Context, plan_file: str, check_drift: bool, yes: bool) -> None:
    """Apply a saved plan and save its rollback plan next to it."""
    loaded = ChangePlan.load(plan_file)
    click.echo(loaded.to_markdown())
    if not loaded.changes:
        click.echo("Nothing to apply.")
        return
    if not yes:
        click.confirm(f"\nApply {len(loaded)} change(s)?", abort=True)
    client = ctx.ads.profile(str(loaded.profile_id))
    result = loaded.apply(client, check_drift=check_drift)
    rollback = result.rollback_plan()
    rollback_path = Path(plan_file).with_name(f"plan-{loaded.id}.rollback.json")
    rollback.save(rollback_path)
    click.echo(result.summary())
    click.echo(f"Rollback plan: {rollback_path}")
    try:
        result.raise_for_errors()
    except PartialFailureError as exc:
        for error in exc.result.errors:
            click.echo(
                f"  item {error.index}: {error.code}: {error.message} ({error.hint})", err=True
            )
        sys.exit(2)


@plan.command("from-history")
@click.option("-m", "--market", required=True)
@click.option("--since", required=True, help="Restore to this moment: YYYY-MM-DD[THH:MM].")
@click.option("--campaign-id", "campaign_ids", multiple=True)
@click.option("--out", type=click.Path(), default=".", show_default=True)
@pass_ctx
def plan_from_history(
    ctx: Context, market: str, since: str, campaign_ids: tuple[str, ...], out: str
) -> None:
    """Build a plan restoring bids and states to a past moment from change history."""
    moment = datetime.fromisoformat(since)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    built = ctx.profile(market).history.rollback_plan(
        moment, campaign_ids=list(campaign_ids) or None
    )
    path = built.save(out)
    click.echo(built.to_markdown())
    click.echo(f"\nSaved {path}. Apply with: adsctl plan apply {path}")


# --- any operation ---------------------------------------------------------------------


@cli.group()
def ops() -> None:
    """Find and call any of the operations in Amazon's published specs."""


@ops.command("search")
@click.argument("text", nargs=-1, required=True)
@click.option("--api", help="Limit to one API slug, e.g. sponsored-brands-v4.")
@pass_ctx
def ops_search(ctx: Context, text: tuple[str, ...], api: str | None) -> None:
    """Search operations by words in their id, path or summary."""
    found = catalog.search(" ".join(text), api=api, limit=100)
    emit(
        ctx,
        [
            {"id": op.id, "method": op.method, "path": op.path, "summary": op.summary}
            for op in found
        ],
    )


@ops.command("show")
@click.argument("operation")
def ops_show(operation: str) -> None:
    """Show one operation's method, path, parameters and media types."""
    op = catalog.get(operation)
    click.echo(json.dumps(op.__dict__, indent=2, default=list))


@ops.command("call")
@click.argument("operation")
@click.option("-m", "--market", required=True)
@click.option("--body", help="JSON body, or @file.json.")
@click.option("-p", "--path-param", "path_params", multiple=True, help="name=value")
@click.option("-q", "--query", "query", multiple=True, help="name=value")
@click.option("--yes", is_flag=True, help="Do not confirm operations that can change data.")
@pass_ctx
def ops_call(
    ctx: Context,
    operation: str,
    market: str,
    body: str | None,
    path_params: tuple[str, ...],
    query: tuple[str, ...],
    yes: bool,
) -> None:
    """Call any operation by id and print the JSON response."""
    op = catalog.get(operation)
    if not op.read_only and not yes:
        click.confirm(f"{op.id} ({op.method} {op.path}) can change data. Send it?", abort=True)
    payload = None
    if body:
        text = Path(body[1:]).read_text() if body.startswith("@") else body
        payload = json.loads(text)
    response = ctx.profile(market).call(
        op.id,
        payload,
        path_params=dict(p.split("=", 1) for p in path_params),
        query=dict(q.split("=", 1) for q in query),
    )
    click.echo(json.dumps(response, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
