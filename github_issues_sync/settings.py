"""Settings under ``plugins.entries.github-issues-sync.settings``, read on every run.

Reading at run time rather than at ``register()`` means a config edit applies to the next sync
without restarting the gateway, and keeps ``register()`` free of work.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

LOCAL_ONLY = "local-only"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PR_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$")
DURATION_RE = re.compile(r"^[1-9][0-9]*(s|m|h|d)?$")
BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
WORKSPACE_KINDS = ("scratch", "worktree", "worktree:", "dir:")
EVENTS = ("claimed", "completed", "blocked")
MIRROR_MODES = ("ready-only", "tasks", "full")
PLACEHOLDER_STATUSES = ("ready", "triage", "blocked")
FALSE_WORDS = {"", "0", "false", "off", "no"}

DEFAULTS: dict[str, Any] = {
    "repos": [],
    "assignee": "",
    "workspace_root": "",
    "lanes": {},
    "board_per_repo": True,
    "board": "",
    "mirror": "ready-only",
    "epic_label": "epic",
    "placeholder_status": "ready",
    "placeholder_assignee": "github",
    "ready_label": "agent-ready",
    "in_progress_label": "in-progress",
    "blocked_label": "blocked",
    "skip_labels": ["epic"],
    "branch_prefix": "issue-",
    "max_runtime": "90m",
    "completion_contract": LOCAL_ONLY,
    "max_cards_per_sync": 10,
    "issue_limit": 100,
    "sync_on_dispatch_tick": True,
    "sync_interval_seconds": 300,
    "reconcile": True,
    "push_events": list(EVENTS),
    "summary_chars": 2000,
    "card_template": "",
    "created_by": "github-issues-sync",
    "require_profile_login": False,
    "command_timeout_seconds": 30,
    "gh_bin": "gh",
    "hermes_bin": "hermes",
}
BOUNDS: dict[str, tuple[int, int]] = {  # inclusive; the Desktop form has no bounds of its own
    "max_cards_per_sync": (0, 1000),
    "issue_limit": (1, 5000),
    "sync_interval_seconds": (0, 86400),
    "summary_chars": (0, 60000),
    "command_timeout_seconds": (1, 3600),
}


class ConfigError(ValueError):
    """A setting that cannot be used. The message is written for the person editing config.yaml."""


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in FALSE_WORDS
    return default if value is None else bool(value)


def as_int(value: Any, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        number = max(minimum, int(value))
    except (TypeError, ValueError):
        return default
    return number if maximum is None else min(maximum, number)


def as_list(value: Any, default: list) -> list:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return list(default)


def as_str(value: Any, default: str) -> str:
    return default if value is None else str(value).strip()


def as_choice(value: Any, choices: tuple[str, ...], what: str) -> str:
    chosen = as_str(value, choices[0]).lower() or choices[0]
    if chosen not in choices:
        raise ConfigError(f"{what} {chosen!r} must be one of {', '.join(choices)}")
    return chosen


def as_map(value: Any, what: str) -> dict[str, str]:
    """A ``label: profile`` map. Empty keys or values are a config error, not a silent skip."""
    if value is None or value == "":
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{what} must be a map of issue label to Hermes profile")
    result: dict[str, str] = {}
    for key, profile in value.items():
        label, name = str(key).strip(), as_str(profile, "")
        if not label or not name:
            raise ConfigError(f"{what}: entry {key!r} needs both a label and a profile")
        result[label] = name
    return result


def validate_contract(value: str) -> str:
    if value in ("", LOCAL_ONLY):
        return LOCAL_ONLY
    if REPO_RE.match(value) or PR_URL_RE.match(value):
        return value
    raise ConfigError(
        f"completion_contract {value!r} must be local-only, OWNER/REPO or a GitHub PR URL"
    )


def normalize_workspace(value: str) -> str:
    """A bare path means a worktree of that checkout; a Hermes workspace spec passes through."""
    if value.startswith(WORKSPACE_KINDS):
        return value
    return f"worktree:{value}"


def checkout_path(workspace: str) -> str:
    """The directory behind a ``worktree:<path>`` or ``dir:<path>`` spec, else empty."""
    for prefix in ("worktree:", "dir:"):
        if workspace.startswith(prefix):
            return workspace[len(prefix) :]
    return ""


def board_slug(name: str) -> str:
    """A Hermes board slug for a repository name: lowercase, ``[a-z0-9_-]``, 64 characters,
    starting with a letter or digit (the rules ``hermes kanban boards create`` enforces)."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:64].rstrip("-_")
    if not BOARD_SLUG_RE.match(slug):
        raise ConfigError(f"cannot derive a board slug from repository name {name!r}; set board")
    return slug


