import argparse
import importlib.util
import logging
import sys
import time
from pathlib import Path

from github_issues_sync.plugin import SLASH_USAGE, Plugin
from github_issues_sync.runtime import Runtime, Throttle
from tests.conftest import FakeCtx, card, issue, show

ROOT = Path(__file__).resolve().parent.parent


def load_entry_point():
    """Import the plugin root the way Hermes does: as a package named after the directory."""
    name = "github_issues_sync_plugin_root"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_register_wires_hooks_commands_and_unload(ctx):
    module = load_entry_point()
    module.register(ctx)
    assert set(ctx.hooks) == {
        "kanban_task_claimed",
        "kanban_task_completed",
        "kanban_task_blocked",
        "on_kanban_dispatch_tick",
    }
    assert "gh-sync" in ctx.commands and ctx.commands["gh-sync"]["hint"].startswith("[--dry-run]")
    assert "github-issues-sync" in ctx.cli_commands
    assert len(ctx.unload) == 1
    ctx.unload[0]()  # shutdown with nothing running


def test_register_survives_a_stub_context():
    class Stub:
        def get_config(self, key, default=None):
            return default

        def register_hook(self, *a, **k):
            pass

    load_entry_point().register(Stub())  # no commands, no on_unload: still fine


def test_slash_command_parses_arguments(fake_cli, ctx):
    fake_cli.respond("issue", "list", stdout=[issue(1)])
    fake_cli.respond("kanban", "list", stdout=[])
    plugin = Plugin(ctx)
    out = plugin.slash("--dry-run acme/widgets")
    assert "(dry run)" in out and "would create: acme/widgets#1" in out
    assert not fake_cli.calls_for("hermes", "create")
    assert plugin.slash("--what") == SLASH_USAGE
    assert plugin.slash("'unterminated") == SLASH_USAGE
    assert "not in the repos setting" in plugin.slash("other/repo")


def test_slash_reports_config_errors(fake_cli):
    out = Plugin(FakeCtx({"repos": ["acme/widgets"]})).slash("")
    assert "no assignee" in out and "errors:" in out


def test_cli_sync_and_status(fake_cli, ctx, capsys):
    fake_cli.respond("issue", "list", stdout=[issue(1)])
    fake_cli.respond("kanban", "list", stdout=[])
    fake_cli.respond("kanban", "create", stdout=card("t_1", 1))
    plugin = Plugin(ctx)
    parser = argparse.ArgumentParser()
    plugin.cli_setup(parser)
    rc = plugin.cli(parser.parse_args(["sync", "--json"]))
    out = capsys.readouterr().out
    assert rc == 0 and '"card_id": "t_1"' in out
    assert ctx.state.get("last_sync")["summary"].startswith("1 created")
    rc = plugin.cli(parser.parse_args(["status"]))
    out = capsys.readouterr().out
    assert rc == 0 and "repo acme/widgets: assignee coder" in out and "last sync:" in out
    assert plugin.cli(parser.parse_args([])) == 2
    assert "usage" in capsys.readouterr().out
    rc = plugin.cli(parser.parse_args(["sync", "--repo", "nope/nope"]))
    assert rc == 1 and "not in the repos setting" in capsys.readouterr().out


def test_status_without_repos_or_with_bad_config(fake_cli):
    assert "repos: none configured" in Plugin(FakeCtx({})).status_text()
    assert "configuration error" in Plugin(FakeCtx({"repos": ["acme/x"]})).status_text()


def test_hooks_push_events(fake_cli, ctx):
    fake_cli.respond(
        "kanban", "show", stdout=show(card("t_1", 1, status="running"), runs=[{"id": 1}])
    )
    fake_cli.respond("issue", "view", stdout={"state": "OPEN", "labels": [], "comments": []})
    runtime = Runtime()
    plugin = Plugin(ctx, runtime=runtime)
    plugin.on_claimed(
        task_id="t_1", run_id=1, assignee="coder", board="default", profile_name="dispatcher"
    )
    assert runtime.drain()
    plugin.on_completed(task_id="t_1", run_id=1, assignee="coder", summary="done", board=None)
    plugin.on_blocked(task_id="t_1", run_id=1, assignee="coder", reason="stuck", board=None)
    plugin.on_claimed(task_id="", run_id=1)
    events = [c["stdin"].split("\n\n")[-1] for c in fake_cli.calls_for("gh", "comment")]
    assert [e.split()[2] for e in events] == ["claimed", "completed", "blocked"]
    shows = fake_cli.calls_for("hermes", "show")
    assert shows[0]["argv"][:3] == ["kanban", "--board", "default"]
    runtime.shutdown()


def test_hook_errors_are_logged_not_raised(fake_cli, ctx, caplog):
    fake_cli.respond("kanban", "show", rc=1, stderr="no such task")
    with caplog.at_level(logging.WARNING):
        Plugin(ctx).on_completed(task_id="t_missing", run_id=1, assignee="coder")
    assert "not posted" in caplog.text
    Plugin(FakeCtx({"repos": ["bad"]})).on_blocked(task_id="t_1", run_id=1)
    assert "not owner/repo" in caplog.text


def test_dispatch_tick_is_throttled_and_backgrounded(fake_cli, ctx):
    fake_cli.respond("issue", "list", stdout=[])
    fake_cli.respond("kanban", "list", stdout=[])
    now = [1000.0]
    runtime = Runtime()
    plugin = Plugin(ctx, runtime=runtime, clock=lambda: now[0])
    plugin.on_dispatch_tick(board="default", dry_run=True)  # dispatcher dry run: nothing
    plugin.on_dispatch_tick(board="default", dry_run=False, outcome="idle")
    plugin.on_dispatch_tick(board="default", dry_run=False, outcome="idle")  # throttled
    assert runtime.drain()
    assert len(fake_cli.calls_for("gh", "list")) == 1
    now[0] += 301
    plugin.on_dispatch_tick(board="default", dry_run=False)
    assert runtime.drain()
    assert len(fake_cli.calls_for("gh", "list")) == 2
    ctx.settings["sync_on_dispatch_tick"] = False
    now[0] += 301
    plugin.on_dispatch_tick(board="default", dry_run=False)
    runtime.drain()
    assert len(fake_cli.calls_for("gh", "list")) == 2
    runtime.shutdown()


def test_dispatch_tick_with_bad_config_logs(caplog):
    with caplog.at_level(logging.WARNING):
        Plugin(FakeCtx({"repos": ["bad"]})).on_dispatch_tick(dry_run=False)
    assert "not owner/repo" in caplog.text


def test_runtime_keeps_going_after_a_failed_job(caplog):
    runtime = Runtime()
    seen = []

    def boom():
        raise RuntimeError("nope")

    with caplog.at_level(logging.ERROR):
        runtime.submit(boom)
        runtime.submit(seen.append, 1)
        assert runtime.drain()
    assert seen == [1] and "background job failed" in caplog.text
    runtime.shutdown()
    assert runtime._thread is not None and not runtime._thread.is_alive()
    runtime.submit(seen.append, 2)  # restarts the lane after shutdown
    assert runtime.drain() and seen == [1, 2]
    runtime.shutdown()


def test_throttle():
    clock = [0.0]
    throttle = Throttle(lambda: clock[0])
    assert throttle.allow(10) and not throttle.allow(10)
    clock[0] = 10
    assert throttle.allow(10)


def test_drain_times_out_when_a_job_hangs():
    runtime = Runtime()
    runtime.submit(time.sleep, 0.5)
    assert runtime.drain(timeout=0.05) is False
    runtime.shutdown(timeout=1)
