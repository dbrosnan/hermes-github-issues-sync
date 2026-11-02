"""What one sync did, for the slash command, the CLI and the log."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cards import IssueRef
from .selection import Skipped


@dataclass(frozen=True)
class Created:
    ref: IssueRef
    title: str
    card_id: str | None  # None in a dry run
    branch: str
    assignee: str


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
    repos: list[tuple[str, int]] = field(default_factory=list)
    created: list[Created] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    pushed: list[PushResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        posted = sum(1 for p in self.pushed if p.posted or p.action == "would post")
        verb = "would create" if self.dry_run else "created"
        return (
            f"{len(self.created)} {verb}, {len(self.skipped)} skipped, "
            f"{posted} posted, {len(self.errors)} errors"
        )

    def render(self) -> str:
        lines = ["github-issues-sync" + (" (dry run)" if self.dry_run else "")]
        for repo, count in self.repos:
            lines.append(
                f"{repo}: {count} open `{self.ready_label}` issue{'s' if count != 1 else ''}"
            )
        for item in self.created:
            verb = "would create" if self.dry_run else "created"
            where = f" -> {item.card_id}" if item.card_id else ""
            lines.append(
                f"  {verb}: {item.ref.short} {item.title.split(': ', 1)[-1]!r}"
                f" (branch {item.branch}, assignee {item.assignee}){where}"
            )
        for item in self.skipped:
            lines.append(f"  skipped {item.ref.short}: {item.reason}")
        for item in self.pushed:
            if item.action == "already posted":
                continue
            lines.append(f"  {item.action} {item.event} on {item.ref.short} (card {item.card_id})")
        if self.errors:
            lines.append("errors:")
            lines.extend(f"  {error}" for error in self.errors)
        lines.append("summary: " + self.summary())
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "repos": [{"repo": repo, "ready_issues": count} for repo, count in self.repos],
            "created": [
                {
                    "issue": c.ref.short,
                    "title": c.title,
                    "card_id": c.card_id,
                    "branch": c.branch,
                    "assignee": c.assignee,
                }
                for c in self.created
            ],
            "skipped": [{"issue": s.ref.short, "reason": s.reason} for s in self.skipped],
            "pushed": [
                {"issue": p.ref.short, "event": p.event, "action": p.action, "card_id": p.card_id}
                for p in self.pushed
            ],
            "errors": list(self.errors),
            "ok": self.ok,
        }
