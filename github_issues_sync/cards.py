"""Card <-> issue mapping and the card bodies.

The title ``owner/repo#12: ...`` and the ``Source issue:`` line in the body are how a card is
recognised as a mirror later, in a process that only has the task id. Both are parsed; the
body line wins because a human may retitle a card. A hidden ``kind=`` marker says whether the
card is a work card, a placeholder or an epic; a card without one (v0.2 and earlier) is a
work card.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .gh import Issue
from .settings import RepoSettings, Settings

logger = logging.getLogger("github_issues_sync")

TITLE_RE = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<number>[1-9][0-9]*):")
SOURCE_RE = re.compile(
    r"^Source issue: https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"/issues/(?P<number>[1-9][0-9]*)\s*$",
    re.MULTILINE,
)
KIND_RE = re.compile(r"<!-- github-issues-sync: kind=(?P<kind>task|placeholder|epic) -->")
MAX_TITLE = 200

TASK = "task"
PLACEHOLDER = "placeholder"
EPIC = "epic"
KINDS = (TASK, PLACEHOLDER, EPIC)

DEFAULT_TEMPLATE = """Source issue: {url}
Repository: {repo} (workspace {workspace})

Rules for this card:
- Issue #{number} is labelled `{in_progress_label}` for you when this card is claimed.
- Work on branch `{branch}`, already checked out in your workspace. Open a PR against the
  default branch whose body contains `Fixes #{number}`. Use that closing keyword only for
  this issue.
- Comment on issue #{number} with the PR URL and evidence (test output, screenshot). Do not
  close the issue by hand; the merged PR closes it.
- If you cannot finish, block this card with the reason. Issue #{number} is then labelled
  `{blocked_label}` and the reason is posted there.
- Run the verification command named in the issue before requesting review.

---

{body}
"""

PLACEHOLDER_TEMPLATE = """Source issue: {url}
Repository: {repo}
Stage on GitHub: {stage}

This card mirrors the issue so the board shows the same work as GitHub. It is not a task for a
worker: its assignee is not a Hermes profile, so the dispatcher leaves it alone. When the issue
is labelled `{ready_label}` on GitHub, the next sync replaces this card with a work card.

---

{body}
"""

EPIC_TEMPLATE = """Source issue: {url}
Repository: {repo}
Epic with {count} open sub-issue{plural} on GitHub.

This card waits on the cards of its sub-issues, the way the epic waits on them on GitHub, and
is never dispatched: its assignee is not a Hermes profile. When GitHub closes the epic, the
next sync archives this card.

---

{body}
"""


@dataclass(frozen=True)
class IssueRef:
    repo: str
    number: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/issues/{self.number}"

    @property
    def short(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def idempotency_key(self) -> str:
        return f"gh-{self.repo.replace('/', '-')}-{self.number}"


def card_title(issue: Issue) -> str:
    title = f"{issue.repo}#{issue.number}: {issue.title or 'untitled'}"
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1] + "…"


def parse_card_ref(title: str, body: str) -> IssueRef | None:
    match = SOURCE_RE.search(body or "") or TITLE_RE.match(title or "")
    if not match:
        return None
    return IssueRef(repo=match.group("repo"), number=int(match.group("number")))


def card_kind(body: str) -> str:
    match = KIND_RE.search(body or "")
    return match.group("kind") if match else TASK


def kind_marker(kind: str) -> str:
    return f"<!-- github-issues-sync: kind={kind} -->"


def branch_for(repo: RepoSettings, number: int) -> str:
    return f"{repo.branch_prefix}{number}"


def _with_source(rendered: str, issue: Issue, kind: str) -> str:
    """The body with the mapping line first and the kind marker right under it."""
    if not SOURCE_RE.search(rendered):
        rendered = f"Source issue: {issue.url}\n\n{rendered}"  # the mapping line is not optional
    end = SOURCE_RE.search(rendered).end()
    return f"{rendered[:end]}\n{kind_marker(kind)}{rendered[end:]}"


def render_card_body(
    issue: Issue, repo: RepoSettings, settings: Settings, *, assignee: str | None = None
) -> str:
    fields: dict[str, Any] = {
        "url": issue.url,
        "repo": issue.repo,
        "number": issue.number,
        "title": issue.title,
        "body": issue.body.strip(),
        "branch": branch_for(repo, issue.number),
        "workspace": repo.workspace,
        "assignee": repo.assignee if assignee is None else assignee,
        "in_progress_label": settings.in_progress_label,
        "blocked_label": settings.blocked_label,
    }
    template = settings.card_template or DEFAULT_TEMPLATE
    try:
        rendered = template.format_map(fields)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("github-issues-sync: card_template is invalid (%s); using the default", exc)
        rendered = DEFAULT_TEMPLATE.format_map(fields)
    return _with_source(rendered, issue, TASK)


def render_placeholder_body(issue: Issue, settings: Settings, stage: str) -> str:
    rendered = PLACEHOLDER_TEMPLATE.format_map(
        {
            "url": issue.url,
            "repo": issue.repo,
            "stage": stage,
            "ready_label": settings.ready_label,
            "body": issue.body.strip(),
        }
    )
    return _with_source(rendered, issue, PLACEHOLDER)


def render_epic_body(issue: Issue, open_children: int) -> str:
    rendered = EPIC_TEMPLATE.format_map(
        {
            "url": issue.url,
            "repo": issue.repo,
            "count": open_children,
            "plural": "" if open_children == 1 else "s",
            "body": issue.body.strip(),
        }
    )
    return _with_source(rendered, issue, EPIC)
