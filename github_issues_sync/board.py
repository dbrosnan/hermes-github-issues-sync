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

    @property
    def busy(self) -> bool:
        """A worker owns the card, or it has finished: the sync never archives or replaces it."""
        return self.status in ("running", "review", "done")


@dataclass(frozen=True)
class CardSpec:
    """Everything ``hermes kanban create`` needs for one card."""

    title: str
    body: str
    assignee: str
    idempotency_key: str
    workspace: str = "scratch"
    branch: str = ""
    max_runtime: str = ""
    completion_contract: str = "local-only"
    parents: tuple[str, ...] = ()
    triage: bool = False
    initial_status: str = ""  # "blocked" parks the card with a sticky block

    def argv(self, created_by: str) -> list[str]:
        args = ["create", self.title, "--assignee", self.assignee, "--workspace", self.workspace]
        if self.branch:
            args += ["--branch", self.branch]
        args += ["--idempotency-key", self.idempotency_key]
        if self.max_runtime:
            args += ["--max-runtime", self.max_runtime]
        args += ["--created-by", created_by]
        if self.completion_contract and self.completion_contract != "local-only":
            args += ["--completion-contract", self.completion_contract]
        for parent in self.parents:
            args += ["--parent", parent]
        if self.triage:
            args.append("--triage")
        if self.initial_status:
            args += ["--initial-status", self.initial_status]
        return [*args, "--json"]


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

    @property
    def board(self) -> str:
        return self._board

    def with_board(self, board: str | None) -> HermesBoard:
        """The same client pointed at ``board`` (a hook payload names the board it fired on)."""
        if not board or board == self._board:
            return self
        return HermesBoard(self._hermes, board, self._timeout, self._created_by, self._run)

    def run(self, args: list[str], *, scoped: bool = True) -> str:
        prefix = ["kanban", "--board", self._board] if scoped and self._board else ["kanban"]
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

    def run_json(self, args: list[str], *, scoped: bool = True) -> Any:
        out = self.run(args, scoped=scoped)
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as exc:
            raise BoardError(
                args, 0, f"hermes printed something other than JSON: {out[:200]!r}"
            ) from exc

    # -- boards -------------------------------------------------------------------------------

    def list_boards(self) -> dict[str, dict[str, Any]]:
        """Slug -> metadata for every unarchived board (``boards list`` ignores ``--board``)."""
        raw = self.run_json(["boards", "list", "--json"], scoped=False)
        boards = raw if isinstance(raw, list) else []
        return {
            str(item.get("slug")): dict(item)
            for item in boards
            if isinstance(item, Mapping) and item.get("slug") and not item.get("archived")
        }

    def create_board(self, slug: str, name: str, workdir: str = "") -> None:
        args = ["boards", "create", slug, "--name", name]
        if workdir:
            args += ["--default-workdir", workdir]
        self.run(args, scoped=False)

    # -- cards --------------------------------------------------------------------------------

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
        return self.create_card(
            CardSpec(
                title=title,
                body=body,
                assignee=assignee,
                idempotency_key=idempotency_key,
                workspace=workspace,
                branch=branch,
                max_runtime=max_runtime,
                completion_contract=completion_contract,
            )
        )

    def create_card(self, spec: CardSpec) -> Card:
        with tempfile.TemporaryDirectory(prefix="github-issues-sync-") as tmp:
            body_file = Path(tmp) / "card.md"
            body_file.write_text(spec.body, encoding="utf-8")
            raw = self.run_json([*spec.argv(self._created_by), "--body-file", str(body_file)])
        return Card.from_json(raw or {})

    def comment(self, task_id: str, text: str) -> None:
        self.run(["comment", task_id, text, "--author", self._created_by])

    def archive(self, task_id: str) -> None:
        self.run(["archive", task_id])

    def link(self, parent_id: str, child_id: str) -> None:
        """``parent_id`` must finish before ``child_id`` may start."""
        self.run(["link", parent_id, child_id])

    def block(self, task_id: str, reason: str) -> None:
        """A sticky block: only ``unblock`` releases it, never the dispatcher's promotion pass."""
        self.run(["block", task_id, "--kind", "needs_input", reason])

    def unblock(self, task_id: str, reason: str) -> None:
        self.run(["unblock", task_id, "--reason", reason])
