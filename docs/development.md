# Development

```bash
git clone https://github.com/anthonyblazejack/amazon-ads-py
cd amazon-ads-py
uv sync --all-extras
uv run pytest
```

## Checks

CI runs all of these on Python 3.11 to 3.14. Run them before pushing:

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy
uv run pytest --cov=amazon_ads
uv run python scripts/generate_catalog.py --docs-only && git diff --exit-code docs
uv run mkdocs build --strict
```

## Tests

Tests mock Amazon with [respx](https://lundberg.github.io/respx/) and run offline. Every
test should name the bug it catches: a retry that could duplicate a write, a 207 error
paired with the wrong input, a report chunk that skips a day. Ids in tests are invented.
Never paste real profile ids, ASINs, campaign names or bids from an account into this
repository.

Live tests hit the real API and are opt-in:

```bash
AMAZON_ADS_ENV_FILE=/path/to/.env uv run pytest -m live
```

They only read.

## Regenerating from Amazon's specs

```bash
uv run python scripts/fetch_specs.py        # downloads ~220 specs into specs/ (gitignored)
uv run python scripts/generate_catalog.py   # rebuilds the catalog and the coverage page
```

The specs are Amazon's documents and are not committed. The generated
`src/amazon_ads/_generated/catalog.json` and `docs/reference/coverage.md` are. A weekly
workflow runs both scripts and opens a pull request when anything changed.

When the typed layer changes which operations it wraps, update `WRAPPED_PREFIXES` in
`scripts/generate_catalog.py` so the coverage page marks them.

## API quirks

When you find live behavior the specs do not describe, add it to
`src/amazon_ads/notes.py`, handle it in code, and run
`scripts/generate_catalog.py --docs-only` to update `docs/guide/quirks.md`. The same
text is served to Claude by the MCP server.

## Documentation

Every change to public behavior updates the relevant page under `docs/`, the README if
it affects the quick start, and `CHANGELOG.md`, in the same pull request.

```bash
uv run mkdocs serve
```

## Releasing

1. Update `version` in `pyproject.toml` and `src/amazon_ads/_version.py`, and move the
   changelog's Unreleased section under the new version.
2. Tag `vX.Y.Z` and push the tag. The release workflow builds and publishes to PyPI
   with trusted publishing (no token stored in the repository).
