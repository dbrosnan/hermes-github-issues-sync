# Releasing

Hermes installs catalog plugins at an exact commit, so a release has two halves: a tagged
release here, then a pin bump in the Hermes catalog.

## 1. Prepare

1. Make sure `main` is green in CI.
2. Pick the version with [SemVer](https://semver.org/): patch for fixes, minor for new settings
   or events, major for anything that changes or removes existing behaviour.
3. Update `version` in `plugin.yaml` and `__version__` in `github_issues_sync/__init__.py`.
4. Move the **Unreleased** notes in `CHANGELOG.md` under a new version heading with today's
   date, and update the compare links at the bottom.
5. If the plugin needs a newer Hermes, raise `requires_hermes` in `plugin.yaml`.
6. Run the checks:

   ```bash
   uvx --with pytest-cov pytest --cov=. tests
   uvx ruff check . && uvx ruff format --check .
   hermes plugins validate . --install-deps
   ```

7. Commit as `chore: release vX.Y.Z` and push.

## 2. Tag and publish

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <(sed -n '/## \[X.Y.Z\]/,/## \[/p' CHANGELOG.md | sed '$d')
```

## 3. Update the Hermes catalog pin

The catalog entry lives at `plugin-catalog/github-issues-sync.yaml` in
`NousResearch/hermes-agent`. Updates are reviewed PRs (catalog rule 4).

1. Sync your fork and branch from `upstream/main`.
2. In the entry, set `sha` to the full 40-character commit of the tag
   (`git rev-parse vX.Y.Z^{commit}`) and `version` to `"X.Y.Z"`. If hooks, settings or data
   handling changed, update `capabilities` and the disclosure text too.
3. Open a PR titled `feat(plugin-catalog): bump github-issues-sync to vX.Y.Z`. Link the compare
   view between the old and new tags so reviewers can read exactly what changed.

Users then update with `hermes plugins update github-issues-sync`.
