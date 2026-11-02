"""Report card transitions on the issue: claimed, completed (handoff), blocked.

Every comment ends with a hidden marker naming the event, card and run, and a transition is
only posted when the issue has no comment with that marker yet. That makes the push safe to
repeat from any process: the hook in the worker, the hook in the dispatcher, or the reconcile
pass during a sync. The issue is never closed here; the PR's ``Fixes #n`` does that.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .board import Card, HermesBoard
from .cards import IssueRef, parse_card_ref
from .gh import GhClient
from .report import PushResult
from .settings import RepoSettings, Settings

logger = logging.getLogger("github_issues_sync")

PR_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*")


def marker(event: str, task_id: str, run_id: Any) -> str:
    return f"<!-- github-issues-sync: {event} {task_id} run={run_id if run_id else '-'} -->"


def has_marker(comments: tuple[str, ...], text: str) -> bool:
    return any(text in comment for comment in comments)


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def find_pr_url(summary: str, show: Mapping[str, Any], repo: str) -> str:
    """The PR this handoff produced: named in the summary, recorded on the run, or the
    card's own contract when it is a PR URL."""
    candidates = PR_URL_RE.findall(summary or "")
    for run in show.get("runs") or []:
        meta = run.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        if isinstance(meta, Mapping) and meta.get("published_pr"):
            candidates.append(str(meta["published_pr"]))
    contract = str((show.get("task") or {}).get("completion_contract") or "")
    if PR_URL_RE.fullmatch(contract):
        candidates.append(contract)
    own = [url for url in candidates if url.startswith(f"https://github.com/{repo}/")]
    return (own or candidates or [""])[0]


def latest_run_id(show: Mapping[str, Any]) -> int | None:
    ids = [run.get("id") for run in show.get("runs") or [] if isinstance(run.get("id"), int)]
    return max(ids) if ids else None


def last_block_reason(show: Mapping[str, Any]) -> str:
    for event in reversed(show.get("events") or []):
        if event.get("kind") != "blocked":
            continue
        payload = event.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return payload
        if isinstance(payload, Mapping):
            return str(payload.get("reason") or "")
    return ""


@dataclass(frozen=True)
class Resolved:
    ref: IssueRef
    repo: RepoSettings
    task: Mapping[str, Any]
    show: Mapping[str, Any]
    board: HermesBoard


