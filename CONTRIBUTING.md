# Contributing to github-issues-sync

Thanks for helping. This is a small plugin, so the process is light.

## Ways to help

- **Report a bug.** Open an issue. Include your Hermes version (`hermes --version`), your `gh`
  version, the output of `hermes github-issues-sync sync --dry-run`, and the
  `github-issues-sync` lines from `~/.hermes/logs/agent.log`. Remove tokens and private repo
  names before posting.
- **Suggest a feature.** Open an issue first, so we can agree on the shape before you write code.
- **Send a pull request.** Bug fixes, tests and docs are always welcome.
- **Security problems** go through private reporting, never public issues. See
  [SECURITY.md](SECURITY.md).

## Development setup

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). Hermes is only needed for
the final validation step; the tests run against a fake `gh` and a fake `hermes` on PATH.

```bash
git clone https://github.com/dbrosnan/hermes-github-issues-sync
cd hermes-github-issues-sync
uvx --with pytest-cov pytest --cov=. tests   # tests, no Hermes or GitHub needed
uvx ruff check . && uvx ruff format --check .
hermes plugins validate . --install-deps     # the same check the Hermes catalog runs
```

To try a change against your own Hermes:

```bash
hermes plugins install /path/to/hermes-github-issues-sync --force
hermes github-issues-sync sync --dry-run
```

## Pull request checklist

- One change per PR, with a clear title in
  [Conventional Commits](https://www.conventionalcommits.org/) form (`fix: ...`, `feat: ...`,
  `docs: ...`).
- Tests for any behaviour change. CI requires 80% coverage, and the project sits above 90%.
- `uvx ruff check .` and the tests pass.
- `hermes plugins validate . --install-deps` passes, if you have Hermes installed.
- If you add a hook or setting, update `plugin.yaml` (declared capabilities must match what
  the code registers) and the README's settings and disclosure sections.
- Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).

## Rules the code has to keep

These come from the
[Hermes catalog admission rules](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission),
and a PR that breaks one can't be released to the catalog:

- Use public Hermes hooks and `ctx.register_*` APIs only. Never patch Hermes code at runtime.
- All GitHub access goes through the `gh` CLI. The plugin never reads a token, a `.env` file or
  `gh`'s own config; it only chooses which profile's environment `gh` runs with.
- The plugin never closes an issue. The PR's `Fixes #n` does.
- Every issue comment carries its marker and is posted at most once per event.
- `register()` does no work: no processes, files or network. The validate probe runs it with a
  stub context.
- No self-updating code, telemetry, or compiled files.

## Code style

Standard library only, small modules with one job each, keyword arguments for hook callbacks,
and comments only where the reason isn't obvious. Ruff settings are in `pyproject.toml`.
