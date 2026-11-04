# github-issues-sync for Hermes Agent

[![CI](https://github.com/dbrosnan/hermes-github-issues-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/dbrosnan/hermes-github-issues-sync/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/hermes-%3E%3D0.22.5-purple.svg)](https://github.com/NousResearch/hermes-agent)

Keep GitHub Issues and the [Hermes Agent](https://github.com/NousResearch/hermes-agent) kanban
board in sync, with GitHub as the system of record.

- **Pull.** Every open issue you label `agent-ready` becomes one kanban card, assigned to the
  Hermes profile its lane label names (or the repository's default profile), in a worktree of
  your checkout, on branch `issue-<n>`. Re-running never duplicates a card.
- **One board per repository, the whole tree if you want it.** Each repository gets a board
  named after it. With `mirror: full` every open issue is on that board: ready issues as work
  cards, the rest as placeholders the dispatcher never touches, and each epic as a card that
  waits on its sub-issue cards, so the board shows the same tree as GitHub. Label changes move
  cards; a closed issue archives its card.
- **Push.** When a worker claims the card, the issue gets the `in-progress` label and a comment
  naming the profile. When the worker hands off, the summary and the PR URL land on the issue.
  When the worker blocks, the issue gets `blocked` and the reason.
- **The merged PR closes the issue.** The plugin never closes anything. The card tells the
  worker to put `Fixes #<n>` in the PR body, and GitHub does the rest.

Hermes workers read the board, not GitHub. This plugin is the bridge, and it needs nothing but
the [`gh` CLI](https://cli.github.com/) you already log in with.

## How it differs from hermes-github-kanban-plugin

[docbobo/hermes-github-kanban-plugin](https://github.com/docbobo/hermes-github-kanban-plugin)
is webhook-driven: GitHub calls your gateway when an issue changes, and the plugin closes the
issue when the card completes. This plugin is the other shape:

| | github-issues-sync | hermes-github-kanban-plugin |
|---|---|---|
| Trigger | Pull: dispatcher ticks, `/gh-sync`, or a terminal command | Push: GitHub webhooks into the gateway |
| Which issues | Only those a human labelled `agent-ready`, with no open blocker | Issues matching the webhook filter |
| Closing | Never. The PR's `Fixes #n` closes the issue on merge | The plugin closes the issue when the card completes |
| Reaching GitHub | The `gh` CLI, with the assignee profile's own login when it has one | A token in the plugin's configuration |
| Needs | `gh` logged in | A public gateway URL and a webhook secret |

Pick this one when GitHub stays the source of truth, you want a human gate before an agent
picks work up, and you do not want to expose a webhook endpoint.

## The workflow this plugin fits

GitHub Issues is the system of record. Every stage of a task is a label on the issue, so the
board is the issue list filtered by label, and the plugin touches exactly one stage. The names
below are the defaults; `ready_label`, `in_progress_label`, `blocked_label`, `skip_labels` and
`lanes` rename them.

| Stage | Label | Who sets it | What the plugin does |
|---|---|---|---|
| Triage | `triage` | You, when an idea arrives | Nothing. A triage pass turns the idea into an epic with sub-issues. |
| Epic | `epic` | You, on the parent issue | `ready-only`, `tasks`: skips it. `full`: an epic card that waits on its sub-issue cards. |
| Spec | `needs-spec` | Default on every new task | `ready-only`: nothing. `tasks`, `full`: a placeholder card the dispatcher never starts. |
| Ready | `agent-ready` | You, after the sizing gate passes | Reads it. Each open, unblocked issue here becomes one card. |
| Working | `in-progress` | The plugin on claim, or a non-Hermes agent by hand | Adds it on claim. Issues already carrying it are skipped. |
| Done | issue closed | GitHub, when a PR with `Fixes #n` merges | Nothing. The plugin never closes an issue. |
| Blocked | `blocked` plus a blocked-by link | The plugin when a card blocks, or anyone | Adds it on block, removes it on re-claim. `ready-only`: the link, not the label, keeps the issue out of the pull. `tasks`, `full`: either parks the work card in `blocked`; removing both unblocks it. |
| Human only | `needs-human` | You | `ready-only`: nothing. `tasks`, `full`: a placeholder card. Never a work card unless you also label it `agent-ready`. |

Main line: `triage` -> `epic` with `needs-spec` sub-issues -> `agent-ready` -> `in-progress` ->
merged. Side stages: `blocked`, `needs-human`. The one human decision the plugin depends on is
the move from `needs-spec` to `agent-ready`. Everything before it (writing the spec, sizing,
splitting) and everything after the PR opens (review, merge) stays with you.

**Lanes.** A second set of labels says which kind of work a task is, and the `lanes` setting
maps each one to the Hermes profile that does that kind of work: `lane:code` to a coding
profile, `lane:docs` to a writing profile, `lane:research` to a cheaper research profile,
`lane:review` to a reviewing profile. An issue with one lane label gets a card for that
profile; with none it goes to `assignee`; with two it is skipped and the report says so,
because the plugin does not guess.

### `needs-spec` is an inbox

Ideas arrive in bulk. A planning session with an LLM can produce seventy sub-issues in an
afternoon, each one line long. They land as `needs-spec` so nothing is lost and nothing is
started. The plugin never makes a work card out of this stage: a card without acceptance
criteria, a verification command and a file list sends a worker into the repository with
nothing to check its work against. With `mirror: tasks` or `full` the issue is on the board as
a placeholder, so you can see the inbox next to the work, but a placeholder has no branch, no
workspace and an assignee that is not a profile. Promotion is the moment a person has read the
issue, run the sizing gate and decided an agent can finish it from the issue body alone. Until
then the issue is a note, not a task.

## From a new idea to a merged PR

The example repository is `dbrosnan/adbudgetbuddy`. Its helper, `scripts/issues.sh`, is a thin
wrapper over `gh` that you can copy; every step names the `gh` call underneath. "You" is the
human.

1. **Capture the idea** (you): `gh issue create --label triage --title "Month wizard"`. One
   line is enough. Issue: open, `triage`. Card: none.
2. **Triage** (you, or an LLM session you drive): turn the idea into an epic and its tasks.
   `scripts/issues.sh epic "Month Wizard" "§5.2" < epic.md` runs
   `gh issue create --label epic --title "Epic: Month Wizard"` and adds it to the Project;
   `scripts/issues.sh task 12 "Wizard step 1: date range" < task.md` runs
   `gh issue create --parent 12 --label needs-spec`. Add the lane label now:
   `gh issue edit 14 --add-label lane:code`. Issue #14: sub-issue of #12, `needs-spec`,
   `lane:code`. Card: none with `mirror: ready-only`. With `full`, the next sync puts a
   placeholder for #14 and an epic card for #12 on the `adbudgetbuddy` board, the epic card
   waiting on the placeholder.
3. **Write the spec** (you): edit #14 until it has an `## Acceptance criteria` heading with 2 to
   8 `- [ ]` checkboxes, a `## Files` heading, and the verification command to run.
   `scripts/issues.sh gate 14` reports what is missing. Issue: still `needs-spec`.
4. **Promote** (you): `scripts/issues.sh promote 14`, which runs the gate and then
   `gh issue edit 14 --remove-label needs-spec --add-label agent-ready`. Issue: `agent-ready`,
   `lane:code`. Card: none yet (or the placeholder, about to be replaced).
   `scripts/issues.sh ready` lists what the next sync will take.
5. **Sync** (plugin): `hermes github-issues-sync sync`, `/gh-sync`, or the next dispatcher tick.
   Issue: unchanged. Card: `dbrosnan/adbudgetbuddy#14: Wizard step 1: date range`, assignee the
   profile `lanes` maps `lane:code` to, branch `issue-14`, idempotency key
   `gh-dbrosnan-adbudgetbuddy-14`, body the rules plus the issue body, on the board named
   `adbudgetbuddy`. With `full`, the placeholder is archived first and the epic card for #12
   is linked to the new card.
6. **Claim** (Hermes dispatcher, then plugin): the dispatcher claims the card and starts a worker
   in a worktree on `issue-14`. The plugin adds `in-progress` and comments
   "Claimed by Hermes profile `opus-5-5` on 2026-11-03 14:05Z (card `t_4f2a`, run 1)".
   Issue: `agent-ready`, `in-progress`, `lane:code`. A non-Hermes agent (Claude Code, Codex, a
   person) takes the same stage by hand with `scripts/issues.sh claim 14`; the label is the lock,
   and the plugin skips any issue carrying it.
7. **Work** (Hermes worker): implement, run the verification command, open a PR whose body says
   `Fixes #14`, comment on the issue with the PR URL and evidence. One comment per event.
8. **Hand off** (worker, then plugin): the card completes; the plugin posts the handoff summary
   and the PR URL on the issue, with a note that the PR closes it. Issue: labels unchanged, PR
   linked. If the worker blocks instead, the card is `blocked`, the issue gets `blocked`, loses
   `in-progress`, and the reason is posted. If another issue is the cause, add the link yourself
   with `gh issue edit 14 --add-blocked-by <m>`; the plugin will not re-mirror #14 while #m is
   open.
9. **Review and merge** (you): read the PR, merge it. GitHub closes #14. With `ready-only`
   nothing in the plugin fires; a closed issue is simply absent from the next pull. With
   `tasks` or `full` the next sync archives the card, and once every sub-issue card of #12 is
   archived the epic card moves to `ready`, where the dispatcher skips it until GitHub closes
   #12 and the sync archives it too.
10. **Clean up** (you, only when needed): a card that finished without a merged PR leaves the
    issue open and `in-progress`. `gh issue edit 14 --remove-label in-progress` and
    `hermes kanban archive t_4f2a` let the next sync try again.

What you never run: anything that closes an issue, or a `gh issue edit --add-label agent-ready`
that skips the gate.

## Requirements

- Hermes Agent 0.22.5 or newer, with the kanban dispatcher running
  (`hermes kanban daemon`, or `kanban.dispatch_in_gateway: true`).
- `gh` 2.x on PATH, logged in to an account that can read the issues, label them and comment.
- A local checkout of each repository. Cards get a worktree of it, so the worker starts on a
  clean branch without cloning.
- A Hermes profile for the worker, for example `hermes profile create coder`.

## Quick start

```bash
hermes plugins install https://github.com/dbrosnan/hermes-github-issues-sync
hermes plugins enable github-issues-sync
```

Hermes marks a Git URL install as an unreviewed community source and scans the repository
before installing. Once the plugin is listed in the Hermes plugin catalog, prefer
`hermes plugins install github-issues-sync`, which installs the reviewed, pinned release.

Tell it which repositories to mirror and who works them, under
`plugins.entries.github-issues-sync.settings` in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - github-issues-sync
  entries:
    github-issues-sync:
      settings:
        repos:
          - acme/widgets
          - repo: acme/api
            workspace: /home/me/src/api        # a checkout; the card gets a worktree of it
            completion_contract: acme/api      # this repo has required checks
            lanes:
              lane:docs: writer                # overrides the global lane for this repo
        assignee: coder                        # the profile for issues with no lane label
        lanes:                                 # issue label -> Hermes profile(s)
          lane:code: [coder, opus]             # a pool: the member with fewer open cards
          lane:docs: librarian
          lane:research: researcher
          lane:review: reviewer
        workspace_root: /home/me/src           # <root>/<repo name> for repos without workspace
        mirror: full                           # or tasks, or ready-only (the default)
```

Each repository gets a board named after it (`widgets`, `api`), created on the first sync with
the checkout as the board's project directory. The Desktop app's Kanban page switches between
them; `hermes kanban --board widgets list` reads one from a terminal.

Create the labels once per repository (the stage labels first, then one per lane you use),
then see what a sync would do:

```bash
gh label create triage      -R acme/widgets --color C5DEF5 --description "New idea, one line is enough; the triage pass turns it into an epic with sub-issues"
gh label create epic        -R acme/widgets --color 8250DF --description "Parent issue holding a sub-PRD; work happens in its sub-issues"
gh label create needs-spec  -R acme/widgets --color FBCA04 --description "Idea captured; needs acceptance criteria before it is agent-ready"
gh label create agent-ready -R acme/widgets --color 0E8A16 --description "Reviewed, self-contained, sized to one PR; an agent may pick it up"
gh label create in-progress -R acme/widgets --color 1D76DB --description "An agent or human has claimed it"
gh label create blocked     -R acme/widgets --color B60205 --description "Waiting on another issue or external input"
gh label create needs-human -R acme/widgets --color D93F0B --description "Only a person can do this (accounts, legal, payments, credentials)"
gh label create lane:code   -R acme/widgets --color 1D76DB --description "Code change in this repo; routed to the coding profile"
gh label create lane:docs   -R acme/widgets --color 0075CA --description "Documentation only; routed to the docs profile"
hermes github-issues-sync sync --dry-run
```

Promote an issue to `agent-ready` and run `hermes github-issues-sync sync` (or type `/gh-sync`
in a Hermes session). A card appears on the board; the dispatcher claims it; the issue gets
`in-progress` and a comment; the worker opens a PR with `Fixes #n`; the handoff lands on the
issue; the PR merges and GitHub closes the issue. The section above walks through every step.

With the gateway running, the plugin also syncs on its own after dispatcher ticks, at most once
every `sync_interval_seconds` (5 minutes by default). Restart the gateway after enabling the
plugin: a gateway holds its plugin set from startup.

### Which profiles need the plugin

Hermes fires `kanban_task_claimed` in the **dispatcher** process and `kanban_task_completed` /
`kanban_task_blocked` in the **worker** process. Each profile has its own plugins directory, so:

- Install and enable the plugin in the profile that runs the dispatcher. That covers the pull,
  the claim comments, and (through reconcile) everything else.
- Also install it in each worker profile (`hermes -p coder plugins install ...`) if you want
  handoff and block comments the moment they happen. Without it, the next sync in the dispatcher
  posts them, which is at most one interval later.

## Settings

All under `plugins.entries.github-issues-sync.settings`. Every key is also a control in the
Hermes Desktop app under Settings → Plugins → github-issues-sync; the table says which.

| Setting | Default | Control | Meaning |
|---|---|---|---|
| `repos` | `[]` | JSON list | Repositories to mirror. Each entry is `owner/repo` or a map with `repo` and any of `workspace`, `assignee`, `lanes`, `completion_contract`, `branch_prefix`, `max_runtime`, `board`, `board_name`. |
| `assignee` | `""` | text | Hermes profile for issues that no lane label routes. Required unless `lanes` is set or every repo sets its own. |
| `workspace_root` | `""` | text | Directory holding one checkout per repo. A repo without `workspace` gets `worktree:<root>/<repo name>`. Required unless every repo sets `workspace`. |
| `lanes` | `{}` | JSON map | Issue label to Hermes profile, consulted before `assignee`. Also allowed per repo entry, where its keys add to or override the global map. An issue with one matching label goes to that profile; with two it is skipped and reported. A label may list several profiles (`["coder", "opus-5-5"]`): a pool whose member with the fewest open cards takes each new card. |
| `board_per_repo` | `true` | switch | One board per repository, slug from the repository name (`acme/My.Service` becomes `my-service`), display name `board_name` or the repository name, created on first use with the checkout as project directory. A repo entry's `board` pins an existing slug instead. |
| `board` | current board | text | Fallback board slug when `board_per_repo` is off and the repo entry sets no `board`. |
| `mirror` | `ready-only` | dropdown | `ready-only`: only ready issues become cards. `tasks`: every open non-epic issue, ready ones as work cards, the rest as placeholders; label changes move cards, a closed issue archives its card. `full`: `tasks` plus each epic as a card that waits on its sub-issue cards. |
| `epic_label` | `epic` | text | Issues with this label are epics: skipped by `ready-only` and `tasks`, mirrored by `full`. |
| `placeholder_status` | `ready` | dropdown | Column for placeholder cards. `ready`: the Ready column, skipped by the dispatcher because of the assignee. `triage`: Hermes's parking column, only with `kanban.auto_decompose: false`. `blocked`: a sticky block in the Blocked column. |
| `placeholder_assignee` | `github` | text | Assignee written on placeholder and epic cards. Must not be a Hermes profile; that is what keeps the dispatcher off them. |
| `ready_label` | `agent-ready` | text | Issues with this label become work cards. |
| `in_progress_label` | `in-progress` | text | Added on claim. An issue carrying it gets no new work card (`ready-only` skips it; `tasks` and `full` give it a placeholder when it has no card). |
| `blocked_label` | `blocked` | text | Added when a card blocks, removed when it is claimed again. In `tasks` and `full` it also parks the work card. |
| `skip_labels` | `[epic]` | JSON list | Issues with any of these are never mirrored (in `full`, the epic label is mirrored as an epic even if listed here). |
| `branch_prefix` | `issue-` | text | The work card's branch is `<prefix><issue number>`. |
| `max_runtime` | `90m` | text | Per-card runtime cap passed to `hermes kanban create --max-runtime`: a number of seconds, or a number with one unit, such as `300`, `90m` or `2h`. |
| `completion_contract` | `local-only` | text | `local-only`, or `OWNER/REPO` when that repo has required checks and you want them to gate completion (a GitHub PR URL is also accepted). A free-plan private repo cannot have required checks; keep `local-only` there. |
| `max_cards_per_sync` | `10` | number, 0 to 1000 | Upper bound on work cards one sync may create. Placeholders and epics do not count. |
| `issue_limit` | `100` | number, 1 to 5000 | Open issues fetched per repository. With `tasks` or `full` it must cover every open issue; the report warns when it is hit. |
| `sync_on_dispatch_tick` | `true` | switch | Sync in the background after dispatcher ticks. |
| `sync_interval_seconds` | `300` | number, 0 to 86400 | Minimum seconds between tick-triggered syncs. |
| `reconcile` | `true` | switch | During a sync, post claim, handoff and block events that no hook posted. |
| `push_events` | `[claimed, completed, blocked]` | JSON list | Which transitions are reported on the issue. |
| `summary_chars` | `2000` | number, 0 to 60000 | Characters of the handoff summary or block reason posted on the issue. |
| `card_template` | built-in | text | Work card body template. Fields: `{url}`, `{repo}`, `{number}`, `{title}`, `{body}`, `{branch}`, `{workspace}`, `{assignee}`, `{in_progress_label}`, `{blocked_label}`. A `Source issue:` line is added if the template lacks one; a template that fails to render logs a warning and the built-in one is used. |
| `created_by` | `github-issues-sync` | text | Author recorded on cards and card comments. |
| `require_profile_login` | `false` | switch | Never let `gh` fall back to its stored login: a profile without a `GH_TOKEN` or `GH_CONFIG_DIR` of its own runs `gh` against an empty config directory under the profile home, so the command fails with exit 4, and a profile that does not exist is an error instead of a warning. |
| `command_timeout_seconds` | `30` | number, 1 to 3600 | Seconds allowed for each `gh` command. `hermes` commands get twice that, and never less than 60. |
| `gh_bin`, `hermes_bin` | `gh`, `hermes` | text | The executables to run. |

A `workspace` value is a checkout path, or a Hermes workspace spec (`worktree:<path>`,
`dir:<path>`, `scratch`) if you need something else. In `config.yaml` the list settings
(`repos`, `skip_labels`, `push_events`) also accept a comma-separated string. Numbers outside
their bounds are clamped, not rejected.

### The Desktop settings panel

Hermes renders `config_schema` from `plugin.yaml` with one control per field type: a switch
for `bool`, a dropdown for a `str` with `choices`, a number input for `int`, a text input for
`str`, and a JSON editor for `list` and `dict`. Settings → Plugins → github-issues-sync looks
like this (the two-column layout is the app's; values are a working example):

```
Settings › Plugins › github-issues-sync                                   0.3.0  [Save]

 Repositories                                      Assignee profile
 ┌──────────────────────────────────────────────┐  ┌──────────────────────────────────┐
 │ ["acme/widgets",                             │  │ coder                            │
 │  {"repo": "acme/api", "board": "apis"}]      │  └──────────────────────────────────┘
 └──────────────────────────────────────────────┘  Hermes profile that works cards no
 GitHub repositories to mirror, as owner/repo …    lane label routes.

 Workspace root                                    Lanes
 ┌──────────────────────────────────────────────┐  ┌──────────────────────────────────┐
 │ /home/me/src                                 │  │ {"lane:code": "coder",           │
 └──────────────────────────────────────────────┘  │  "lane:docs": "librarian"}       │
                                                   └──────────────────────────────────┘
 One board per repository          [ on  ●]        Fallback board
                                                   ┌──────────────────────────────────┐
                                                   │                                  │
                                                   └──────────────────────────────────┘
 What to mirror                                    Epic label
 ┌──────────────────────────────────────────┐      ┌──────────────────────────────────┐
 │ full                                   ▾ │      │ epic                             │
 └──────────────────────────────────────────┘      └──────────────────────────────────┘
   ready-only · tasks · full

 Placeholder column                                Placeholder assignee
 ┌──────────────────────────────────────────┐      ┌──────────────────────────────────┐
 │ ready                                  ▾ │      │ github                           │
 └──────────────────────────────────────────┘      └──────────────────────────────────┘
   ready · triage · blocked

 Ready label        [agent-ready ]                 In-progress label  [in-progress ]
 Blocked label      [blocked     ]                 Skip labels        ["epic"]
 Branch prefix      [issue-      ]                 Max runtime        [90m         ]
 Completion contract [local-only ]                 Cards per sync     [10        ⇅]
 Issues per repo    [500       ⇅]                  Sync on dispatcher ticks  [ on ●]
 Sync interval      [300       ⇅]                  Reconcile on sync         [ on ●]
 Push events        ["claimed","completed","blocked"]
 Summary length     [2000      ⇅]                  Card template      [            ]
 Created by         [github-issues-sync]           Require profile gh login  [off ○]
 Command timeout    [30        ⇅]                  gh command         [gh          ]
 hermes command     [hermes      ]
```

What the panel cannot do, and why: the renderer has no control that lists Hermes profiles,
boards or a repository's labels, so `assignee`, the `lanes` values, `board` and the label
settings are text, validated when the plugin loads them (`hermes github-issues-sync status`
reports a bad value). Number bounds are enforced by the plugin, not by the input. `repos` and
`lanes` are JSON in the panel and YAML in `config.yaml`; both are the same shape.

## Commands

| Where | Command | What it does |
|---|---|---|
| Any Hermes session | `/gh-sync` | Sync every configured repository and return the report. |
| | `/gh-sync --dry-run [owner/repo]` | Report what would be created or posted, change nothing. `-n` is a short form. |
| Terminal | `hermes github-issues-sync sync [--dry-run] [--repo owner/repo] [--json]` | The same, with machine-readable output on request. Exit code 1 when anything failed (a setting, a repository or a card), 2 without a subcommand. |
| | `hermes github-issues-sync status` | The mirror mode, the boards, the repositories with their assignee, lanes, workspace and contract, the labels, the sync switches, and the last sync this profile ran. |

A report looks like this:

```
github-issues-sync (dry run)
acme/widgets: 3 open `agent-ready` issues
  would create: acme/widgets#12 'Wizard step 1: date range' (branch issue-12, assignee coder) on `widgets`
  skipped acme/widgets#13: blocked by open #12
  skipped acme/widgets#9: card t_4f2a exists (running)
summary: 1 would create, 0 moved, 2 skipped, 0 posted, 0 errors
```

and with `mirror: full` on a repository that has no board yet:

```
github-issues-sync (dry run)
would create board `widgets`
acme/widgets: 7 open issues (full mirror)
  would create task: acme/widgets#12 'Wizard step 1: date range' (branch issue-12, assignee coder) on `widgets`
  would create placeholder: acme/widgets#14 'Wizard step 2: channels' (placeholder, needs-spec) on `widgets`
  would create epic: acme/widgets#10 'Epic: Month wizard' (epic, waits on 2 sub-issue cards) on `widgets`
  would archive acme/widgets#8 (card t_91c0): issue closed
  would replace acme/widgets#11 (card t_2a7e): placeholder card becomes a task card
  would block acme/widgets#13 (card t_4f2a): blocked by open #12
summary: 3 would create (1 tasks, 1 placeholders, 1 epics), 3 moved, 0 skipped, 0 posted, 0 errors
```

## How it works

**Boards.** With `board_per_repo` on, a sync first runs `hermes kanban boards list --json`
once and creates any repository board that is missing:

```
hermes kanban boards create widgets --name widgets --default-workdir /home/me/src/widgets
```

Every later `hermes kanban` call for that repository carries `--board widgets`. Push hooks
already receive the board a card lives on and use it. An archived board counts as missing and
is recreated; archive the plugin's cards instead if you want a repository off the board.

**Pull.** For each repository, `gh issue list --state open` (with `--label agent-ready` in
`ready-only`) and the `blockedBy` and `parent` fields, run with the `gh` identity of the
repository's `assignee` profile (or of the first lane's profile when there is no assignee).
In `ready-only`, an issue is skipped when it carries a
`skip_labels` label or the `in_progress_label`, when any issue blocking it is still open, when
a card that `hermes kanban list` returns (which excludes archived cards) already mirrors it, or
when its lane labels match two `lanes` entries. The rest become cards in the order `gh` returns
them (newest first by default), up to `max_cards_per_sync`, each assigned to the profile its
lane label maps to, or to `assignee`. A lane that lists several profiles is a pool: the card
goes to the member with the fewest open cards on the board (counting the ones this sync has
just planned), ties to the first listed, so two profiles can work one lane side by side under
Hermes's per-profile cap:

```
hermes [--board <slug>] kanban create "acme/widgets#12: Wizard step 1: date range" \
  --assignee coder --workspace worktree:/home/me/src/widgets --branch issue-12 \
  --idempotency-key gh-acme-widgets-12 --max-runtime 90m --created-by github-issues-sync \
  [--completion-contract acme/api] --json --body-file <issue body plus the rules>
```

The card title `owner/repo#n: ...` and the `Source issue:` line in the body are how a card is
recognised later from nothing but its task id. The idempotency key, `gh-<owner>-<repo>-<n>`,
is a second guard inside Hermes: a card with the same key that is not archived is returned
instead of a duplicate. A hidden `<!-- github-issues-sync: kind=task -->` line under the
source line says what kind of card it is; a card without one (from 0.2 or earlier) is a work
card.

**Mirror** (`tasks` and `full`). Every open issue is given a desired card and compared with
the card it has:

| Issue | Card | Status | Assignee |
|---|---|---|---|
| `agent-ready`, no open blocker, no `blocked` label | work card | `ready` | the lane's profile, or `assignee` |
| `agent-ready` with an open blocked-by link or the `blocked` label | work card | `blocked` (sticky, with the reason as a card comment) | same |
| `in-progress` without a card (claimed outside Hermes) | placeholder | `placeholder_status` | `placeholder_assignee` |
| any other open non-epic issue (`needs-spec`, `needs-human`, `triage`, ...) | placeholder | `placeholder_status` | `placeholder_assignee` |
| `epic` (`full` only) | epic card, child of its sub-issues' cards | `todo` while any sub-issue card is open | `placeholder_assignee` |
| a `skip_labels` label (other than the epic label in `full`), or two lane labels | none | | |

Then the differences become `hermes kanban` calls, in this order: cards whose issue is closed
are archived (`archive`), unless a worker is running them, which the report says; a card of
the wrong kind is archived and recreated (`replace`), unless a worker has it or it is done; a
work card that should be blocked is blocked (`block --kind needs_input`), one that should not
be is unblocked (`unblock`); missing cards are created, work cards and placeholders first,
then epics with `--parent <sub-issue card>` for every open sub-issue; finally an existing epic
card gets a `link` for any sub-issue card it does not wait on yet. The dispatcher never sees a
placeholder as work: its assignee is not a profile, so each dispatch pass records one
`skipped_nonspawnable` event and moves on, which Hermes calls a terminal lane. An epic card is
the *child* of its sub-issue cards because a Hermes parent link means "the parent must finish
first": linked that way round, the epic card waits in `todo` while its sub-issues are open and
promotes to `ready` (where it is skipped, same assignee) when they are all done or archived,
which is when GitHub closes the epic and the sync archives it.

Why not `todo` for placeholders: the dispatcher promotes every `todo` card without an
unfinished parent to `ready` on each tick, so a parentless placeholder cannot stay there. Why
not `triage` by default: the built-in decomposer fans triage cards out into child tasks when
`kanban.auto_decompose` is on, which is the Hermes default. `placeholder_status: triage` is
there for boards that run with it off.

**Push.** A hook payload carries the task id. The plugin runs `hermes kanban show <id> --json`,
reads the issue reference from the card, and acts only if that repository is configured.
Every comment ends with a hidden marker such as
`<!-- github-issues-sync: completed t_4f2a run=3 -->`, and an event is posted only when the
issue has no comment with that marker yet. That makes the push idempotent across processes.

| Event | Labels | Comment |
|---|---|---|
| `kanban_task_claimed` | `+in-progress -blocked` | Claimed by Hermes profile `coder` on 2026-11-02 14:05Z (card `t_4f2a`, run 3). |
| `kanban_task_completed` | unchanged | Handoff from Hermes profile `coder` on 2026-11-02 15:40Z (card `t_4f2a`, run 3), the summary (clipped to `summary_chars`), the PR URL, and a note that the PR's `Fixes #n` closes the issue. |
| `kanban_task_blocked` | `+blocked -in-progress` | Blocked by Hermes profile `coder`, with the reason. |

An empty hook summary falls back to the card's latest summary; with none at all the comment
says so. The PR URL is looked for in the handoff summary, the run's `published_pr` metadata and
the card's completion contract when that is a PR URL; a URL in the issue's own repository wins
over one elsewhere, otherwise the first found. When none is found the comment says the issue
stays open. The `blocked` label is informational: what keeps an issue out of the next pull is
an open blocked-by link, not the label.

**Reconcile.** After creating cards, a sync looks at every work card that mirrors an issue of
that repository: `running` cards get the claim event, `review` and `done` cards the handoff
event, `blocked` cards the block event. The marker check means this posts only what a hook
missed. Placeholder and epic cards mirror a stage, not a worker, and post nothing.

**Process split.** The claim hook fires inside the dispatcher's board lock, so the plugin hands
the work to a background thread and returns at once. The completed and blocked hooks fire in a
worker that may exit right afterwards, so those post synchronously, bounded by
`command_timeout_seconds` per command. Dispatcher ticks only start a background sync when the
interval has passed, and never when the dispatcher itself is in dry-run mode.

**The `gh` identity.** Hermes's completion-contract gate runs `gh` as the card's assignee
profile, with that profile's own `GH_TOKEN` or `GH_CONFIG_DIR`. This plugin does the same:
it resolves the profile home through `hermes_constants` and reads the profile's secrets through
`agent.secret_scope`, then overlays `GH_TOKEN`, `GITHUB_TOKEN` and `GH_CONFIG_DIR` on the
environment it gives `gh`. The launch process's own `GH_TOKEN`, `GITHUB_TOKEN` and
`GH_CONFIG_DIR` are dropped first, because Hermes loads its `.env` into its process and that
token belongs to the launch profile, not to the assignee. A profile with no gh credentials of
its own leaves `gh` to its stored login (the one `gh auth status` shows) and says so in the log
at debug level. With `require_profile_login: true` the plugin instead points `gh` at an empty
config directory under the profile home, so the command fails with exit 4 unless that profile
has its own login, and a profile that does not exist is an error rather than a warning. When
the assignee is the profile the process runs as, the environment is used unchanged.

### Working an issue, from the card

The card body carries the issue body plus these rules, which are the agent checklist from the
workflow above in card form, so a worker that has never seen the repository knows the contract:

1. Issue `#n` is labelled `in-progress` for you when the card is claimed.
2. Work on branch `issue-n`, already checked out in your workspace. Open a PR against the
   default branch whose body contains `Fixes #n`.
3. Comment on the issue with the PR URL and evidence. Do not close the issue by hand.
4. If you cannot finish, block the card with the reason. The issue is labelled `blocked` and the
   reason is posted there.
5. Run the verification command named in the issue before requesting review.

A card that finished without a merged PR leaves the issue open and labelled `in-progress`.
Remove the label to let the next sync consider it again, and archive the old card first if you
want a fresh one (`hermes kanban archive <id>`).

## What it reads and writes (disclosure)

- **GitHub, through `gh`:** reads open issues (number, title, body, URL, labels, blocking
  issues, parent issue), only those with the ready label in `ready-only`, and, before posting,
  an issue's state, labels and comment bodies.
  Writes labels and comments on those issues only. Never closes, reopens, edits the body of, or
  creates an issue. Never touches a repository that is not in `repos`.
- **The kanban boards, through `hermes kanban`:** lists boards and creates one per repository
  (`board_per_repo`); lists cards, shows one card, creates cards and appends a one-line comment
  on a card after posting to its issue. With `mirror: tasks` or `full` it also archives the
  cards of closed issues and of cards it replaces, blocks and unblocks work cards to match the
  issue, and links epic cards to their sub-issue cards. Never completes or deletes a card,
  never archives a running card, never removes a board.
- **What reaches GitHub:** the Hermes profile name, the card id and run number, the handoff
  summary and the block reason (each clipped to `summary_chars`, 2000 by default), and the PR
  URL. Summaries and reasons are written by the worker and may contain anything the worker
  chose to say. The workspace path is on the card, not on the issue.
- **What reaches the board:** the issue body, verbatim, inside the card body, plus the issue
  URL, repository and workspace path; for a board it creates, the repository name and the
  checkout path as the board's project directory.
- **Credentials:** none are read or stored. The plugin picks which profile's environment `gh`
  runs with, via Hermes's `agent.secret_scope`, drops the launch process's gh variables for a
  different profile, and `gh` does its own authentication.
- **Shell commands:** `gh` and `hermes`, from PATH (or `gh_bin` / `hermes_bin`), always as
  argument lists, never through a shell. Card bodies go to `hermes` through a temporary file
  that is deleted after the call.
- **Background work:** one daemon thread per loaded plugin for claim posts and tick-triggered
  syncs. It starts on first use, not at load.
- **Storage:** the last sync's summary in the plugin's own Hermes state, for `status`.
- No network calls other than what `gh` makes to GitHub, no telemetry, no self-updates, no
  compiled files.

## Troubleshooting

| Symptom | Check |
|---|---|
| `no repositories configured` | Add `repos` under `plugins.entries.github-issues-sync.settings`. |
| `no assignee profile` or `no workspace` | Set `assignee` (or `lanes`) and `workspace_root` globally, or `assignee` and `workspace` on the repo entry. |
| `skipped ...: has lane labels ...; keep one` | The issue carries two labels that `lanes` maps. Remove one. |
| `skipped ...: has no lane label and the repository sets no assignee` | Add a lane label to the issue, or set `assignee` as the fallback. |
| `gh issue list ... failed (exit 4)` | `gh` has no stored login. Run `gh auth login`, or put a `GH_TOKEN` in the assignee profile's `.env` (a `GH_TOKEN` in the launch profile's `.env` is deliberately not used for a different profile). |
| `HTTP 404` on a private repo | The `gh` login cannot see the repository. Check `gh auth status` and the token's scopes. |
| Cards appear but nothing claims them | The dispatcher is not running, or the assignee profile does not exist. `hermes kanban diagnostics`. |
| No comments on the issue after a handoff | The worker profile does not load the plugin. The next sync posts it; or install the plugin in that profile. |
| A card exists but the issue has no `in-progress` label | The claim hook did not fire in a process with the plugin. Run `/gh-sync`; reconcile posts it. |
| The same issue got two cards | Someone archived the first card while the issue stayed `agent-ready` without `in-progress`. Archive one. With `tasks` or `full`, the sync replaces a card whose kind changed; the old one is archived, not deleted. |
| Cards went to a new board after updating | `board_per_repo` is on by default since 0.3.0. Set it to `false` to keep one board, or archive the cards on the old board. |
| `warning: ... gh returned N issues, the issue_limit` | Raise `issue_limit` above the number of open issues; with `tasks` or `full` the pull must see every open issue or it archives nothing and mirrors only part of the tree. |
| `cannot derive a board slug from repository name` | The name has no `[a-z0-9]` characters. Set `board` on the repo entry. |
| The dispatcher log says `Skipped (non-spawnable assignee ...)` for the placeholders | Expected. That is how placeholders and epic cards stay out of the way; Hermes records it once per card. |
| Placeholders got decomposed into child tasks | `placeholder_status: triage` with `kanban.auto_decompose` on. Use `ready` (the default) or turn auto-decompose off. |
| Nothing syncs on its own | The gateway was started before the plugin was enabled, `sync_on_dispatch_tick` is off, or the dispatcher is not running in that gateway. |

Log lines are prefixed `github-issues-sync` in `~/.hermes/logs/agent.log`.

## FAQ

**Why label-gated?** Because an agent should only pick up work a person has read and sized.
The label is the approval; the plugin never decides what is ready.

**Why does it not close the issue?** The PR's `Fixes #n` closes it on merge, which is also when
the work actually landed. A card can complete with a PR that is never merged; the issue should
stay open then.

**Why does it ignore `needs-spec`?** Because that stage is the inbox: ideas captured before
anyone has written acceptance criteria. The sizing gate and the promotion to `agent-ready` are
where a person decides the work is ready for an agent.

**Do Claude Code, Codex or people use it?** No. They read GitHub directly and claim with
`scripts/issues.sh claim <n>`, which sets the same `in-progress` label. Only Hermes workers go
through the board, and the label keeps the two kinds of agent off the same issue.

**Can one Hermes board serve several repositories?** Yes, with `board_per_repo: false` and a
shared `board`. Each repo entry carries its own workspace, and the card title names the
repository. The default since 0.3.0 is one board per repository, named after it.

**Why is the epic card the child of its sub-issue cards, not their parent?** Because in Hermes
a parent link means the parent must finish before the child can start. GitHub's epic is the
thing that finishes last, so it is the child. Linking it the other way would hold every
sub-issue card in `todo` until the epic was done, which is never.

**Does it need the issue sync script from the Hermes docs?** No. This plugin replaces the
one-off shell script with the same card shape, plus the push direction and the reconcile pass.

**Does it support GitLab or Gitea?** No. It uses `gh`, so GitHub only.

## Contributing

Bug reports, ideas and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md),
see [SECURITY.md](SECURITY.md) for private vulnerability reports, and
[docs/RELEASING.md](docs/RELEASING.md) for how a change reaches users. Changes are listed in
[CHANGELOG.md](CHANGELOG.md).

```bash
uvx --with pytest-cov pytest --cov=. tests
uvx ruff check . && uvx ruff format --check .
hermes plugins validate . --install-deps
```

## License

MIT