@dataclass(frozen=True)
class RepoSettings:
    repo: str
    assignee: str  # the fallback profile; empty when every card is routed by a lane label
    workspace: str
    completion_contract: str
    branch_prefix: str
    max_runtime: str
    board: str = ""  # board slug for this repository's cards; empty means the current board
    board_name: str = ""  # display name used when the board has to be created
    lanes: Mapping[str, str] = field(default_factory=dict)  # issue label -> Hermes profile

    @property
    def name(self) -> str:
        return self.repo.split("/", 1)[1]

    @property
    def checkout(self) -> str:
        return checkout_path(self.workspace)

    @property
    def identity_profile(self) -> str:
        """The profile whose gh identity reads the issue list: the assignee, else the lane whose
        label sorts first."""
        return self.assignee or (self.lanes[min(self.lanes)] if self.lanes else "")


@dataclass(frozen=True)
class Settings:
    repos: tuple[RepoSettings, ...]
    board_per_repo: bool
    board: str
    mirror: str
    epic_label: str
    placeholder_status: str
    placeholder_assignee: str
    ready_label: str
    in_progress_label: str
    blocked_label: str
    skip_labels: tuple[str, ...]
    max_cards_per_sync: int
    issue_limit: int
    sync_on_dispatch_tick: bool
    sync_interval_seconds: int
    reconcile: bool
    push_events: frozenset[str]
    summary_chars: int
    card_template: str
    created_by: str
    require_profile_login: bool
    command_timeout_seconds: int
    gh_bin: str
    hermes_bin: str

    def repo(self, slug: str) -> RepoSettings | None:
        wanted = slug.lower()
        return next((r for r in self.repos if r.repo.lower() == wanted), None)

    @property
    def repo_slugs(self) -> tuple[str, ...]:
        return tuple(r.repo for r in self.repos)

    @property
    def ready_only(self) -> bool:
        return self.mirror == "ready-only"

    @property
    def mirrors_epics(self) -> bool:
        return self.mirror == "full"


def _repo_board(entry: Mapping[str, Any], repo: str, get: Callable[[str], Any]) -> tuple[str, str]:
    """``(slug, display name)`` for the repository's board."""
    name = repo.split("/", 1)[1]
    display = as_str(entry.get("board_name"), "") or name
    explicit = as_str(entry.get("board"), "").lower()
    if explicit:
        if not BOARD_SLUG_RE.match(explicit):
            raise ConfigError(f"{repo}: board {explicit!r} is not a valid board slug")
        return explicit, display
    if as_bool(get("board_per_repo"), True):
        return board_slug(name), display
    return as_str(get("board"), "").lower(), display


