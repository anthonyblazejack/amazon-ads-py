# amazon-ads-py

Public Python client, CLI (`adsctl`) and MCP server (`amazon-ads-mcp`) for the Amazon Ads API.

## Commands

```bash
uv sync --all-extras
uv run pytest                                    # offline, mocked with respx
uv run ruff check src tests scripts && uv run ruff format src tests scripts
uv run mypy                                      # strict
uv run python scripts/generate_catalog.py --docs-only   # after editing notes.py
uv run mkdocs build --strict
```

## Rules

- **Docs move with the code.** Any change to public behavior updates the matching page in
  `docs/`, the README when the quick start changes, and `CHANGELOG.md` (Unreleased), in
  the same commit.
- **This repository is public.** Never commit credentials, real profile/campaign/keyword
  ids, ASINs, campaign names, bids or report rows from any account. Tests and docs use
  invented values.
- **Record live API behavior in `src/amazon_ads/notes.py`** when the API does something
  its specs do not say, then regenerate `docs/guide/quirks.md`. The MCP server serves the
  same text to Claude.
- **No policy.** The library does not decide bids or budgets. It moves data and makes
  writes safe.
- **Writes the MCP server makes go through plans** and need the plan's fingerprint.
  `call_operation` must keep refusing operations that can change data.
- Do not retry writes on 5xx or timeouts (a create may have gone through). 429 is
  always safe to retry.
- `src/amazon_ads/_generated/catalog.json` and `docs/reference/coverage.md` are generated;
  change `scripts/generate_catalog.py` instead of editing them.
- No em dashes anywhere. Comments must make sense without any outside context.
