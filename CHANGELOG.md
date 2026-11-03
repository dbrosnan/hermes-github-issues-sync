# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-03

### Added

- `lanes` setting, global and per repository: a map of issue label to Hermes profile, consulted
  before `assignee`. An issue with one lane label gets a card for that profile; with none it goes
  to `assignee`; with two it is skipped and the report says which labels clash. `assignee` is no
  longer required when `lanes` is set. `status` prints the lanes.

### Fixed

- A dry-run sync (`/gh-sync --dry-run`, `sync --dry-run`) posted reconcile events: the flag was
  not carried into the per-card reconcile, so running, review, done and blocked cards had their
  issues labelled and commented on. The report now says `would post` and nothing is written.

### Changed

- README: new sections on the Issues-first workflow the plugin fits (stages by label, from
  `triage` and `needs-spec` through `agent-ready` and `in-progress` to the merged PR, with
  `blocked`, `needs-human` and `epic` as side stages), a walkthrough from a new idea to a merged
  PR, and the full label set with the colours and descriptions of the reference repository.
- README and `plugin.yaml` now describe what the code does where they drifted: `hermes` commands
  get twice the `gh` timeout and never less than 60 seconds; `require_profile_login` points `gh`
  at an empty config directory rather than failing early; a PR URL in the issue's own repository
  is preferred when several are found; the completed comment's fallbacks when the summary or the
  PR URL is missing; `completion_contract` also accepts a PR URL; `max_runtime` takes `300`,
  `90m` or `2h`; an invalid `card_template` falls back to the built-in one with a warning; `-n`
  as a short dry-run flag; exit code 2 without a subcommand; `skipped` reasons for lane clashes.
- `docs/RELEASING.md` lists `SECURITY.md` among the files a version bump touches, pins the check
  commands to the versions CI uses, and says the catalog pins a commit, never a tag.

## [0.1.1] - 2026-11-02

### Fixed

- `gh` ran with the launch process's `GH_TOKEN` when the assignee profile had no gh credentials
  of its own. Hermes loads its `.env` into the process, so that token belongs to the launch
  profile, not to the assignee. The launch process's `GH_TOKEN`, `GITHUB_TOKEN` and
  `GH_CONFIG_DIR` are now dropped for a different profile, and `gh` uses its stored login
  unless `require_profile_login` is on.

## [0.1.0] - 2026-11-02

### Added

- Pull: open issues labelled `agent-ready` (configurable) in the configured repositories become
  one kanban card each, created through `hermes kanban create` with an idempotency key, a
  worktree of the repo checkout, branch `issue-<n>`, a runtime cap and a completion contract.
  Issues labelled `epic` or `in-progress`, issues with an open blocker, and issues that already
  have a card are skipped and listed with the reason.
- Push: `kanban_task_claimed` adds the `in-progress` label and posts who claimed the card;
  `kanban_task_completed` posts the handoff summary and the PR URL; `kanban_task_blocked` adds
  the `blocked` label and posts the reason. The issue is never closed by the plugin.
- Reconcile: every sync also posts claim, handoff and block events that a worker profile without
  the plugin did not post. Each event is posted once, keyed by a hidden marker in the comment.
- `/gh-sync [--dry-run] [owner/repo]` in any session and `hermes github-issues-sync sync`,
  `hermes github-issues-sync status` in a terminal.
- Background sync after dispatcher ticks, at most once per `sync_interval_seconds`.
- `gh` runs with the assignee profile's own `GH_TOKEN` or `GH_CONFIG_DIR` when the profile has
  one, the way Hermes's completion-contract gate does. `require_profile_login` makes a profile
  without its own login an error instead of a fallback to the ambient login.

[Unreleased]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dbrosnan/hermes-github-issues-sync/releases/tag/v0.1.0
