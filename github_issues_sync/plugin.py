"""Wires settings, clients, hooks and commands together. One instance per loaded plugin."""

from __future__ import annotations

import argparse
import logging
import shlex
import time
from collections.abc import Callable
from typing import Any

from .board import BoardError, HermesBoard
from .gh import GhClient, GhError
from .profile_env import ProfileLoginError, gh_env_for_profile
from .pull import Puller
from .push import Pusher
from .report import SyncReport
from .runtime import Runtime, Throttle
from .settings import REPO_RE, ConfigError, Settings, load_settings

logger = logging.getLogger("github_issues_sync")

SLASH_USAGE = (
    "Usage: /gh-sync [--dry-run] [owner/repo]\n"
    "Mirror agent-ready GitHub issues onto the kanban board and post missed card events "
    "back on the issues. --dry-run reports without creating or posting anything."
)
PUSH_ERRORS = (ConfigError, GhError, BoardError, ProfileLoginError)
CLI_USAGE = (
    "usage: hermes github-issues-sync <sync [--dry-run] [--repo owner/repo] [--json] | status>"
)


class Plugin:
    def __init__(
        self,
        ctx: Any,
        *,
        runtime: Runtime | None = None,
        clock: Callable[[], float] = time.monotonic,
        env_for: Callable[..., dict[str, str]] = gh_env_for_profile,
    ):
        self._ctx = ctx
        self._runtime = runtime or Runtime()
        self._throttle = Throttle(clock)
        self._env_for = env_for

    # -- wiring -------------------------------------------------------------------------------

    def settings(self) -> Settings:
        return load_settings(self._ctx.get_config)

    def build(self, settings: Settings) -> tuple[Puller, Pusher]:
        timeout = settings.command_timeout_seconds
        gh = GhClient(settings.gh_bin, timeout=timeout)
        board = HermesBoard(
            settings.hermes_bin,
            settings.board,
            timeout=max(60, timeout * 2),
            created_by=settings.created_by,
        )

        def env_for(profile: str) -> dict[str, str]:
            return self._env_for(profile, require_login=settings.require_profile_login)

        pusher = Pusher(settings, gh, board, env_for)
        return Puller(settings, gh, board, pusher, env_for), pusher

    def sync(self, *, dry_run: bool = False, only_repo: str | None = None) -> SyncReport:
        try:
            settings = self.settings()
        except ConfigError as exc:
            return SyncReport(dry_run=dry_run, errors=[str(exc)])
        puller, _pusher = self.build(settings)
        report = puller.run(dry_run=dry_run, only_repo=only_repo)
        if not dry_run:
            self._remember(report)
        return report

    def _remember(self, report: SyncReport) -> None:
        try:
            self._ctx.state.set(
                "last_sync",
                {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "summary": report.summary(),
                    "errors": report.errors[:10],
                },
            )
        except Exception as exc:  # state is a convenience for `status`, never a dependency
            logger.debug("github-issues-sync: could not record last sync: %s", exc)

    # -- hooks --------------------------------------------------------------------------------

    def on_dispatch_tick(self, *, dry_run: bool = False, **_: Any) -> None:
        if dry_run:
            return
        try:
            settings = self.settings()
        except ConfigError as exc:
            logger.warning("github-issues-sync: %s", exc)
            return
        if not settings.sync_on_dispatch_tick or not settings.repos:
            return
        if self._throttle.allow(settings.sync_interval_seconds):
            self._runtime.submit(self._background_sync)

    def _background_sync(self) -> None:
        report = self.sync()
        level = logging.WARNING if report.errors else logging.INFO
        logger.log(level, "github-issues-sync: %s", report.summary())
        for error in report.errors:
            logger.warning("github-issues-sync: %s", error)

    def on_claimed(
        self,
        *,
        task_id: str = "",
        run_id: Any = None,
        assignee: str | None = None,
        board: str | None = None,
        **_: Any,
    ) -> None:
        if task_id:  # inside the dispatch lock: hand off and return at once
            self._runtime.submit(self.push, "claimed", task_id, run_id, assignee or "", board, {})

    def on_completed(
        self,
        *,
        task_id: str = "",
        run_id: Any = None,
        assignee: str | None = None,
        board: str | None = None,
        summary: str = "",
        **_: Any,
    ) -> None:
        if task_id:  # the worker may exit right after this hook, so post now
            self.push(
                "completed", task_id, run_id, assignee or "", board, {"summary": summary or ""}
            )

    def on_blocked(
        self,
        *,
        task_id: str = "",
        run_id: Any = None,
        assignee: str | None = None,
        board: str | None = None,
        reason: str = "",
        **_: Any,
    ) -> None:
        if task_id:
            self.push("blocked", task_id, run_id, assignee or "", board, {"reason": reason or ""})

    def push(
        self,
        event: str,
        task_id: str,
        run_id: Any,
        assignee: str,
        board: str | None,
        extra: dict[str, str],
    ) -> None:
        try:
            settings = self.settings()
            if event not in settings.push_events or not settings.repos:
                return
            _puller, pusher = self.build(settings)
            method = getattr(pusher, event)
            result = method(task_id, run_id, assignee=assignee, board=board, **extra)
        except PUSH_ERRORS as exc:
            logger.warning("github-issues-sync: %s for card %s not posted: %s", event, task_id, exc)
            return
        if result is not None:
            logger.info("github-issues-sync: %s %s on %s", result.action, event, result.ref.short)

    # -- commands -----------------------------------------------------------------------------

    def slash(self, raw_args: str = "") -> str:
        try:
            tokens = shlex.split(raw_args or "")
        except ValueError:
            return SLASH_USAGE
        dry_run, only_repo = False, None
        for token in tokens:
            if token in ("--dry-run", "dry-run", "dry", "-n"):
                dry_run = True
            elif REPO_RE.match(token):
                only_repo = token
            else:
                return SLASH_USAGE
        return self.sync(dry_run=dry_run, only_repo=only_repo).render()

    def cli_setup(self, subparser: argparse.ArgumentParser) -> None:
        subs = subparser.add_subparsers(dest="ghis_command")
        sync = subs.add_parser(
            "sync", help="Mirror ready issues onto the board and reconcile cards"
        )
        sync.add_argument(
            "--dry-run", "-n", action="store_true", help="Report only; change nothing"
        )
        sync.add_argument("--repo", help="Only this owner/repo")
        sync.add_argument("--json", action="store_true", help="Machine-readable output")
        subs.add_parser("status", help="Show the configuration and the last sync")
        subparser.set_defaults(func=self.cli)

    def cli(self, args: argparse.Namespace) -> int:
        command = getattr(args, "ghis_command", None)
        if command == "sync":
            report = self.sync(dry_run=args.dry_run, only_repo=args.repo)
            if args.json:
                import json

                print(json.dumps(report.to_dict(), indent=2))
            else:
                print(report.render())
            return 0 if report.ok else 1
        if command == "status":
            print(self.status_text())
            return 0
        print(CLI_USAGE)
        return 2

    def status_text(self) -> str:
        try:
            s = self.settings()
        except ConfigError as exc:
            return f"configuration error: {exc}"
        lines = ["github-issues-sync"]
        if not s.repos:
            lines.append("repos: none configured")
        for repo in s.repos:
            lines.append(
                f"repo {repo.repo}: assignee {repo.assignee or '(by lane only)'}, "
                f"workspace {repo.workspace}, branch {repo.branch_prefix}<n>, "
                f"contract {repo.completion_contract}, max runtime {repo.max_runtime}"
            )
            if repo.lanes:
                routes = ", ".join(f"`{label}` -> {prof}" for label, prof in repo.lanes.items())
                lines.append(f"  lanes: {routes}")
        lines.append(
            f"labels: ready `{s.ready_label}`, in progress `{s.in_progress_label}`, "
            f"blocked `{s.blocked_label}`, skip {list(s.skip_labels)}"
        )
        lines.append(
            f"sync on dispatcher ticks: {'yes' if s.sync_on_dispatch_tick else 'no'} "
            f"(every {s.sync_interval_seconds}s), reconcile: {'yes' if s.reconcile else 'no'}, "
            f"push events: {sorted(s.push_events)}"
        )
        try:
            last = self._ctx.state.get("last_sync", None)
        except Exception:
            last = None
        if isinstance(last, dict):
            lines.append(f"last sync: {last.get('at')} ({last.get('summary')})")
            lines.extend(f"  {error}" for error in last.get("errors") or [])
        else:
            lines.append("last sync: none recorded in this profile")
        return "\n".join(lines)

    def shutdown(self) -> None:
        self._runtime.shutdown()
