"""Which ready issues become cards. Pure functions, no I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .board import Card
from .cards import IssueRef
from .gh import Issue
from .settings import Settings


@dataclass(frozen=True)
class Skipped:
    ref: IssueRef
    reason: str


def select_candidates(
    issues: list[Issue], settings: Settings, mirrored: Mapping[IssueRef, Card]
) -> tuple[list[Issue], list[Skipped]]:
    """Split ``issues`` into the ones that get a card and the ones that do not, with reasons."""
    candidates: list[Issue] = []
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
            candidates.append(issue)
    return candidates, skipped
