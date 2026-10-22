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

## Contributor terms

By opening a pull request you confirm two things.

**You wrote it, or you have the right to submit it.** The contribution is your own work,
or you have permission from whoever owns it, and submitting it here breaks no agreement
you are under. If any part came from somewhere else, say where in the pull request.

**You grant a licence wide enough to relicense.** You keep the copyright in what you
write. You grant Anthony Jack a perpetual, worldwide, irrevocable, royalty-free licence
to use, reproduce, modify, publish and distribute your contribution, and to sublicense it
and offer it to others under any licence terms, including terms different from the ones
this project uses today.

That second paragraph is the part that matters and the reason this is a grant rather than
a Developer Certificate of Origin sign-off. Under Apache-2.0 alone each contributor keeps
their own copyright and licenses only under Apache-2.0, so a project cannot later be
offered under different terms without going back to every contributor who ever landed a
patch. Projects that skip this and later want to change course have to track people down
years afterwards, and some of them cannot be found. Asking once, up front, costs a
contributor nothing and keeps the choice open.

The project is Apache-2.0 today and this does not change that. Anything already published
stays published under it: an Apache-2.0 grant on a release cannot be withdrawn, and
nothing here tries to. The grant is about what may happen to future versions.

## Reporting bugs

Include the `adsctl --version`, the command or code, and the full error with Amazon's
request id (`ApiError.request_id`). Remove credentials and account identifiers first.
