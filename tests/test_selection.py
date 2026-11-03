from github_issues_sync.board import Card
from github_issues_sync.cards import IssueRef
from github_issues_sync.gh import Issue
from github_issues_sync.selection import lane_assignee, select_candidates
from github_issues_sync.settings import load_settings
from tests.conftest import REPO, SETTINGS, card, issue


def test_selection_reasons():
    s = load_settings(lambda k, d=None: SETTINGS.get(k, d))
    issues = [
        Issue.from_json(REPO, issue(1)),
        Issue.from_json(REPO, issue(2, labels=("agent-ready", "Epic"))),
        Issue.from_json(REPO, issue(3, labels=("agent-ready", "in-progress"))),
        Issue.from_json(REPO, issue(4, blockers=(1, 9))),
        Issue.from_json(REPO, issue(5)),
    ]
    mirrored = {IssueRef(REPO, 5): Card.from_json(card("t_5", 5, status="done"))}
    candidates, skipped = select_candidates(issues, s.repos[0], s, mirrored)
    assert [(c.issue.number, c.assignee) for c in candidates] == [(1, "coder")]
    assert [(sk.ref.number, sk.reason) for sk in skipped] == [
        (2, "labelled `epic`"),
        (3, "labelled `in-progress`"),
        (4, "blocked by open #1, #9"),
        (5, "card t_5 exists (done)"),
    ]


def lane_settings(**overrides):
    data = {**SETTINGS, "lanes": {"lane:code": "opus", "lane:docs": "librarian"}, **overrides}
    return load_settings(lambda k, d=None: data.get(k, d))


def test_one_lane_label_routes_to_its_profile_and_none_falls_back():
    repo = lane_settings().repos[0]
    docs = Issue.from_json(REPO, issue(1, labels=("agent-ready", "Lane:Docs")))
    plain = Issue.from_json(REPO, issue(2))
    assert lane_assignee(docs, repo) == ("librarian", None)
    assert lane_assignee(plain, repo) == ("coder", None)


def test_two_lane_labels_are_skipped_not_guessed():
    s = lane_settings()
    both = Issue.from_json(REPO, issue(3, labels=("agent-ready", "lane:docs", "lane:code")))
    candidates, skipped = select_candidates([both], s.repos[0], s, {})
    assert candidates == []
    assert skipped[0].reason == "has lane labels `lane:code`, `lane:docs`; keep one"


def test_no_lane_and_no_assignee_is_skipped_with_a_reason():
    s = lane_settings(assignee="")
    plain = Issue.from_json(REPO, issue(4))
    candidates, skipped = select_candidates([plain], s.repos[0], s, {})
    assert candidates == []
    assert skipped[0].reason == "has no lane label and the repository sets no assignee"
