"""All GitHub access, through the ``gh`` CLI. No token is ever read or stored by the plugin."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("github_issues_sync")

ISSUE_FIELDS = "number,title,body,url,labels,blockedBy"
NON_INTERACTIVE = {"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}


class GhError(RuntimeError):
    """``gh`` failed. ``stderr`` is kept for the log, never for the issue comment."""

    def __init__(self, args: list[str], returncode: int, stderr: str):
        self.args_list = list(args)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"gh {' '.join(args[:3])} failed (exit {returncode}): {self.stderr[:300]}")


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...]
    open_blockers: tuple[int, ...]

    @classmethod
    def from_json(cls, repo: str, raw: Mapping[str, Any]) -> Issue:
        labels = tuple(str(label.get("name", "")) for label in raw.get("labels") or [])
        blocked_by = raw.get("blockedBy") or {}
        nodes = blocked_by.get("nodes") if isinstance(blocked_by, Mapping) else blocked_by
        blockers = tuple(
            int(node["number"])
            for node in nodes or []
            if str(node.get("state", "OPEN")).upper() == "OPEN" and node.get("number")
        )
        return cls(
            repo=repo,
            number=int(raw["number"]),
            title=str(raw.get("title") or "").strip(),
            body=str(raw.get("body") or ""),
            url=str(raw.get("url") or f"https://github.com/{repo}/issues/{raw['number']}"),
            labels=labels,
            open_blockers=blockers,
        )


@dataclass(frozen=True)
class IssueState:
    state: str
    labels: tuple[str, ...]
    comments: tuple[str, ...]


class GhClient:
    """Thin ``gh`` wrapper. ``env`` per call carries the assignee profile's identity."""

    def __init__(
        self,
        gh_bin: str = "gh",
        timeout: int = 30,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self._gh = gh_bin
        self._timeout = timeout
        self._run = runner

    def run(self, args: list[str], *, env: Mapping[str, str] | None = None, stdin: str = "") -> str:
        full_env = {**(dict(env) if env is not None else os.environ), **NON_INTERACTIVE}
        try:
            result = self._run(
                [self._gh, *args],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                env=full_env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GhError(args, -1, str(exc)) from exc
        if result.returncode != 0:
            raise GhError(args, result.returncode, result.stderr or "")
        return result.stdout

    def run_json(self, args: list[str], *, env: Mapping[str, str] | None = None) -> Any:
        out = self.run(args, env=env)
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as exc:
            raise GhError(args, 0, f"gh printed something other than JSON: {out[:200]!r}") from exc

    def list_ready_issues(
        self, repo: str, label: str, limit: int, *, env: Mapping[str, str] | None = None
    ) -> list[Issue]:
        raw = self.run_json(
            [
                "issue",
                "list",
                "-R",
                repo,
                "--label",
                label,
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                ISSUE_FIELDS,
            ],
            env=env,
        )
        return [Issue.from_json(repo, item) for item in raw or []]

    def issue_state(
        self, repo: str, number: int, *, env: Mapping[str, str] | None = None
    ) -> IssueState:
        raw = self.run_json(
            ["issue", "view", str(number), "-R", repo, "--json", "state,labels,comments"], env=env
        )
        return IssueState(
            state=str(raw.get("state") or ""),
            labels=tuple(str(label.get("name", "")) for label in raw.get("labels") or []),
            comments=tuple(str(c.get("body") or "") for c in raw.get("comments") or []),
        )

    def edit_labels(
        self,
        repo: str,
        number: int,
        *,
        add: tuple[str, ...] = (),
        remove: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
    ) -> None:
        args = ["issue", "edit", str(number), "-R", repo]
        for label in add:
            args += ["--add-label", label]
        for label in remove:
            args += ["--remove-label", label]
        if add or remove:
            self.run(args, env=env)

    def comment(
        self, repo: str, number: int, body: str, *, env: Mapping[str, str] | None = None
    ) -> str:
        out = self.run(
            ["issue", "comment", str(number), "-R", repo, "--body-file", "-"], env=env, stdin=body
        )
        return out.strip()
