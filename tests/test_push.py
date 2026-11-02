import json

from github_issues_sync.board import Card, HermesBoard
from github_issues_sync.gh import GhClient
from github_issues_sync.push import Pusher, find_pr_url, has_marker, last_block_reason, marker
from github_issues_sync.settings import load_settings
from tests.conftest import REPO, SETTINGS, card, show

PR = "https://github.com/acme/widgets/pull/40"


def settings(**overrides):
    data = {**SETTINGS, **overrides}
    return load_settings(lambda key, default=None: data.get(key, default))


def pusher(fake_cli, **overrides):
    s = settings(**overrides)
    return Pusher(
        s,
        GhClient(),
        HermesBoard(),
        lambda profile: {"GH_TOKEN": f"tok-{profile}", **__import__("os").environ},
        clock=lambda: 1_800_000_000,
    )


def issue_view(labels=("agent-ready",), comments=()):
    return {
        "state": "OPEN",
        "labels": [{"name": n} for n in labels],
        "comments": [{"body": c} for c in comments],
    }


def test_marker_helpers():
    m = marker("claimed", "t_1", 3)
    assert m == "<!-- github-issues-sync: claimed t_1 run=3 -->"
    assert marker("completed", "t_1", None).endswith("run=- -->")
    assert has_marker(("x", f"text\n\n{m}"), m) and not has_marker(("x",), m)


def test_find_pr_url_prefers_the_repo_and_falls_back_to_run_metadata():
    assert find_pr_url(f"see https://github.com/other/x/pull/1 and {PR}", {}, REPO) == PR
    runs = [{"id": 1, "metadata": json.dumps({"published_pr": PR})}]
    assert find_pr_url("", {"runs": runs}, REPO) == PR
    assert (
        find_pr_url(
            "", {"runs": [{"metadata": "bad json"}], "task": {"completion_contract": PR}}, REPO
        )
        == PR
    )
    assert find_pr_url("nothing", {"task": {"completion_contract": "local-only"}}, REPO) == ""


def test_last_block_reason_reads_json_or_text_payload():
    events = [{"kind": "claimed"}, {"kind": "blocked", "payload": '{"reason": "needs creds"}'}]
    assert last_block_reason({"events": events}) == "needs creds"
    assert last_block_reason({"events": [{"kind": "blocked", "payload": "plain"}]}) == "plain"
    assert last_block_reason({"events": []}) == ""


def test_claimed_labels_and_comments_once(fake_cli):
    fake_cli.respond("kanban", "show", stdout=show(card("t_1", 1, status="running")))
    fake_cli.respond("issue", "view", stdout=issue_view(labels=("agent-ready", "blocked")))
    fake_cli.respond("issue", "comment", stdout="https://github.com/acme/widgets/issues/1#c9")
    result = pusher(fake_cli).claimed("t_1", 3, assignee="coder")
    assert result.action == "posted" and result.event == "claimed"
    edit = fake_cli.calls_for("gh", "edit")[0]["argv"]
    assert edit[5:] == ["--add-label", "in-progress", "--remove-label", "blocked"]
    comment = fake_cli.calls_for("gh", "comment")[0]
    assert "Claimed by Hermes profile `coder`" in comment["stdin"]
    assert comment["stdin"].endswith(marker("claimed", "t_1", 3))
    assert comment["env"]["GH_TOKEN"] == "tok-coder"
    note = fake_cli.calls_for("hermes", "comment")[0]["argv"]
    assert note[1:4] == [
        "comment",
        "t_1",
        "Posted claimed on https://github.com/acme/widgets/issues/1#c9",
    ]
    # second delivery of the same event is a no-op
    fake_cli.rules = [r for r in fake_cli.rules if "view" not in r["match"]]
    fake_cli.respond("issue", "view", stdout=issue_view(comments=(comment["stdin"],)))
    again = pusher(fake_cli).claimed("t_1", 3, assignee="coder")
    assert again.action == "already posted"
    assert len(fake_cli.calls_for("gh", "comment")) == 1


