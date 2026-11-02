"""Mirror ready issues onto the board, then reconcile the cards that already exist."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from .board import BoardError, Card, HermesBoard
from .cards import IssueRef, branch_for, card_title, parse_card_ref, render_card_body
from .gh import GhClient, GhError
from .profile_env import ProfileLoginError
from .push import Pusher
from .report import Created, SyncReport
from .selection import Skipped, select_candidates
from .settings import RepoSettings, Settings

logger = logging.getLogger("github_issues_sync")


def mirrored_cards(cards: list[Card]) -> dict[IssueRef, Card]:
    """Issue -> card for every card that mirrors an issue. The newest card wins a tie."""
    found: dict[IssueRef, Card] = {}
    for card in sorted(cards, key=lambda c: c.created_at or 0):
        ref = parse_card_ref(card.title, card.body)
        if ref is not None:
            found[ref] = card
    return found


class Puller:
    def __init__(
        self,
        settings: Settings,
        gh: GhClient,
        board: HermesBoard,
        pusher: Pusher,
        env_for: Callable[[str], Mapping[str, str]],
    ):
        self._settings = settings
        self._gh = gh
        self._board = board
        self._pusher = pusher
        self._env_for = env_for

    def run(self, *, dry_run: bool = False, only_repo: str | None = None) -> SyncReport:
        s = self._settings
        report = SyncReport(dry_run=dry_run, ready_label=s.ready_label)
        repos = list(s.repos)
        if only_repo:
            chosen = s.repo(only_repo)
            if chosen is None:
                report.errors.append(f"{only_repo} is not in the repos setting")
                return report
            repos = [chosen]
        if not repos:
            report.errors.append(
                "no repositories configured; add owner/repo entries under "
                "plugins.entries.github-issues-sync.settings.repos"
            )
            return report
        try:
            mirrored = mirrored_cards(self._board.list_cards())
        except BoardError as exc:
            report.errors.append(f"cannot read the board: {exc}")
            return report
        budget = s.max_cards_per_sync
        for repo in repos:
            budget = self._sync_repo(repo, mirrored, report, budget, dry_run)
        return report

    def _sync_repo(
        self,
        repo: RepoSettings,
        mirrored: dict[IssueRef, Card],
        report: SyncReport,
        budget: int,
        dry_run: bool,
    ) -> int:
        s = self._settings
        try:
            env = self._env_for(repo.assignee)
            issues = self._gh.list_ready_issues(repo.repo, s.ready_label, s.issue_limit, env=env)
        except (GhError, ProfileLoginError) as exc:
            report.errors.append(f"{repo.repo}: {exc}")
            return budget
        report.repos.append((repo.repo, len(issues)))
        candidates, skipped = select_candidates(issues, s, mirrored)
        report.skipped.extend(skipped)
        for issue in candidates:
            ref = IssueRef(issue.repo, issue.number)
            if budget <= 0:
                report.skipped.append(Skipped(ref, "max_cards_per_sync reached"))
                continue
            title = card_title(issue)
            branch = branch_for(repo, issue.number)
            if dry_run:
                report.created.append(Created(ref, title, None, branch, repo.assignee))
                budget -= 1
                continue
            try:
                card = self._board.create(
                    title=title,
                    body=render_card_body(issue, repo, s),
                    assignee=repo.assignee,
                    workspace=repo.workspace,
                    branch=branch,
                    idempotency_key=ref.idempotency_key,
                    max_runtime=repo.max_runtime,
                    completion_contract=repo.completion_contract,
                )
            except BoardError as exc:
                report.errors.append(f"{ref.short}: {exc}")
                continue
            mirrored[ref] = card
            report.created.append(Created(ref, title, card.id, branch, repo.assignee))
            logger.info("github-issues-sync: created card %s for %s", card.id, ref.short)
            budget -= 1
        if s.reconcile:
            self._reconcile(repo, mirrored, report, dry_run)
        return budget

    def _reconcile(
        self,
        repo: RepoSettings,
        mirrored: Mapping[IssueRef, Card],
        report: SyncReport,
        dry_run: bool,
    ) -> None:
        for ref, card in mirrored.items():
            if ref.repo.lower() != repo.repo.lower():
                continue
            try:
                result = self._pusher.reconcile(card, dry_run=dry_run)
            except (GhError, BoardError, ProfileLoginError) as exc:
                report.errors.append(f"{ref.short} (card {card.id}): {exc}")
                continue
            if result is not None:
                report.pushed.append(result)
