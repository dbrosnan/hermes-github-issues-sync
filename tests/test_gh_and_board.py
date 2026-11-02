import json

import pytest

from github_issues_sync.board import BoardError, HermesBoard
from github_issues_sync.gh import GhClient, GhError
from tests.conftest import REPO, card, issue


def test_list_ready_issues_runs_gh_with_label_filter(fake_cli):
    fake_cli.respond("issue", "list", stdout=[issue(1), issue(2, blockers=(1,))])
    issues = GhClient().list_ready_issues(REPO, "agent-ready", 50)
    assert [i.number for i in issues] == [1, 2]
    assert issues[1].open_blockers == (1,)
    argv = fake_cli.calls[0]["argv"]
    assert argv[:4] == ["issue", "list", "-R", REPO]
    assert "--label" in argv and "agent-ready" in argv and "50" in argv
    assert fake_cli.calls[0]["env"]["GH_PROMPT_DISABLED"] == "1"


def test_issue_state_labels_and_comments(fake_cli):
    fake_cli.respond(
        "issue",
        "view",
        stdout={
            "state": "OPEN",
            "labels": [{"name": "agent-ready"}],
            "comments": [{"body": "hello <!-- m -->"}],
        },
    )
    state = GhClient().issue_state(REPO, 3)
    assert state.state == "OPEN"
    assert state.labels == ("agent-ready",)
    assert state.comments == ("hello <!-- m -->",)


def test_edit_labels_and_comment_pass_through(fake_cli):
    fake_cli.respond("issue", "comment", stdout="https://github.com/acme/widgets/issues/3#c1\n")
    gh = GhClient()
    gh.edit_labels(REPO, 3, add=("in-progress",), remove=("blocked",))
    gh.edit_labels(REPO, 3)  # nothing to do, no call
    assert gh.comment(REPO, 3, "body text") == "https://github.com/acme/widgets/issues/3#c1"
    edits = fake_cli.calls_for("gh", "edit")
    assert len(edits) == 1
    assert edits[0]["argv"] == [
        "issue",
        "edit",
        "3",
        "-R",
        REPO,
        "--add-label",
        "in-progress",
        "--remove-label",
        "blocked",
    ]
    comment = fake_cli.calls_for("gh", "comment")[0]
    assert comment["argv"][-2:] == ["--body-file", "-"]
    assert comment["stdin"] == "body text"


def test_gh_failures_become_gh_error(fake_cli):
    fake_cli.respond("issue", "list", rc=1, stderr="HTTP 404: Not Found")
    with pytest.raises(GhError, match="404"):
        GhClient().list_ready_issues(REPO, "agent-ready", 10)
    fake_cli.rules.clear()
    fake_cli.respond("issue", "view", stdout="not json")
    with pytest.raises(GhError, match="other than JSON"):
        GhClient().issue_state(REPO, 1)
    with pytest.raises(GhError):
        GhClient(gh_bin="/nonexistent/gh").run(["x"])


def test_profile_env_is_passed_to_gh(fake_cli):
    GhClient().run(
        ["auth", "status"],
        env={
            "PATH": __import__("os").environ["PATH"],
            "FAKE_CLI_DIR": __import__("os").environ["FAKE_CLI_DIR"],
            "GH_TOKEN": "profile-token",
        },
    )
    assert fake_cli.calls[0]["env"]["GH_TOKEN"] == "profile-token"


def test_board_list_show_create_comment(fake_cli, tmp_path):
    fake_cli.respond("kanban", "list", stdout=[card("t_1", 1)])
    fake_cli.respond("kanban", "show", stdout={"task": card("t_1", 1), "runs": []})
    fake_cli.respond("kanban", "create", stdout=card("t_9", 9, status="ready"))
    board = HermesBoard(board="web", created_by="syncer")
    cards = board.list_cards()
    assert cards[0].id == "t_1" and cards[0].status == "ready"
    assert board.show("t_1")["task"]["id"] == "t_1"
    created = board.create(
        title="acme/widgets#9: Do the thing",
        body="line one\n--flag-looking line\n",
        assignee="coder",
        workspace="worktree:/srv/widgets",
        branch="issue-9",
        idempotency_key="gh-acme-widgets-9",
        max_runtime="90m",
        completion_contract="acme/widgets",
    )
    assert created.id == "t_9"
    board.comment("t_9", "Posted claimed")
    create_call = fake_cli.calls_for("hermes", "create")[0]["argv"]
    assert create_call[:3] == ["kanban", "--board", "web"]
    assert "--idempotency-key" in create_call and "gh-acme-widgets-9" in create_call
    assert "--completion-contract" in create_call and "acme/widgets" in create_call
    assert (
        "--body-file" in create_call and "--created-by" in create_call and "syncer" in create_call
    )
    assert fake_cli.calls_for("hermes", "comment")[0]["argv"][-2:] == ["--author", "syncer"]
    assert board.with_board("web") is board
    assert board.with_board("") is board
    assert board.with_board("other") is not board


def test_board_local_only_contract_is_the_default(fake_cli):
    fake_cli.respond("kanban", "create", stdout=card("t_9", 9))
    HermesBoard().create(
        title="t",
        body="b",
        assignee="a",
        workspace="scratch",
        branch="issue-9",
        idempotency_key="k",
        max_runtime="90m",
        completion_contract="local-only",
    )
    argv = fake_cli.calls[0]["argv"]
    assert argv[0] == "kanban" and "--board" not in argv
    assert "--completion-contract" not in argv


def test_board_errors(fake_cli):
    fake_cli.respond("kanban", "list", rc=2, stderr="no such board")
    with pytest.raises(BoardError, match="no such board"):
        HermesBoard().list_cards()
    fake_cli.rules.clear()
    fake_cli.respond("kanban", "show", stdout="garbage")
    with pytest.raises(BoardError, match="other than JSON"):
        HermesBoard().show("t_1")
    assert json.loads(json.dumps(card("t", 1)))["id"] == "t"
