"""Per-repository boards and the tasks/full mirror, end to end through the fake executables."""

import json

from github_issues_sync.board import HermesBoard
from github_issues_sync.gh import GhClient
from github_issues_sync.pull import Puller
from github_issues_sync.push import Pusher
from github_issues_sync.settings import load_settings
from tests.conftest import REPO, SETTINGS, card, issue, show

FULL = {**SETTINGS, "board_per_repo": True, "mirror": "full"}
BOARD = {"slug": "widgets", "name": "widgets", "archived": False}


def settings(**overrides):
    data = {**FULL, **overrides}
    return load_settings(lambda key, default=None: data.get(key, default))


def puller(fake_cli, **overrides):
    s = settings(**overrides)
    gh, board = GhClient(), HermesBoard(board=s.board, created_by=s.created_by)
    env_for = lambda profile: dict(__import__("os").environ)  # noqa: E731
    return Puller(s, gh, board, Pusher(s, gh, board, env_for), env_for)


def argv_of(call):
    return call["argv"]


def test_missing_board_is_created_with_the_checkout_as_project_directory(fake_cli):
    fake_cli.respond("boards", "list", stdout=[{"slug": "default", "name": "Default"}])
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond("issue", "list", stdout=[issue(1)])
    fake_cli.respond("kanban", "create", stdout=card("t_1", 1))
    report = puller(fake_cli, reconcile=False).run()
    assert report.ok and report.boards == [("widgets", "created")]
    boards_create = fake_cli.calls_for("hermes", "boards", "create")[0]["argv"]
    assert boards_create == [
        "kanban",
        "boards",
        "create",
        "widgets",
        "--name",
        "widgets",
        "--default-workdir",
        "/srv/checkouts/widgets",
    ]
    listing = fake_cli.calls_for("hermes", "boards", "list")[0]["argv"]
    assert listing[:2] == ["kanban", "boards"]  # boards commands never carry --board
    cards = fake_cli.calls_for("hermes", "list", without=("boards",))[0]["argv"]
    assert cards[:4] == ["kanban", "--board", "widgets", "list"]
    create = fake_cli.calls_for("hermes", "create", without=("boards",))[0]["argv"]
    assert create[:4] == ["kanban", "--board", "widgets", "create"]
    assert report.created[0].board == "widgets"
    assert "created board `widgets`" in report.render()


def test_existing_board_and_overrides(fake_cli):
    fake_cli.respond("boards", "list", stdout=[BOARD, {"slug": "apis", "archived": True}])
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond("issue", "list", stdout=[])
    repos = [REPO, {"repo": "acme/api", "board": "apis", "board_name": "APIs"}]
    report = puller(fake_cli, repos=repos).run()
    assert report.ok
    creates = fake_cli.calls_for("hermes", "boards", "create")
    assert [c["argv"][3:6] for c in creates] == [["apis", "--name", "APIs"]]  # archived: recreated
    assert len(fake_cli.calls_for("hermes", "boards", "list")) == 1  # listed once per sync
    s = settings(board_per_repo=False, board="shared")
    assert [r.board for r in s.repos] == ["shared"]
    s = settings(board_per_repo=False)
    assert [r.board for r in s.repos] == [""]


def test_dry_run_on_a_missing_board_creates_nothing(fake_cli):
    fake_cli.respond("boards", "list", stdout=[])
    fake_cli.respond("issue", "list", stdout=[issue(1), issue(2, labels=("epic",))])
    report = puller(fake_cli).run(dry_run=True)
    assert report.ok and report.boards == [("widgets", "would create")]
    assert [c.kind for c in report.created] == ["task", "epic"]
    assert not fake_cli.calls_for("hermes", "create")
    assert not fake_cli.calls_for("hermes", "list", without=("boards",))
    text = report.render()
    assert "would create board `widgets`" in text and "would create epic: acme/widgets#2" in text
    assert "1 tasks, 0 placeholders, 1 epics" in text


