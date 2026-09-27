# Contributing

Thanks for helping. The short version:

1. `uv sync --all-extras`
2. Make the change, with a test that would fail without it.
3. Update the docs page it affects, the README if the quick start changes, and
   `CHANGELOG.md` under Unreleased.
4. Run the checks in [docs/development.md](docs/development.md#checks).
5. Open a pull request describing what changed and why.

## Ground rules

- **No account data.** Never commit real profile ids, campaign or keyword ids, ASINs,
  campaign names, bids or report rows. Tests use invented ids.
- **Tests catch bugs.** Each test should answer "what bug does this catch?" Do not test
  hardcoded values or Python itself.
- **Live behavior is evidence.** When the API does something its specs do not say,
  record it in `src/amazon_ads/notes.py` with what was observed, handle it in code, and
  regenerate `docs/guide/quirks.md`.
- **Comments stand alone.** A comment should make sense to someone with only the code
  in front of them: state the concrete reason, not a pointer to a discussion.
- **No em dashes** in code, comments, docs or commit messages. CI checks.
- **Writes stay reviewable.** Anything that changes an account must be expressible as a
  change plan, and the MCP server must never apply a change without the plan's
  fingerprint.

## Reporting bugs

Include the `adsctl --version`, the command or code, and the full error with Amazon's
request id (`ApiError.request_id`). Remove credentials and account identifiers first.
