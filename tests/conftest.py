"""Fake ``gh`` and ``hermes`` executables on PATH.

Each fake appends the call it received to ``calls.jsonl`` and answers from ``responses.json``:
a list of rules ``{"match": [tokens...], "stdout": "...", "rc": 0}`` where every token must be one
of the argv words. The first matching rule wins; with no match the fake prints ``null`` and exits 0.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE_SOURCE = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ["FAKE_CLI_DIR"])
argv = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
prefixes = ("GH_", "GITHUB_", "HERMES_")
record = {"tool": Path(sys.argv[0]).name, "argv": argv, "stdin": stdin,
          "env": {k: v for k, v in os.environ.items() if k.startswith(prefixes)}}
with (state / "calls.jsonl").open("a") as fh:
    fh.write(json.dumps(record) + "\n")
rules_file = state / "responses.json"
rules = json.loads(rules_file.read_text()) if rules_file.exists() else []
for rule in rules:
    if rule.get("tool", Path(sys.argv[0]).name) != Path(sys.argv[0]).name:
        continue
    if all(token in argv for token in rule["match"]):
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("rc", 0))
sys.stdout.write("null")
"""


class FakeCli:
    def __init__(self, directory: Path):
        self.dir = directory
        self.rules: list[dict] = []
        (self.dir / "calls.jsonl").write_text("")
        self.save()

    def respond(self, *match: str, stdout="null", rc: int = 0, tool: str | None = None, stderr=""):
        if not isinstance(stdout, str):
            stdout = json.dumps(stdout)
        rule = {"match": list(match), "stdout": stdout, "rc": rc, "stderr": stderr}
        if tool:
            rule["tool"] = tool
        self.rules.append(rule)
        self.save()

    def save(self) -> None:
        (self.dir / "responses.json").write_text(json.dumps(self.rules))

    @property
    def calls(self) -> list[dict]:
        text = (self.dir / "calls.jsonl").read_text()
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def calls_for(self, tool: str, *tokens: str, without: tuple[str, ...] = ()) -> list[dict]:
        return [
            c
            for c in self.calls
            if c["tool"] == tool
            and all(t in c["argv"] for t in tokens)
            and not any(t in c["argv"] for t in without)
        ]


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("gh", "hermes"):
        exe = bin_dir / name
        exe.write_text(FAKE_SOURCE)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CLI_DIR", str(state))
    return FakeCli(state)


class FakeState:
    def __init__(self):
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeCtx:
    """The parts of Hermes's PluginContext the plugin touches."""

    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}
        self.hooks: dict[str, list] = {}
        self.commands: dict[str, dict] = {}
        self.cli_commands: dict[str, dict] = {}
        self.unload: list = []
        self.state = FakeState()
        self.profile_name = "default"

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback, **_):
        self.hooks.setdefault(name, []).append(callback)

    def register_command(self, name, handler, description="", args_hint="", **_):
        self.commands[name] = {"handler": handler, "description": description, "hint": args_hint}

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, **_):
        self.cli_commands[name] = {"help": help, "setup": setup_fn, "handler": handler_fn}

    def on_unload(self, callback):
        self.unload.append(callback)


REPO = "acme/widgets"
SETTINGS = {  # the v0.2 shape: one board, ready issues only
    "repos": [REPO],
    "assignee": "coder",
    "workspace_root": "/srv/checkouts",
    "board_per_repo": False,
}


def issue(
    number: int,
    title: str = "Do the thing",
    labels=("agent-ready",),
    blockers=(),
    body="Body",
    parent: int | None = None,
):
    return {
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/{REPO}/issues/{number}",
        "labels": [{"name": name} for name in labels],
        "parent": {"number": parent, "state": "OPEN"} if parent else None,
        "blockedBy": {
            "nodes": [{"number": n, "state": "OPEN"} for n in blockers],
            "totalCount": len(blockers),
        },
    }


def card(
    task_id: str,
    number: int,
    status: str = "ready",
    assignee: str = "coder",
    kind: str = "",
    **extra,
):
    marker = f"\n<!-- github-issues-sync: kind={kind} -->" if kind else ""
    return {
        "id": task_id,
        "title": f"{REPO}#{number}: Do the thing",
        "body": f"Source issue: https://github.com/{REPO}/issues/{number}{marker}\n\nRules",
        "status": status,
        "assignee": assignee,
        "created_at": 1000 + number,
        **extra,
    }


def show(task: dict, *, runs=(), events=(), latest_summary=None, parents=(), children=()):
    return {
        "task": task,
        "latest_summary": latest_summary,
        "parents": list(parents),
        "children": list(children),
        "comments": [],
        "events": list(events),
        "runs": list(runs),
    }


@pytest.fixture
def ctx():
    return FakeCtx(dict(SETTINGS))
