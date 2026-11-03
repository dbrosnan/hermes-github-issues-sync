"""Which ready issues become cards. Pure functions, no I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .board import Card
from .cards import IssueRef
from .gh import Issue
from .settings import RepoSettings, Settings


@dataclass(frozen=True)
class Skipped:
    ref: IssueRef
    reason: str


@dataclass(frozen=True)
class Candidate:
    issue: Issue
    assignee: str  # the Hermes profile the card goes to, after lane routing


def lane_assignee(issue: Issue, repo: RepoSettings) -> tuple[str, str | None]:
    """The profile for ``issue``: its single lane label's profile, else the repo assignee.

    Returns ``(profile, None)`` or ``("", reason)`` when no profile can be chosen without guessing.
    """
    lanes = {label.lower(): profile for label, profile in repo.lanes.items()}
    hits = sorted(label.lower() for label in issue.labels if label.lower() in lanes)
    if len(hits) > 1:
        return "", "has lane labels " + ", ".join(f"`{h}`" for h in hits) + "; keep one"
    if hits:
        return lanes[hits[0]], None
    if repo.assignee:
        return repo.assignee, None
    return "", "has no lane label and the repository sets no assignee"


def select_candidates(
    issues: list[Issue],
    repo: RepoSettings,
    settings: Settings,
    mirrored: Mapping[IssueRef, Card],
) -> tuple[list[Candidate], list[Skipped]]:
    """Split ``issues`` into the ones that get a card and the ones that do not, with reasons."""
    candidates: list[Candidate] = []
    skipped: list[Skipped] = []
    skip_labels = {label.lower() for label in settings.skip_labels}
    in_progress = settings.in_progress_label.lower()
    for issue in issues:
        ref = IssueRef(issue.repo, issue.number)
        labels = {label.lower() for label in issue.labels}
        hit = sorted(labels & skip_labels)
        if hit:
            skipped.append(Skipped(ref, f"labelled `{hit[0]}`"))
        elif in_progress in labels:
            skipped.append(Skipped(ref, f"labelled `{settings.in_progress_label}`"))
        elif issue.open_blockers:
            blockers = ", ".join(f"#{n}" for n in issue.open_blockers)
            skipped.append(Skipped(ref, f"blocked by open {blockers}"))
        elif ref in mirrored:
            card = mirrored[ref]
            skipped.append(Skipped(ref, f"card {card.id} exists ({card.status})"))
        else:
            assignee, why = lane_assignee(issue, repo)
            if why:
                skipped.append(Skipped(ref, why))
            else:
                candidates.append(Candidate(issue, assignee))
    return candidates, skipped