def test_full_mirror_creates_every_kind_and_links_epics(fake_cli):
    fake_cli.respond("boards", "list", stdout=[BOARD])
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond(
        "issue",
        "list",
        stdout=[
            issue(10, title="Epic: wizard", labels=("epic",)),
            issue(11, parent=10),
            issue(12, labels=("needs-spec", "lane:code"), parent=10),
            issue(13, labels=("agent-ready", "blocked"), parent=10),
        ],
    )
    for number in (11, 12, 13):
        title = f"{REPO}#{number}: Do the thing"
        fake_cli.respond("kanban", "create", title, stdout=card(f"t_{number}", number))
    fake_cli.respond("kanban", "create", f"{REPO}#10: Epic: wizard", stdout=card("t_10", 10))
    report = puller(fake_cli, reconcile=False).run()
    assert report.ok
    assert [(c.ref.number, c.kind, c.card_id) for c in report.created] == [
        (11, "task", "t_11"),
        (12, "placeholder", "t_12"),
        (13, "task", "t_13"),
        (10, "epic", "t_10"),
    ]
    creates = {
        c["argv"][4].split("#")[1].split(":")[0]: c["argv"]
        for c in fake_cli.calls_for("hermes", "create", without=("boards",))
    }
    task = creates["11"]
    assert task[task.index("--assignee") + 1] == "coder" and "--branch" in task
    assert "--initial-status" not in task and "--parent" not in task
    placeholder = creates["12"]
    assert placeholder[placeholder.index("--assignee") + 1] == "github"
    assert placeholder[placeholder.index("--workspace") + 1] == "scratch"
    assert "--branch" not in placeholder and "--triage" not in placeholder
    assert "--idempotency-key" in placeholder and "gh-acme-widgets-12" in placeholder
    blocked = creates["13"]
    assert blocked[blocked.index("--initial-status") + 1] == "blocked"
    note = fake_cli.calls_for("hermes", "comment", "t_13")[0]["argv"]
    assert "Blocked on GitHub: labelled `blocked`" in note
    epic = creates["10"]
    parents = [epic[i + 1] for i, tok in enumerate(epic) if tok == "--parent"]
    assert parents == ["t_11", "t_12", "t_13"]
    assert epic[epic.index("--assignee") + 1] == "github"
    assert report.created[-1].parents == ("t_11", "t_12", "t_13")
    assert "created epic: acme/widgets#10 'Epic: wizard' (epic, waits on 3 sub-issue cards)" in (
        report.render()
    )


def test_placeholder_status_choices(fake_cli):
    fake_cli.respond("boards", "list", stdout=[BOARD])
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond("issue", "list", stdout=[issue(1, labels=("needs-spec",))])
    fake_cli.respond("kanban", "create", stdout=card("t_1", 1))
    puller(fake_cli, reconcile=False, placeholder_status="triage").run()
    argv = fake_cli.calls_for("hermes", "create", without=("boards",))[0]["argv"]
    assert "--triage" in argv and "--initial-status" not in argv
    puller(fake_cli, reconcile=False, placeholder_status="blocked").run()
    argv = fake_cli.calls_for("hermes", "create", without=("boards",))[1]["argv"]
    assert argv[argv.index("--initial-status") + 1] == "blocked" and "--triage" not in argv


def test_label_transitions_and_archive_on_close(fake_cli):
    fake_cli.respond("boards", "list", stdout=[BOARD])
    fake_cli.respond(
        "kanban",
        "list",
        stdout=[
            card("t_spec", 1, kind="placeholder"),  # promoted to agent-ready
            card("t_gone", 2, status="done"),  # issue closed
            card("t_run", 3, status="running"),  # issue closed, worker still on it
            card("t_blk", 4),  # now blocked on GitHub
            card("t_unb", 5, status="blocked"),  # unblocked on GitHub
        ],
    )
    fake_cli.respond(
        "issue", "list", stdout=[issue(1), issue(4, labels=("agent-ready", "blocked")), issue(5)]
    )
    fake_cli.respond("kanban", "create", stdout=card("t_new", 1))
    report = puller(fake_cli, reconcile=False, mirror="tasks").run()
    assert report.ok
    assert [(t.ref.number, t.action, t.card_id) for t in report.transitions] == [
        (2, "archive", "t_gone"),
        (1, "replace", "t_spec"),
        (4, "block", "t_blk"),
        (5, "unblock", "t_unb"),
    ]
    assert [c["argv"][3:] for c in fake_cli.calls_for("hermes", "archive")] == [
        ["archive", "t_gone"],
        ["archive", "t_spec"],
    ]
    block = fake_cli.calls_for("hermes", "block")[0]["argv"][3:]
    assert block == ["block", "t_blk", "--kind", "needs_input", "labelled `blocked`"]
    unblock = fake_cli.calls_for("hermes", "unblock")[0]["argv"][3:]
    assert unblock == ["unblock", "t_unb", "--reason", "no longer blocked on GitHub"]
    assert [(c.ref.number, c.kind, c.card_id) for c in report.created] == [(1, "task", "t_new")]
    assert "issue closed while card t_run is running" in report.render()
    text = report.render()
    assert "archive acme/widgets#2 (card t_gone): issue closed" in text
    assert "replace acme/widgets#1 (card t_spec): placeholder card becomes a task card" in text


