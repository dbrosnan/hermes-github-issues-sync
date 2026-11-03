"""What one sync did, for the slash command, the CLI and the log."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cards import IssueRef
from .mirror import Transition
from .selection import Skipped


@dataclass(frozen=True)
class Created:
    ref: IssueRef
    title: str
    card_id: str | None  # None in a dry run
    branch: str
    assignee: str
    kind: str = "task"
    board: str = ""
    parents: tuple[str, ...] = ()  # card ids an epic card waits on


@dataclass(frozen=True)
class PushResult:
    ref: IssueRef
    event: str
    action: str  # "posted", "already posted", "would post", or "skipped: <why>"
    card_id: str

    @property
    def posted(self) -> bool:
        return self.action == "posted"


@dataclass
class SyncReport:
    dry_run: bool = False
    ready_label: str = "agent-ready"
    mirror: str = "ready-only"
    repos: list[tuple[str, int]] = field(default_factory=list)
    boards: list[tuple[str, str]] = field(default_factory=list)  # (slug, "created"/"would create")
    created: list[Created] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    pushed: list[PushResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def created_of(self, kind: str) -> int:
        return sum(1 for c in self.created if c.kind == kind)

    def summary(self) -> str:
        posted = sum(1 for p in self.pushed if p.posted or p.action == "would post")
        verb = "would create" if self.dry_run else "created"
        counts = f"{len(self.created)} {verb}"
        if self.mirror != "ready-only":
            counts += (
                f" ({self.created_of('task')} tasks, {self.created_of('placeholder')} "
                f"placeholders, {self.created_of('epic')} epics)"
            )
        return (
            f"{counts}, {len(self.transitions)} moved, {len(self.skipped)} skipped, "
            f"{posted} posted, {len(self.errors)} errors"
        )

    def _issue_line(self, repo: str, count: int) -> str:
        if self.mirror == "ready-only":
            return f"{repo}: {count} open `{self.ready_label}` issue{'s' if count != 1 else ''}"
        return f"{repo}: {count} open issue{'s' if count != 1 else ''} ({self.mirror} mirror)"

    def render(self) -> str:
        lines = ["github-issues-sync" + (" (dry run)" if self.dry_run else "")]
        for slug, action in self.boards:
            lines.append(f"{action} board `{slug}`")
        for repo, count in self.repos:
            lines.append(self._issue_line(repo, count))
        verb = "would create" if self.dry_run else "created"
        for item in self.created:
            where = f" -> {item.card_id}" if item.card_id else ""
            on_board = f" on `{item.board}`" if item.board else ""
            if item.kind == "task":
                what = f"(branch {item.branch}, assignee {item.assignee})"
            elif item.kind == "epic":
                what = f"(epic, waits on {len(item.parents)} sub-issue cards)"
            else:
                what = f"(placeholder: {item.assignee})"
            kind = "" if self.mirror == "ready-only" else f" {item.kind}"
            lines.append(
                f"  {verb}{kind}: {item.ref.short} {item.title.split(': ', 1)[-1]!r}"
                f" {what}{on_board}{where}"
            )
        moved = "would " if self.dry_run else ""
        for item in self.transitions:
            lines.append(
                f"  {moved}{item.action} {item.ref.short} (card {item.card_id}): {item.detail}"
            )
        for item in self.skipped:
            lines.append(f"  skipped {item.ref.short}: {item.reason}")
        for item in self.pushed:
            if item.action == "already posted":
                continue
            lines.append(f"  {item.action} {item.event} on {item.ref.short} (card {item.card_id})")
        for warning in self.warnings:
            lines.append(f"warning: {warning}")
        if self.errors:
            lines.append("errors:")
            lines.extend(f"  {error}" for error in self.errors)
        lines.append("summary: " + self.summary())
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "mirror": self.mirror,
            "repos": [{"repo": repo, "open_issues": count} for repo, count in self.repos],
            "boards": [{"board": slug, "action": action} for slug, action in self.boards],
            "created": [
                {
                    "issue": c.ref.short,
                    "title": c.title,
                    "card_id": c.card_id,
                    "branch": c.branch,
                    "assignee": c.assignee,
                    "kind": c.kind,
                    "board": c.board,
                    "parents": list(c.parents),
                }
                for c in self.created
            ],
            "transitions": [
                {
                    "issue": t.ref.short,
                    "action": t.action,
                    "card_id": t.card_id,
                    "detail": t.detail,
                }
                for t in self.transitions
            ],
            "skipped": [{"issue": s.ref.short, "reason": s.reason} for s in self.skipped],
            "pushed": [
                {"issue": p.ref.short, "event": p.event, "action": p.action, "card_id": p.card_id}
                for p in self.pushed
            ],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "ok": self.ok,
        }
