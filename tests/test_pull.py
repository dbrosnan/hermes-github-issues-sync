from github_issues_sync.board import HermesBoard
from github_issues_sync.gh import GhClient
from github_issues_sync.pull import Puller, mirrored_cards
from github_issues_sync.push import Pusher
from github_issues_sync.settings import load_settings
from tests.conftest import REPO, SETTINGS, card, issue, show


def settings(**overrides):
    data = {**SETTINGS, **overrides}
    return load_settings(lambda key, default=None: data.get(key, default))


def puller(fake_cli, **overrides):
    s = settings(**overrides)
    gh, board = GhClient(), HermesBoard(created_by=s.created_by)
    env_for = lambda profile: dict(__import__("os").environ)  # noqa: E731
    return Puller(s, gh, board, Pusher(s, gh, board, env_for), env_for)


def test_mirrored_cards_newest_wins():
    older = card("t_old", 1)
    newer = card("t_new", 1)
    newer["created_at"] = older["created_at"] + 10
    from github_issues_sync.board import Card

    found = mirrored_cards([Card.from_json(newer), Card.from_json(older)])
    assert [c.id for c in found.values()] == ["t_new"]


def test_sync_creates_cards_for_ready_issues_and_skips_the_rest(fake_cli):
    fake_cli.respond(
        "issue",
        "list",
        stdout=[
            issue(1),
            issue(2, labels=("agent-ready", "epic")),
            issue(3),
            issue(4, blockers=(1,)),
        ],
    )
    fake_cli.respond("kanban", "list", stdout=[card("t_3", 3, status="ready")])
    fake_cli.respond("kanban", "create", stdout=card("t_1", 1))
    report = puller(fake_cli, reconcile=False).run()
    assert report.ok
    assert report.repos == [(REPO, 4)]
    assert [(c.ref.number, c.card_id, c.branch) for c in report.created] == [(1, "t_1", "issue-1")]
    assert sorted(s.ref.number for s in report.skipped) == [2, 3, 4]
    create = fake_cli.calls_for("hermes", "create")[0]["argv"]
    assert create[:3] == ["kanban", "create", "acme/widgets#1: Do the thing"]
    assert "worktree:/srv/checkouts/widgets" in create and "issue-1" in create
    assert "gh-acme-widgets-1" in create and "coder" in create
    body_file = create[create.index("--body-file") + 1]
    assert body_file.endswith("card.md")  # temp file; removed after the call
    text = report.render()
    assert "created: acme/widgets#1" in text and "skipped acme/widgets#2: labelled `epic`" in text
    assert text.endswith("summary: 1 created, 3 skipped, 0 posted, 0 errors")
    assert report.to_dict()["created"][0]["card_id"] == "t_1"


def test_dry_run_creates_nothing(fake_cli):
    fake_cli.respond("issue", "list", stdout=[issue(1), issue(2)])
    fake_cli.respond("kanban", "list", stdout=[])
    report = puller(fake_cli).run(dry_run=True)
    assert [c.ref.number for c in report.created] == [1, 2]
    assert all(c.card_id is None for c in report.created)
    assert not fake_cli.calls_for("hermes", "create") and not fake_cli.calls_for("gh", "comment")
    assert "would create" in report.render() and "(dry run)" in report.render()


def test_card_budget_is_respected(fake_cli):
    fake_cli.respond("issue", "list", stdout=[issue(1), issue(2), issue(3)])
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond("kanban", "create", stdout=card("t_x", 1))
    report = puller(fake_cli, max_cards_per_sync=2, reconcile=False).run()
    assert len(report.created) == 2
    assert [s.reason for s in report.skipped] == ["max_cards_per_sync reached"]


def test_reconcile_posts_missed_events_for_existing_cards(fake_cli):
    fake_cli.respond("issue", "list", stdout=[])
    fake_cli.respond(
        "kanban", "list", stdout=[card("t_d", 9, status="done"), card("t_r", 10, status="ready")]
    )
    fake_cli.respond(
        "kanban",
        "show",
        "t_d",
        stdout=show(card("t_d", 9, status="done"), latest_summary="shipped", runs=[{"id": 2}]),
    )
    fake_cli.respond("issue", "view", stdout={"state": "OPEN", "labels": [], "comments": []})
    report = puller(fake_cli).run()
    assert [(p.event, p.action, p.card_id) for p in report.pushed] == [
        ("completed", "posted", "t_d")
    ]
    assert "posted completed on acme/widgets#9" in report.render()


def test_errors_are_reported_not_raised(fake_cli):
    fake_cli.respond("kanban", "list", rc=1, stderr="db locked")
    report = puller(fake_cli).run()
    assert report.errors == [
        "cannot read the board: hermes kanban list --json failed (exit 1): db locked"
    ]
    fake_cli.rules.clear()
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond("issue", "list", rc=4, stderr="not logged in")
    report = puller(fake_cli).run()
    assert report.errors and "not logged in" in report.errors[0]
    fake_cli.rules.clear()
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond("issue", "list", stdout=[issue(1)])
    fake_cli.respond("kanban", "create", rc=2, stderr="bad assignee")
    report = puller(fake_cli, reconcile=False).run()
    assert "acme/widgets#1" in report.errors[0] and "bad assignee" in report.errors[0]
    assert "errors:" in report.render()


def test_only_repo_filter_and_empty_config(fake_cli):
    report = puller(fake_cli).run(only_repo="other/repo")
    assert report.errors == ["other/repo is not in the repos setting"]
    assert not fake_cli.calls
    s = settings(repos=[])
    env_for = lambda profile: {}  # noqa: E731
    gh, board = GhClient(), HermesBoard()
    report = Puller(s, gh, board, Pusher(s, gh, board, env_for), env_for).run()
    assert "no repositories configured" in report.errors[0]