def test_existing_epic_gets_missing_links(fake_cli):
    fake_cli.respond("boards", "list", stdout=[BOARD])
    fake_cli.respond(
        "kanban",
        "list",
        stdout=[card("t_epic", 10, kind="epic"), card("t_11", 11), card("t_12", 12)],
    )
    fake_cli.respond(
        "kanban", "show", "t_epic", stdout=show(card("t_epic", 10, kind="epic"), parents=["t_11"])
    )
    fake_cli.respond(
        "issue",
        "list",
        stdout=[issue(10, labels=("epic",)), issue(11, parent=10), issue(12, parent=10)],
    )
    report = puller(fake_cli, reconcile=False).run()
    assert report.ok
    assert [(t.action, t.card_id, t.detail) for t in report.transitions] == [
        ("link", "t_epic", "waits on card t_12")
    ]
    assert fake_cli.calls_for("hermes", "link")[0]["argv"][3:] == ["link", "t_12", "t_epic"]
    # dry run: reported, not linked
    report = puller(fake_cli, reconcile=False).run(dry_run=True)
    assert len(fake_cli.calls_for("hermes", "link")) == 1 and report.transitions


def test_reconcile_skips_placeholders_and_uses_the_repo_board(fake_cli):
    fake_cli.respond("boards", "list", stdout=[BOARD])
    fake_cli.respond(
        "kanban",
        "list",
        stdout=[
            card("t_p", 1, status="blocked", kind="placeholder"),
            card("t_t", 2, status="done"),
        ],
    )
    fake_cli.respond("issue", "list", stdout=[issue(1, labels=("needs-spec",)), issue(2)])
    fake_cli.respond("kanban", "show", "t_t", stdout=show(card("t_t", 2, status="done")))
    fake_cli.respond("issue", "view", stdout={"state": "OPEN", "labels": [], "comments": []})
    report = puller(fake_cli, placeholder_status="blocked").run()
    assert report.ok
    assert [(p.event, p.card_id) for p in report.pushed] == [("completed", "t_t")]
    shows = fake_cli.calls_for("hermes", "show")  # reconcile looks, then the push resolves
    assert len(shows) == 2
    assert all(s["argv"][:5] == ["kanban", "--board", "widgets", "show", "t_t"] for s in shows)


def test_issue_limit_warning_and_budget_counts_only_tasks(fake_cli):
    fake_cli.respond("boards", "list", stdout=[BOARD])
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond(
        "issue",
        "list",
        stdout=[issue(1), issue(2), issue(3, labels=("needs-spec",)), issue(4, labels=("epic",))],
    )
    fake_cli.respond("kanban", "create", stdout=card("t_x", 1))
    report = puller(fake_cli, reconcile=False, max_cards_per_sync=1, issue_limit=4).run()
    assert [c.kind for c in report.created] == ["task", "placeholder", "epic"]
    assert [s.reason for s in report.skipped] == ["max_cards_per_sync reached"]
    assert report.warnings == [
        "acme/widgets: gh returned 4 issues, the issue_limit; raise issue_limit or the mirror "
        "is incomplete"
    ]
    assert "warning: acme/widgets: gh returned 4 issues" in report.render()
    assert json.loads(json.dumps(report.to_dict()))["created"][2]["kind"] == "epic"


def test_board_errors_are_reported(fake_cli):
    fake_cli.respond("boards", "list", rc=1, stderr="no kanban root")
    report = puller(fake_cli).run()
    assert report.errors == [
        "cannot read the board: hermes kanban boards list failed (exit 1): no kanban root"
    ]
    fake_cli.rules.clear()
    fake_cli.respond("boards", "list", stdout=[BOARD])
    fake_cli.respond("kanban", "list", stdout=[card("t_gone", 2, status="done")])
    fake_cli.respond("issue", "list", stdout=[issue(10, labels=("epic",)), issue(11, parent=10)])
    fake_cli.respond("archive", rc=1, stderr="locked")
    fake_cli.respond("kanban", "create", f"{REPO}#11: Do the thing", stdout=card("t_11", 11))
    fake_cli.respond("kanban", "create", f"{REPO}#10: Do the thing", rc=1, stderr="bad parent")
    report = puller(fake_cli, reconcile=False).run()
    assert [e.split(":")[0] for e in report.errors] == [
        "acme/widgets#2 (card t_gone)",
        "acme/widgets#10",
    ]
