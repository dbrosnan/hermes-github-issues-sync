from github_issues_sync.board import Card
from github_issues_sync.cards import IssueRef
from github_issues_sync.gh import Issue
from github_issues_sync.selection import select_candidates
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
    candidates, skipped = select_candidates(issues, s, mirrored)
    assert [i.number for i in candidates] == [1]
    assert [(sk.ref.number, sk.reason) for sk in skipped] == [
        (2, "labelled `epic`"),
        (3, "labelled `in-progress`"),
        (4, "blocked by open #1, #9"),
        (5, "card t_5 exists (done)"),
    ]