def test_completed_posts_summary_and_pr_without_closing(fake_cli):
    task = card("t_2", 2, status="done")
    fake_cli.respond("kanban", "show", stdout=show(task, runs=[{"id": 7, "metadata": None}]))
    fake_cli.respond("issue", "view", stdout=issue_view(labels=("in-progress",)))
    fake_cli.respond("issue", "comment", stdout="")
    result = pusher(fake_cli, summary_chars=60).completed(
        "t_2", 7, summary="Done. " + "x" * 100 + f" {PR}", assignee="coder"
    )
    assert result.action == "posted"
    body = fake_cli.calls_for("gh", "comment")[0]["stdin"]
    assert "Handoff from Hermes profile `coder`" in body
    assert "…" in body and f"PR: {PR}" in body and "`Fixes #2`" in body
    assert not fake_cli.calls_for("gh", "edit")
    assert not fake_cli.calls_for("gh", "close")


def test_completed_without_pr_says_so_and_uses_board_summary(fake_cli):
    task = card("t_2", 2, status="done")
    fake_cli.respond("kanban", "show", stdout=show(task, latest_summary="finished quietly"))
    fake_cli.respond("issue", "view", stdout=issue_view())
    pusher(fake_cli).completed("t_2", None)
    body = fake_cli.calls_for("gh", "comment")[0]["stdin"]
    assert "finished quietly" in body and "No PR URL was found" in body


def test_blocked_swaps_labels_and_posts_reason(fake_cli):
    task = card("t_3", 3, status="blocked")
    fake_cli.respond(
        "kanban",
        "show",
        stdout=show(task, events=[{"kind": "blocked", "payload": {"reason": "needs API key"}}]),
    )
    fake_cli.respond("issue", "view", stdout=issue_view(labels=("in-progress",)))
    result = pusher(fake_cli).blocked("t_3", 2)
    assert result.action == "posted"
    edit = fake_cli.calls_for("gh", "edit")[0]["argv"]
    assert edit[5:] == ["--add-label", "blocked", "--remove-label", "in-progress"]
    assert "needs API key" in fake_cli.calls_for("gh", "comment")[0]["stdin"]


def test_dry_run_and_disabled_events_touch_nothing(fake_cli):
    fake_cli.respond("kanban", "show", stdout=show(card("t_1", 1)))
    fake_cli.respond("issue", "view", stdout=issue_view())
    assert pusher(fake_cli).claimed("t_1", 1, dry_run=True).action == "would post"
    assert (
        pusher(fake_cli, push_events=["completed"]).claimed("t_1", 1).action.startswith("skipped")
    )
    assert not fake_cli.calls_for("gh", "comment") and not fake_cli.calls_for("gh", "edit")


def test_unmirrored_or_unconfigured_cards_are_ignored(fake_cli):
    fake_cli.respond(
        "kanban", "show", "t_x", stdout=show({"id": "t_x", "title": "Plain card", "body": ""})
    )
    foreign = card("t_f", 4)
    foreign["title"] = "other/repo#4: x"
    foreign["body"] = "Source issue: https://github.com/other/repo/issues/4"
    fake_cli.respond("kanban", "show", "t_f", stdout=show(foreign))
    p = pusher(fake_cli)
    assert p.claimed("t_x", 1) is None
    assert p.blocked("t_f", 1) is None
    assert not fake_cli.calls_for("gh")


def test_reconcile_by_status(fake_cli):
    fake_cli.respond(
        "kanban",
        "show",
        "t_r",
        stdout=show(card("t_r", 5, status="running"), runs=[{"id": 1}, {"id": 4}]),
    )
    fake_cli.respond(
        "kanban",
        "show",
        "t_d",
        stdout=show(card("t_d", 6, status="review"), latest_summary=f"shipped {PR}"),
    )
    fake_cli.respond("kanban", "show", "t_b", stdout=show(card("t_b", 7, status="blocked")))
    fake_cli.respond("issue", "view", stdout=issue_view())
    p = pusher(fake_cli)
    running = p.reconcile(Card.from_json(card("t_r", 5, status="running")))
    assert (
        running.event == "claimed"
        and marker("claimed", "t_r", 4) in fake_cli.calls_for("gh", "comment")[-1]["stdin"]
    )
    assert p.reconcile(Card.from_json(card("t_d", 6, status="review"))).event == "completed"
    assert p.reconcile(Card.from_json(card("t_b", 7, status="blocked"))).event == "blocked"
    assert p.reconcile(Card.from_json(card("t_n", 8, status="ready"))) is None
    assert p.reconcile(Card.from_json(card("t_n", 8, status="todo"))) is None
