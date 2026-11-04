# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-11-04

### Added

- **Lane pools.** A `lanes` entry may list several profiles (`lane:code: [coder, opus-5-5]`).
  Each new work card goes to the pool member with the fewest open cards (ready, running,
  review, blocked, todo), ties to the one listed first, counting the cards created earlier in
  the same sync. With `kanban.max_in_progress_per_profile: 1` and `kanban.max_in_progress` at
  least the pool size, the members work the lane side by side. `status` prints a pool as
  `` `lane:code` -> coder | opus-5-5 ``. A single profile per label is unchanged.

### Changed

- `RepoSettings.lanes` values are tuples of profile names (one element for a plain entry).
  `identity_profile` without an `assignee` is the first profile of the lane that sorts first.

## [0.3.0] - 2026-11-03

### Added

- **One board per repository.** `board_per_repo` (on by default) gives each configured
  repository its own kanban board: the slug is derived from the repository name (`widgets` for
  `acme/widgets`, lowercased, anything outside `[a-z0-9_-]` replaced by `-`), the display name
  is `board_name` or the repository name, and the board is created with
  `hermes kanban boards create --default-workdir <checkout>` the first time a sync needs it. A
  repo entry can pin an existing slug with `board`. Every card, push and reconcile for that
  repository goes to that board.
- **`mirror` setting.** `ready-only` is the v0.2 pull. `tasks` mirrors every open non-epic
  issue: issues with the ready label become work cards, everything else (`needs-spec`,
  `needs-human`, `triage`, in progress outside Hermes) becomes a placeholder card that is never
  dispatched. `full` adds each epic as a card linked as the *child* of its sub-issue cards, so
  the Hermes graph has the same tree as GitHub sub-issues and the epic card waits in `todo` the
  way the epic waits on GitHub. Cards carry a hidden `kind=` marker; a card without one (v0.2)
  is a work card.
- **Label changes move cards** (`tasks` and `full`): `needs-spec` to `agent-ready` replaces the
  placeholder with a work card; the reverse replaces the work card with a placeholder (not
  while a worker has it); a `blocked` label or an open blocked-by link parks the work card in
  `blocked` with a sticky block, and removing it unblocks the card; a closed issue archives its
  card, unless the card is running, which the report says; a new sub-issue of an existing epic
  links its card to the epic card.
- `epic_label`, `placeholder_status` (`ready`, `triage` or `blocked`) and
  `placeholder_assignee` settings. Placeholders and epics are kept out of the dispatcher by
  their assignee: a name that is not a Hermes profile lands in the dispatcher's
  "non-spawnable assignee" bucket, which it treats as a terminal lane and records once on the
  card. `todo` cannot hold them, because the dispatcher promotes every `todo` card without an
  unfinished parent to `ready` on each tick; `triage` is Hermes's own parking column but the
  built-in decomposer fans triage cards out when `kanban.auto_decompose` is on (the Hermes
  default), so it is opt-in; `blocked` works but says the wrong thing.
- Report: `created` lines name the card kind and the board, `moved` lines list archives,
  replacements, blocks, unblocks and links, `warning:` lines appear when `gh` returned exactly
  `issue_limit` issues, and the summary counts tasks, placeholders and epics. `status` prints
  the mirror mode, the placeholder column and each repository's board.
- `plugin.yaml` declares every setting for the Hermes Desktop settings panel: switches for the
  booleans, dropdowns for `mirror` and `placeholder_status` (`choices`), number inputs with the
  bounds named in the description and enforced by the loader (`max_cards_per_sync` 0 to 1000,
  `issue_limit` 1 to 5000, `sync_interval_seconds` 0 to 86400, `summary_chars` 0 to 60000,
  `command_timeout_seconds` 1 to 3600), and JSON editors for `repos`, `lanes`, `skip_labels`
  and `push_events`.

### Changed

- Placeholder and epic cards do not count against `max_cards_per_sync`; only work cards do,
  because only work cards start a worker.
- A `BoardError` while reading the board or creating its board is reported as
  `cannot read the board: ...` for that repository and the sync moves on to the next one.
- The summary line gained a `moved` count: `1 created, 0 moved, 3 skipped, 0 posted, 0 errors`.

### Migration from 0.2.x

- `board_per_repo` defaults to on, so after updating, cards for `acme/widgets` go to a board
  named `widgets`, not to the board the v0.2 `board` setting named. Cards that already exist on
  the old board are left where they are and are no longer seen by the sync, so the next sync
  creates a fresh work card on the new board for every open ready issue that has no card
  there. Either archive the old cards first, or set `board_per_repo: false` to keep the v0.2
  layout; `board` is then the fallback for repositories without a `board` of their own.
- `mirror` defaults to `ready-only`, `epic_label` to `epic`, `placeholder_status` to `ready`
  and `placeholder_assignee` to `github`, so with `board_per_repo: false` a v0.2 configuration
  behaves as before.
- With `mirror: tasks` or `full`, `issue_limit` must cover every open issue of the
  repository, not only the ready ones. The report warns when the limit is hit.
- Profiles that only run workers can stay on the same settings; the mirror runs where the
  dispatcher runs.

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

[Unreleased]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/dbrosnan/hermes-github-issues-sync/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dbrosnan/hermes-github-issues-sync/releases/tag/v0.1.0