class Pusher:
    def __init__(
        self,
        settings: Settings,
        gh: GhClient,
        board: HermesBoard,
        env_for: Callable[[str], Mapping[str, str]],
        clock: Callable[[], float] = time.time,
    ):
        self._settings = settings
        self._gh = gh
        self._board = board
        self._env_for = env_for
        self._clock = clock

    def resolve(self, task_id: str, board: str | None = None) -> Resolved | None:
        """The issue behind a card, or None when the card is not a mirror of a configured repo."""
        client = self._board.with_board(board)
        show = client.show(task_id)
        task = show.get("task") or {}
        ref = parse_card_ref(str(task.get("title") or ""), str(task.get("body") or ""))
        if ref is None:
            logger.debug("github-issues-sync: %s is not a mirrored card", task_id)
            return None
        repo = self._settings.repo(ref.repo)
        if repo is None:
            logger.info(
                "github-issues-sync: %s mirrors %s, which is not configured", task_id, ref.short
            )
            return None
        return Resolved(ref, repo, task, show, client)

    def _stamp(self) -> str:
        return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(self._clock()))

    def claimed(
        self,
        task_id: str,
        run_id: Any = None,
        *,
        assignee: str = "",
        board: str | None = None,
        dry_run: bool = False,
    ) -> PushResult | None:
        resolved = self.resolve(task_id, board)
        if resolved is None:
            return None
        who = assignee or str(resolved.task.get("assignee") or resolved.repo.assignee)
        body = (
            f"Claimed by Hermes profile `{who}` on {self._stamp()} "
            f"(card `{task_id}`, run {run_id or '-'})."
        )
        s = self._settings
        return self._post(
            resolved,
            "claimed",
            run_id,
            body,
            who,
            dry_run,
            add=(s.in_progress_label,),
            remove=(s.blocked_label,),
        )

    def completed(
        self,
        task_id: str,
        run_id: Any = None,
        *,
        summary: str = "",
        assignee: str = "",
        board: str | None = None,
        dry_run: bool = False,
    ) -> PushResult | None:
        resolved = self.resolve(task_id, board)
        if resolved is None:
            return None
        who = assignee or str(resolved.task.get("assignee") or resolved.repo.assignee)
        summary = summary or str(resolved.show.get("latest_summary") or "")
        pr_url = find_pr_url(summary, resolved.show, resolved.ref.repo)
        parts = [
            f"Handoff from Hermes profile `{who}` on {self._stamp()} "
            f"(card `{task_id}`, run {run_id or '-'}).",
            clip(summary, self._settings.summary_chars) or "_No handoff summary._",
        ]
        if pr_url:
            parts.append(
                f"PR: {pr_url}\n\nThis issue stays open until that PR merges; "
                f"its `Fixes #{resolved.ref.number}` closes it."
            )
        else:
            parts.append("No PR URL was found in the handoff. The issue stays open.")
        return self._post(resolved, "completed", run_id, "\n\n".join(parts), who, dry_run)

    def blocked(
        self,
        task_id: str,
        run_id: Any = None,
        *,
        reason: str = "",
        assignee: str = "",
        board: str | None = None,
        dry_run: bool = False,
    ) -> PushResult | None:
        resolved = self.resolve(task_id, board)
        if resolved is None:
            return None
        who = assignee or str(resolved.task.get("assignee") or resolved.repo.assignee)
        reason = reason or last_block_reason(resolved.show)
        body = (
            f"Blocked by Hermes profile `{who}` on {self._stamp()} "
            f"(card `{task_id}`, run {run_id or '-'}).\n\n"
            + (clip(reason, self._settings.summary_chars) or "_No reason given._")
        )
        s = self._settings
        return self._post(
            resolved,
            "blocked",
            run_id,
            body,
            who,
            dry_run,
            add=(s.blocked_label,),
            remove=(s.in_progress_label,),
        )

    def reconcile(self, card: Card, *, dry_run: bool = False) -> PushResult | None:
        """Post whatever a card's current state implies, if it has not been posted yet."""
        if card.status == "running":
            return self._reconcile_with(self.claimed, card)
        if card.status in ("review", "done"):
            return self._reconcile_with(self.completed, card)
        if card.status == "blocked":
            return self._reconcile_with(self.blocked, card)
        return None

    def _reconcile_with(
        self, method: Callable[..., PushResult | None], card: Card
    ) -> PushResult | None:
        show = self._board.show(card.id)
        return method(card.id, latest_run_id(show), assignee=card.assignee)

    def _post(
        self,
        resolved: Resolved,
        event: str,
        run_id: Any,
        body: str,
        who: str,
        dry_run: bool,
        *,
        add: tuple[str, ...] = (),
        remove: tuple[str, ...] = (),
    ) -> PushResult:
        ref, task_id = resolved.ref, str(resolved.task.get("id") or "")
        if event not in self._settings.push_events:
            return PushResult(ref, event, "skipped: event disabled in push_events", task_id)
        env = self._env_for(who)
        state = self._gh.issue_state(ref.repo, ref.number, env=env)
        mark = marker(event, task_id, run_id)
        if has_marker(state.comments, mark):
            return PushResult(ref, event, "already posted", task_id)
        if dry_run:
            return PushResult(ref, event, "would post", task_id)
        present = {label.lower() for label in state.labels}
        self._gh.edit_labels(
            ref.repo,
            ref.number,
            add=tuple(label for label in add if label.lower() not in present),
            remove=tuple(label for label in remove if label.lower() in present),
            env=env,
        )
        url = self._gh.comment(ref.repo, ref.number, f"{body}\n\n{mark}", env=env)
        try:
            resolved.board.comment(task_id, f"Posted {event} on {url or ref.url}")
        except Exception as exc:  # the issue comment is the record; the card note is a courtesy
            logger.debug(
                "github-issues-sync: could not note %s on card %s: %s", event, task_id, exc
            )
        logger.info("github-issues-sync: posted %s on %s for card %s", event, ref.short, task_id)
        return PushResult(ref, event, "posted", task_id)
