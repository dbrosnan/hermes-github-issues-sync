# Releasing

Hermes installs catalog plugins at an exact commit, so a release has two halves: a tagged
release here, then a pin bump in the Hermes catalog.

## 1. Prepare

1. Make sure `main` is green in CI.
2. Pick the version with [SemVer](https://semver.org/): patch for fixes, minor for new settings
   or events, major for anything that changes or removes existing behaviour.
3. Update `version` in `plugin.yaml`, `__version__` in `github_issues_sync/__init__.py`, and
   the supported-versions row in `SECURITY.md` on a minor or major bump.
4. Move the **Unreleased** notes in `CHANGELOG.md` under a new version heading with today's
   date, point the `[Unreleased]` compare link at the new tag, and add a `[X.Y.Z]:` compare
   link for it at the bottom.
5. If the plugin needs a newer Hermes, raise `requires_hermes` in `plugin.yaml`.
6. Run the same checks CI runs, with the same pins (see `.github/workflows/ci.yml`):

   ```bash
   uvx --python 3.13 ruff@0.17.3 check . && uvx --python 3.13 ruff@0.17.3 format --check .
   for v in 3.11 3.12 3.13 3.14; do
     uvx --python $v --with pytest-cov==7.1.0 pytest@9.2.0 --cov=. --cov-fail-under=80 tests
   done
   hermes plugins validate . --install-deps
   ```

7. Commit as `chore: release vX.Y.Z` and push.

## 2. Tag and publish

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <(sed -n '/## \[X.Y.Z\]/,/## \[/p' CHANGELOG.md | sed '1d;$d')
```

## 3. Update the Hermes catalog pin

The catalog entry lives at `plugin-catalog/github-issues-sync.yaml` in
`NousResearch/hermes-agent`. Updates are reviewed PRs (catalog rule 4).

The catalog pins the commit, not the tag: the loader rejects branches, tags and short SHAs,
and `version` is a label it never parses, so the tag above is this project's convention and
the `sha` is the release.

1. Sync your fork and branch from `upstream/main`. While the entry's first PR is still open,
   update that branch instead of opening a new one.
2. In the entry, set `sha` to the full 40-character commit of the tag
   (`git rev-parse 'vX.Y.Z^{commit}'`, quoted for zsh) and `version` to `"X.Y.Z"`. If hooks, settings or data
   handling changed, update `capabilities` and the disclosure text too.
3. Open a PR titled `feat(plugin-catalog): bump github-issues-sync to vX.Y.Z`. Link the compare
   view between the old and new tags so reviewers can read exactly what changed.

Users then update with `hermes plugins update github-issues-sync`.
