"""The Hermes kanban board, through the ``hermes kanban`` CLI.

Going through the CLI keeps the plugin on Hermes's public surface, makes every call visible in a
process list, and lets the tests put a fake ``hermes`` on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BoardError(RuntimeError):
    def __init__(self, args: list[str], returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"hermes kanban {' '.join(args[:2])} failed (exit {returncode}): {self.stderr[:300]}"
        )


@dataclass(frozen=True)
class Card:
    id: str
    title: str
    body: str
    status: str
    assignee: str
    completion_contract: str
    created_at: int | None

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Card:
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            body=str(raw.get("body") or ""),
            status=str(raw.get("status") or ""),
            assignee=str(raw.get("assignee") or ""),
            completion_contract=str(raw.get("completion_contract") or ""),
            created_at=raw.get("created_at"),
        )


class HermesBoard:
    def __init__(
        self,
        hermes_bin: str = "hermes",
        board: str = "",
        timeout: int = 60,
        created_by: str = "github-issues-sync",
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self._hermes = hermes_bin
        self._board = board
        self._timeout = timeout
        self._created_by = created_by
        self._run = runner

    def with_board(self, board: str | None) -> HermesBoard:
        """The same client pointed at ``board`` (a hook payload names the board it fired on)."""
        if not board or board == self._board:
            return self
        return HermesBoard(self._hermes, board, self._timeout, self._created_by, self._run)

    def run(self, args: list[str]) -> str:
        prefix = ["kanban", "--board", self._board] if self._board else ["kanban"]
        full = [self._hermes, *prefix, *args]
        try:
            result = self._run(
                full,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                env={**os.environ, "NO_COLOR": "1"},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoardError(args, -1, str(exc)) from exc
        if result.returncode != 0:
            raise BoardError(args, result.returncode, result.stderr or "")
        return result.stdout

    def run_json(self, args: list[str]) -> Any:
        out = self.run(args)
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as exc:
            raise BoardError(
                args, 0, f"hermes printed something other than JSON: {out[:200]!r}"
            ) from exc

    def list_cards(self) -> list[Card]:
        raw = self.run_json(["list", "--json"])
        return [Card.from_json(item) for item in raw or []]

    def show(self, task_id: str) -> dict[str, Any]:
        raw = self.run_json(["show", task_id, "--json"])
        return raw if isinstance(raw, dict) else {}

    def create(
        self,
        *,
        title: str,
        body: str,
        assignee: str,
        workspace: str,
        branch: str,
        idempotency_key: str,
        max_runtime: str,
        completion_contract: str,
    ) -> Card:
        args = [
            "create",
            title,
            "--assignee",
            assignee,
            "--workspace",
            workspace,
            "--branch",
            branch,
            "--idempotency-key",
            idempotency_key,
            "--max-runtime",
            max_runtime,
            "--created-by",
            self._created_by,
            "--json",
        ]
        if completion_contract and completion_contract != "local-only":
            args += ["--completion-contract", completion_contract]
        with tempfile.TemporaryDirectory(prefix="github-issues-sync-") as tmp:
            body_file = Path(tmp) / "card.md"
            body_file.write_text(body, encoding="utf-8")
            raw = self.run_json([*args, "--body-file", str(body_file)])
        return Card.from_json(raw or {})

    def comment(self, task_id: str, text: str) -> None:
        self.run(["comment", task_id, text, "--author", self._created_by])
