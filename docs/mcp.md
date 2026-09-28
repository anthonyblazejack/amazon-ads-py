# MCP server

`amazon-ads-mcp` exposes the library to Claude and any other MCP client over stdio.

```bash
pip install "amazon-ads-py[mcp]"
```

## Setup

**Claude Code**

```bash
claude mcp add amazon-ads --env AMAZON_ADS_ENV_FILE=/path/to/.env -- amazon-ads-mcp
```

Read-only:

```bash
claude mcp add amazon-ads-ro --env AMAZON_ADS_ENV_FILE=/path/to/.env \
  --env AMAZON_ADS_MCP_READ_ONLY=1 -- amazon-ads-mcp
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "amazon-ads": {
      "command": "amazon-ads-mcp",
      "env": {
        "AMAZON_ADS_ENV_FILE": "/path/to/.env",
        "AMAZON_ADS_MCP_DB": "/path/to/ads.sqlite"
      }
    }
  }
}
```

If `amazon-ads-mcp` is not on the client's `PATH`, give the full path (for example
`/path/to/venv/bin/amazon-ads-mcp`) or use
`"command": "uvx", "args": ["--from", "amazon-ads-py[mcp]", "amazon-ads-mcp"]`.

## Configuration

| Variable | Meaning |
|---|---|
| `AMAZON_ADS_ENV_FILE` | Dotenv file with the `AMAZON_ADS_*` credentials (or set them directly) |
| `AMAZON_ADS_MCP_READ_ONLY` | `1` registers no write tools at all |
| `AMAZON_ADS_MCP_PLAN_DIR` | Where plans, results and rollback plans are kept. Default `~/.local/state/amazon-ads/plans` |
| `AMAZON_ADS_MCP_DB` | SQLite [warehouse](guide/warehouse.md) path; enables `sync_reports` and `query_reports` |

## Tools

**Reading**

| Tool | What |
|---|---|
| `api_notes` | Amazon API behavior the docs do not state. The server's instructions tell Claude to read it once per session. |
| `list_profiles` | Every marketplace the credentials reach |
| `list_entities` | Campaigns, ad groups, keywords, targets, negatives, ads, portfolios, and Sponsored Brands campaigns, keywords and negative keywords (`sb_*`), with filters and a text search |
| `get_suggested_bids` | Suggested low/median/high next to the live bid for existing keywords or targets |
| `get_suggested_bids_for_new` | Suggested bids for keywords and ASIN targets before an ad group exists |
| `get_change_history` | What changed, from what, to what, when |
| `run_report` | Any preset over any range, as rows or grouped totals with CTR, CPC and ACOS |
| `search_operations` | Search the ~1,200 operations in Amazon's specs |
| `call_operation` | Call any read-only operation by id. Operations that can write are refused. |
| `get_plan`, `list_plans` | Saved plans and whether each was applied |
| `query_reports`, `sync_reports` | Warehouse SQL and sync (with `AMAZON_ADS_MCP_DB`) |

**Changing** (not registered in read-only mode)

| Tool | What |
|---|---|
| `plan_update` | Plan bid, state or other updates |
| `plan_create` | Plan new keywords, targets, negatives, ads, campaigns, ad groups |
| `plan_archive` | Plan archiving (permanent) |
| `plan_restore_from_history` | Plan restoring bids and states to a past moment |
| `apply_plan` | Apply a plan by id **and fingerprint** |

Results are capped at 200 rows per call with a note when truncated, so a large account
does not flood the conversation. `run_report` can save every row to a file.

## How writes stay under your control

1. Claude calls a `plan_*` tool. The plan is saved, and Claude gets back its table
   (every change, before and after) and a fingerprint. Nothing has been sent to Amazon.
2. The server's instructions tell Claude to show you the table and wait for your
   approval.
3. `apply_plan` needs the plan id **and** the fingerprint. The fingerprint is a hash of
   the plan's content, so only the exact plan that was shown can be applied. A plan
   can be applied only once.
4. By default the plan is checked for drift first. If any value changed since the plan
   was built, nothing is sent and the drifted fields are reported.
5. The result (per-item successes and failures) and a rollback plan are saved. Undoing
   is the same two steps with the rollback plan.

Claude cannot bypass this through `call_operation`, which refuses any operation that
can change data. In read-only mode the write tools do not exist.

Claude Code also asks you before each tool call unless you have allowed the tool.
Keeping `apply_plan` out of your allow list gives a second, independent approval step.

## Errors

`apply_plan` returns every item Amazon rejected with the entity id and label, what was
sent, Amazon's error type, reason and message, the allowed range for bid errors, and a
plain `what_to_do`. Items Amazon throttled mid-batch have already been retried.

When a tool refuses or Amazon rejects something, the reason goes back to Claude as the
tool result (for example `PermissionError: Fingerprint does not match this plan`), so
it can explain or correct course.