def _repo_settings(entry: Any, get: Callable[[str], Any]) -> RepoSettings:
    if isinstance(entry, str):
        entry = {"repo": entry}
    if not isinstance(entry, Mapping):
        raise ConfigError(f"repos entries must be owner/repo strings or maps, got {entry!r}")
    repo = as_str(entry.get("repo"), "")
    if not REPO_RE.match(repo):
        raise ConfigError(f"repos entry {repo!r} is not owner/repo")
    assignee = as_str(entry.get("assignee"), "") or as_str(get("assignee"), "")
    lanes = {**as_map(get("lanes"), "lanes"), **as_map(entry.get("lanes"), f"{repo}: lanes")}
    if not assignee and not lanes:
        raise ConfigError(
            f"{repo}: no assignee profile; set assignee or lanes globally or on the repo"
        )
    workspace = as_str(entry.get("workspace"), "")
    root = as_str(get("workspace_root"), "")
    if workspace:
        workspace = normalize_workspace(workspace)
    elif root:
        workspace = f"worktree:{root.rstrip('/')}/{repo.split('/', 1)[1]}"
    else:
        raise ConfigError(f"{repo}: no workspace; set workspace on the repo or workspace_root")
    contract = validate_contract(
        as_str(entry.get("completion_contract"), "")
        or as_str(get("completion_contract"), LOCAL_ONLY)
    )
    max_runtime = as_str(entry.get("max_runtime"), "") or as_str(get("max_runtime"), "90m")
    if not DURATION_RE.match(max_runtime):
        raise ConfigError(f"{repo}: max_runtime {max_runtime!r} must look like 300, 90m or 2h")
    board, board_name = _repo_board(entry, repo, get)
    return RepoSettings(
        repo=repo,
        assignee=assignee,
        workspace=workspace,
        completion_contract=contract,
        branch_prefix=as_str(entry.get("branch_prefix"), "") or as_str(get("branch_prefix"), ""),
        max_runtime=max_runtime,
        board=board,
        board_name=board_name,
        lanes=lanes,
    )


def _bounded(get: Callable[[str], Any], key: str) -> int:
    low, high = BOUNDS[key]
    return as_int(get(key), DEFAULTS[key], minimum=low, maximum=high)


def load_settings(get_config: Callable[[str, Any], Any]) -> Settings:
    """Build validated settings from ``ctx.get_config``. Raises ``ConfigError`` on bad values."""

    def get(key: str) -> Any:
        value = get_config(key, DEFAULTS[key])
        return DEFAULTS[key] if value is None else value

    repos = tuple(_repo_settings(entry, get) for entry in as_list(get("repos"), []))
    slugs = [r.repo.lower() for r in repos]
    if len(set(slugs)) != len(slugs):
        raise ConfigError("repos lists the same repository twice")
    events = frozenset(str(e).strip().lower() for e in as_list(get("push_events"), list(EVENTS)))
    unknown = events - set(EVENTS)
    if unknown:
        raise ConfigError(f"push_events has unknown events {sorted(unknown)}; use {list(EVENTS)}")
    return Settings(
        repos=repos,
        board_per_repo=as_bool(get("board_per_repo"), True),
        board=as_str(get("board"), "").lower(),
        mirror=as_choice(get("mirror"), MIRROR_MODES, "mirror"),
        epic_label=as_str(get("epic_label"), "epic") or "epic",
        placeholder_status=as_choice(
            get("placeholder_status"), PLACEHOLDER_STATUSES, "placeholder_status"
        ),
        placeholder_assignee=as_str(get("placeholder_assignee"), "github") or "github",
        ready_label=as_str(get("ready_label"), "agent-ready") or "agent-ready",
        in_progress_label=as_str(get("in_progress_label"), "in-progress") or "in-progress",
        blocked_label=as_str(get("blocked_label"), "blocked") or "blocked",
        skip_labels=tuple(str(s).strip() for s in as_list(get("skip_labels"), ["epic"])),
        max_cards_per_sync=_bounded(get, "max_cards_per_sync"),
        issue_limit=_bounded(get, "issue_limit"),
        sync_on_dispatch_tick=as_bool(get("sync_on_dispatch_tick"), True),
        sync_interval_seconds=_bounded(get, "sync_interval_seconds"),
        reconcile=as_bool(get("reconcile"), True),
        push_events=events,
        summary_chars=_bounded(get, "summary_chars"),
        card_template=as_str(get("card_template"), ""),
        created_by=as_str(get("created_by"), "github-issues-sync") or "github-issues-sync",
        require_profile_login=as_bool(get("require_profile_login"), False),
        command_timeout_seconds=_bounded(get, "command_timeout_seconds"),
        gh_bin=as_str(get("gh_bin"), "gh") or "gh",
        hermes_bin=as_str(get("hermes_bin"), "hermes") or "hermes",
    )
