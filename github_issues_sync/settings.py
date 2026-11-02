"""Settings under ``plugins.entries.github-issues-sync.settings``, read on every run.

Reading at run time rather than at ``register()`` means a config edit applies to the next sync
without restarting the gateway, and keeps ``register()`` free of work.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

LOCAL_ONLY = "local-only"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PR_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$")
DURATION_RE = re.compile(r"^[1-9][0-9]*(s|m|h|d)?$")
WORKSPACE_KINDS = ("scratch", "worktree", "worktree:", "dir:")
EVENTS = ("claimed", "completed", "blocked")
FALSE_WORDS = {"", "0", "false", "off", "no"}

DEFAULTS: dict[str, Any] = {
    "repos": [],
    "assignee": "",
    "workspace_root": "",
    "board": "",
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


class ConfigError(ValueError):
    """A setting that cannot be used. The message is written for the person editing config.yaml."""


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in FALSE_WORDS
    return default if value is None else bool(value)


def as_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def as_list(value: Any, default: list) -> list:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return list(default)


def as_str(value: Any, default: str) -> str:
    return default if value is None else str(value).strip()


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


@dataclass(frozen=True)
class RepoSettings:
    repo: str
    assignee: str
    workspace: str
    completion_contract: str
    branch_prefix: str
    max_runtime: str

    @property
    def name(self) -> str:
        return self.repo.split("/", 1)[1]


@dataclass(frozen=True)
class Settings:
    repos: tuple[RepoSettings, ...]
    board: str
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


def _repo_settings(entry: Any, get: Callable[[str], Any]) -> RepoSettings:
    if isinstance(entry, str):
        entry = {"repo": entry}
    if not isinstance(entry, Mapping):
        raise ConfigError(f"repos entries must be owner/repo strings or maps, got {entry!r}")
    repo = as_str(entry.get("repo"), "")
    if not REPO_RE.match(repo):
        raise ConfigError(f"repos entry {repo!r} is not owner/repo")
    assignee = as_str(entry.get("assignee"), "") or as_str(get("assignee"), "")
    if not assignee:
        raise ConfigError(f"{repo}: no assignee profile; set assignee globally or on the repo")
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
    return RepoSettings(
        repo=repo,
        assignee=assignee,
        workspace=workspace,
        completion_contract=contract,
        branch_prefix=as_str(entry.get("branch_prefix"), "") or as_str(get("branch_prefix"), ""),
        max_runtime=max_runtime,
    )


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
        board=as_str(get("board"), ""),
        ready_label=as_str(get("ready_label"), "agent-ready") or "agent-ready",
        in_progress_label=as_str(get("in_progress_label"), "in-progress") or "in-progress",
        blocked_label=as_str(get("blocked_label"), "blocked") or "blocked",
        skip_labels=tuple(str(s).strip() for s in as_list(get("skip_labels"), ["epic"])),
        max_cards_per_sync=as_int(get("max_cards_per_sync"), 10),
        issue_limit=as_int(get("issue_limit"), 100, minimum=1),
        sync_on_dispatch_tick=as_bool(get("sync_on_dispatch_tick"), True),
        sync_interval_seconds=as_int(get("sync_interval_seconds"), 300),
        reconcile=as_bool(get("reconcile"), True),
        push_events=events,
        summary_chars=as_int(get("summary_chars"), 2000),
        card_template=as_str(get("card_template"), ""),
        created_by=as_str(get("created_by"), "github-issues-sync") or "github-issues-sync",
        require_profile_login=as_bool(get("require_profile_login"), False),
        command_timeout_seconds=as_int(get("command_timeout_seconds"), 30, minimum=1),
        gh_bin=as_str(get("gh_bin"), "gh") or "gh",
        hermes_bin=as_str(get("hermes_bin"), "hermes") or "hermes",
    )
