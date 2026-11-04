"""The mirror plan: pure, no fake executables needed."""

from github_issues_sync.board import Card
from github_issues_sync.cards import EPIC, PLACEHOLDER, TASK, IssueRef
from github_issues_sync.gh import Issue
from github_issues_sync.mirror import Desired, desired_state, plan_mirror
from github_issues_sync.selection import Skipped
from github_issues_sync.settings import load_settings
from tests.conftest import REPO, SETTINGS, card, issue


def settings(**overrides):
    data = {**SETTINGS, "mirror": "full", **overrides}
    return load_settings(lambda key, default=None: data.get(key, default))


def mk(number, **kwargs):
    return Issue.from_json(REPO, issue(number, **kwargs))


def mirrored(*cards):
    return {IssueRef(REPO, c["number"]): Card.from_json(c["card"]) for c in cards}


def entry(number, task_id, status="ready", kind=""):
    return {"number": number, "card": card(task_id, number, status=status, kind=kind)}


def test_desired_state_by_label():
    s = settings(lanes={"lane:code": "opus", "lane:docs": "librarian"})
    repo = s.repos[0]
    assert desired_state(mk(1), repo, s) == Desired(mk(1), TASK, "coder")
    assert desired_state(mk(2, labels=("agent-ready", "lane:code")), repo, s).assignee == "opus"
    spec = desired_state(mk(3, labels=("needs-spec", "lane:code")), repo, s)
    assert (spec.kind, spec.assignee, spec.reason) == (PLACEHOLDER, "github", "needs-spec")
    assert desired_state(mk(4, labels=("epic",)), repo, s).kind == EPIC
    assert desired_state(mk(5, labels=("agent-ready", "in-progress")), repo, s).reason == (
        "in progress"
    )
    blocked = desired_state(mk(6, blockers=(1, 2)), repo, s)
    assert blocked.kind == TASK and blocked.blocked and blocked.reason == "blocked by open #1, #2"
    by_label = desired_state(mk(7, labels=("agent-ready", "blocked")), repo, s)
    assert by_label.blocked and by_label.reason == "labelled `blocked`"
    assert desired_state(mk(8, labels=()), repo, s).reason == "open"
    clash = desired_state(mk(9, labels=("agent-ready", "lane:code", "lane:docs")), repo, s)
    assert isinstance(clash, Skipped) and "keep one" in clash.reason


def test_tasks_mode_skips_epics_and_skip_labels():
    s = settings(mirror="tasks", skip_labels=["epic", "wontfix"])
    repo = s.repos[0]
    assert desired_state(mk(1, labels=("epic",)), repo, s) == Skipped(
        IssueRef(REPO, 1), "labelled `epic`"
    )
    assert desired_state(mk(2, labels=("needs-spec", "wontfix")), repo, s).reason == (
        "labelled `wontfix`"
    )
    # in full mode the epic label wins over skip_labels, other skip labels still skip
    full = settings(skip_labels=["epic", "wontfix"])
    assert desired_state(mk(1, labels=("epic",)), full.repos[0], full).kind == EPIC
    assert isinstance(desired_state(mk(3, labels=("wontfix",)), full.repos[0], full), Skipped)


def test_plan_creates_tasks_then_epics_with_children():
    s = settings()
    issues = [
        mk(10, labels=("epic",)),
        mk(11, parent=10),
        mk(12, labels=("needs-spec",), parent=10),
        mk(13, labels=("epic",)),  # an epic with no open sub-issues
    ]
    plan = plan_mirror(issues, s.repos[0], s, {})
    assert [(d.issue.number, d.kind) for d in plan.creates] == [
        (11, TASK),
        (12, PLACEHOLDER),
        (10, EPIC),
        (13, EPIC),
    ]
    assert plan.children == {10: (11, 12), 13: ()}
    assert plan.transitions == [] and plan.skipped == []


def test_plan_archives_closed_issues_but_not_running_cards():
    s = settings(mirror="tasks")
    have = mirrored(entry(1, "t_1"), entry(2, "t_2", status="running"), entry(3, "t_3", "done"))
    plan = plan_mirror([], s.repos[0], s, have)
    assert [(t.ref.number, t.action, t.card_id) for t in plan.transitions] == [
        (1, "archive", "t_1"),
        (3, "archive", "t_3"),
    ]
    assert [sk.reason for sk in plan.skipped] == ["issue closed while card t_2 is running"]


def test_plan_moves_cards_when_labels_change():
    s = settings(mirror="tasks")
    have = mirrored(
        entry(1, "t_spec", kind="placeholder"),  # needs-spec -> agent-ready
        entry(2, "t_task"),  # agent-ready -> needs-spec
        entry(3, "t_run", status="running"),  # worker has it, whatever GitHub says
        entry(4, "t_ready"),  # now labelled blocked
        entry(5, "t_blocked", status="blocked"),  # blocked label removed
        entry(6, "t_same"),  # unchanged
        entry(7, "t_old"),  # a v0.2 card without a kind marker is a task card
    )
    issues = [
        mk(1),
        mk(2, labels=("needs-spec",)),
        mk(3, labels=("needs-spec",)),
        mk(4, labels=("agent-ready", "blocked")),
        mk(5),
        mk(6),
        mk(7),
    ]
    plan = plan_mirror(issues, s.repos[0], s, have)
    assert [(t.ref.number, t.action, t.detail) for t in plan.transitions] == [
        (1, "replace", "placeholder card becomes a task card"),
        (2, "replace", "task card becomes a placeholder card"),
        (4, "block", "labelled `blocked`"),
        (5, "unblock", "no longer blocked on GitHub"),
    ]
    assert [(d.issue.number, d.kind) for d in plan.creates] == [(1, TASK), (2, PLACEHOLDER)]
    assert [(sk.ref.number, sk.reason) for sk in plan.skipped] == [
        (3, "card t_run exists (running)"),
        (6, "card t_same exists (ready)"),
        (7, "card t_old exists (ready)"),
    ]


def test_full_mirror_spreads_a_lane_pool_by_open_cards():
    s = settings(lanes={"lane:code": ["coder", "opus"]})
    have = mirrored(
        {"number": 7, "card": card("t_7", 7, status="review", assignee="opus")},
        {"number": 8, "card": card("t_8", 8, status="done", assignee="coder")},
    )
    issues = [mk(n, labels=("agent-ready", "lane:code")) for n in (1, 2, 3)] + [
        mk(9, labels=("needs-spec", "lane:code")),
        mk(7, labels=("agent-ready", "lane:code")),
        mk(8, labels=("agent-ready", "lane:code")),
    ]
    plan = plan_mirror(issues, s.repos[0], s, have)
    assert [(d.issue.number, d.kind, d.assignee) for d in plan.creates] == [
        (1, TASK, "coder"),  # opus holds t_7 in review; t_8 is done and does not count
        (2, TASK, "coder"),  # one each now: the tie goes to the first listed
        (3, TASK, "opus"),
        (9, PLACEHOLDER, "github"),
    ]
