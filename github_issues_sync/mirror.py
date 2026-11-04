"""What the board should look like for a repository's open issues, and what to do to get there.

Pure functions, no I/O. ``desired_state`` maps one issue to the card it should have;
``plan_mirror`` compares that with the cards that exist and lists the changes a sync applies.

Card kinds:

- ``task``: a work card the dispatcher may spawn (an open issue carrying the ready label).
- ``placeholder``: every other open non-epic issue (``needs-spec``, ``needs-human``, ``triage``,
  or in progress outside Hermes). Never dispatched: its assignee is not a Hermes profile.
- ``epic``: an issue carrying the epic label, in ``full`` mode. Linked as the *child* of its
  sub-issues' cards, because a Hermes parent link means "finish before the child starts": the
  epic card waits in ``todo`` until the sub-issue cards are done, as the epic waits on GitHub.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .board import Card
from .cards import EPIC, PLACEHOLDER, TASK, IssueRef, card_kind
from .gh import Issue
from .selection import Skipped, card_load, lane_assignee, with_card
from .settings import RepoSettings, Settings


@dataclass(frozen=True)
class Desired:
    issue: Issue
    kind: str  # TASK, PLACEHOLDER or EPIC
    assignee: str
    blocked: bool = False  # a task card that must sit in blocked, with ``reason``
    reason: str = ""  # the stage shown on a placeholder, or why a task is blocked

    @property
    def ref(self) -> IssueRef:
        return IssueRef(self.issue.repo, self.issue.number)


@dataclass(frozen=True)
class Transition:
    ref: IssueRef
    action: str  # archive, block, unblock, link, replace
    card_id: str
    detail: str = ""


@dataclass
class Plan:
    creates: list[Desired] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    # epic issue number -> sub-issue numbers whose cards gate the epic card
    children: dict[int, tuple[int, ...]] = field(default_factory=dict)


def _stage(issue: Issue, settings: Settings) -> str:
    """The label that names the issue's stage, for the placeholder body and the report."""
    known = (settings.in_progress_label, settings.blocked_label)
    for label in issue.labels:
        if label.lower() in {k.lower() for k in known}:
            return label
    skipped = {label.lower() for label in settings.skip_labels}
    named = [label for label in issue.labels if not label.lower().startswith("lane:")]
    named = [label for label in named if label.lower() not in skipped]
    return named[0] if named else "open"


def desired_state(
    issue: Issue,
    repo: RepoSettings,
    settings: Settings,
    load: Mapping[str, int] | None = None,
) -> Desired | Skipped:
    """The card ``issue`` should have under the ``tasks`` and ``full`` mirror modes."""
    ref = IssueRef(issue.repo, issue.number)
    if settings.mirrors_epics and issue.has_label(settings.epic_label):
        return Desired(issue, EPIC, settings.placeholder_assignee)
    skip_labels = {label.lower() for label in settings.skip_labels}
    hit = sorted(label for label in (name.lower() for name in issue.labels) if label in skip_labels)
    if hit:
        return Skipped(ref, f"labelled `{hit[0]}`")
    if issue.has_label(settings.in_progress_label):
        return Desired(issue, PLACEHOLDER, settings.placeholder_assignee, reason="in progress")
    if not issue.has_label(settings.ready_label):
        return Desired(
            issue, PLACEHOLDER, settings.placeholder_assignee, reason=_stage(issue, settings)
        )
    assignee, why = lane_assignee(issue, repo, load)
    if why:
        return Skipped(ref, why)
    if issue.open_blockers:
        blockers = ", ".join(f"#{n}" for n in issue.open_blockers)
        return Desired(issue, TASK, assignee, blocked=True, reason=f"blocked by open {blockers}")
    if issue.has_label(settings.blocked_label):
        return Desired(
            issue, TASK, assignee, blocked=True, reason=f"labelled `{settings.blocked_label}`"
        )
    return Desired(issue, TASK, assignee)


def _epic_children(issues: list[Issue], settings: Settings) -> dict[int, tuple[int, ...]]:
    epics = {i.number for i in issues if i.has_label(settings.epic_label)}
    children: dict[int, list[int]] = {number: [] for number in epics}
    for issue in issues:
        if issue.parent in children and issue.number != issue.parent:
            children[issue.parent].append(issue.number)
    return {number: tuple(sorted(subs)) for number, subs in children.items()}


def _reconcile_existing(
    wanted: Desired, card: Card, settings: Settings, plan: Plan
) -> Desired | None:
    """Transitions for an issue that already has a card; the Desired to create when the card
    has to be replaced, else None."""
    ref, have = wanted.ref, card_kind(card.body)
    if card.busy:
        plan.skipped.append(Skipped(ref, f"card {card.id} exists ({card.status})"))
        return None
    if have != wanted.kind:
        plan.transitions.append(
            Transition(ref, "replace", card.id, f"{have} card becomes a {wanted.kind} card")
        )
        return wanted
    if wanted.kind == TASK:
        if wanted.blocked and card.status != "blocked":
            plan.transitions.append(Transition(ref, "block", card.id, wanted.reason))
        elif not wanted.blocked and card.status == "blocked":
            plan.transitions.append(
                Transition(ref, "unblock", card.id, "no longer blocked on GitHub")
            )
        else:
            plan.skipped.append(Skipped(ref, f"card {card.id} exists ({card.status})"))
    else:
        plan.skipped.append(Skipped(ref, f"card {card.id} exists ({card.status})"))
    return None


def plan_mirror(
    issues: list[Issue],
    repo: RepoSettings,
    settings: Settings,
    mirrored: Mapping[IssueRef, Card],
) -> Plan:
    """Creates, transitions and skips that bring the board in line with ``issues``.

    Cards of this repository whose issue is no longer open are archived, unless a worker has
    them. Epics are created after the tasks so their sub-issue cards can be named as parents.
    """
    plan = Plan()
    plan.children = _epic_children(issues, settings) if settings.mirrors_epics else {}
    open_numbers = {issue.number for issue in issues}
    for ref, card in sorted(mirrored.items(), key=lambda item: item[0].number):
        if ref.repo.lower() != repo.repo.lower() or ref.number in open_numbers:
            continue
        if card.status == "running":
            plan.skipped.append(Skipped(ref, f"issue closed while card {card.id} is running"))
        else:
            plan.transitions.append(Transition(ref, "archive", card.id, "issue closed"))
    epics: list[Desired] = []
    load = card_load(mirrored)
    for issue in issues:
        state = desired_state(issue, repo, settings, load)
        if isinstance(state, Skipped):
            plan.skipped.append(state)
            continue
        card = mirrored.get(state.ref)
        wanted = state if card is None else _reconcile_existing(state, card, settings, plan)
        if wanted is None:
            continue
        (epics if wanted.kind == EPIC else plan.creates).append(wanted)
        if wanted.kind == TASK:
            load = with_card(load, wanted.assignee)
    plan.creates.extend(epics)
    return plan
