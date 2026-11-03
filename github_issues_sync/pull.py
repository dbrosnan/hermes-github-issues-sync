"""Mirror issues onto the board, then reconcile the cards that already exist.

``ready-only`` keeps the v0.2 pull: ready issues become work cards, nothing else is touched.
``tasks`` and ``full`` run the mirror plan: every open issue gets a card of the right kind,
label changes move cards, closed issues archive them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from .board import BoardError, Card, CardSpec, HermesBoard
from .cards import (
    EPIC,
    TASK,
    IssueRef,
    branch_for,
    card_title,
    parse_card_ref,
    render_card_body,
    render_epic_body,
    render_placeholder_body,
)
from .gh import GhClient, GhError, Issue
from .mirror import Desired, Plan, Transition, plan_mirror
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
        self._known_boards: dict[str, dict] | None = None

    # -- entry point --------------------------------------------------------------------------

    def run(self, *, dry_run: bool = False, only_repo: str | None = None) -> SyncReport:
        s = self._settings
        report = SyncReport(dry_run=dry_run, ready_label=s.ready_label, mirror=s.mirror)
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
        budget = s.max_cards_per_sync
        for repo in repos:
            budget = self._sync_repo(repo, report, budget, dry_run)
        return report

    # -- per repository -----------------------------------------------------------------------

    def _ensure_board(self, repo: RepoSettings, report: SyncReport, dry_run: bool) -> HermesBoard:
        """The client for the repository's board, creating the board when it is missing."""
        if not repo.board:
            return self._board
        if self._known_boards is None:
            self._known_boards = self._board.list_boards()
        if repo.board not in self._known_boards:
            if dry_run:
                report.boards.append((repo.board, "would create"))
            else:
                self._board.create_board(repo.board, repo.board_name, repo.checkout)
                report.boards.append((repo.board, "created"))
            self._known_boards = {**self._known_boards, repo.board: {"slug": repo.board}}
        return self._board.with_board(repo.board)

    def _mirrored(
        self, board: HermesBoard, repo: RepoSettings, report: SyncReport, dry_run: bool
    ) -> dict[IssueRef, Card]:
        """The board's mirror cards; none when the board would only be created by this dry run."""
        if dry_run and (repo.board, "would create") in report.boards:
            return {}
        return mirrored_cards(board.list_cards())

    def _sync_repo(self, repo: RepoSettings, report: SyncReport, budget: int, dry_run: bool) -> int:
        s = self._settings
        try:
            board = self._ensure_board(repo, report, dry_run)
            mirrored = self._mirrored(board, repo, report, dry_run)
        except BoardError as exc:
            report.errors.append(f"cannot read the board: {exc}")
            return budget
        try:
            env = self._env_for(repo.identity_profile)
            label = s.ready_label if s.ready_only else ""
            issues = self._gh.list_issues(repo.repo, label=label, limit=s.issue_limit, env=env)
        except (GhError, ProfileLoginError) as exc:
            report.errors.append(f"{repo.repo}: {exc}")
            return budget
        if len(issues) >= s.issue_limit:
            report.warnings.append(
                f"{repo.repo}: gh returned {len(issues)} issues, the issue_limit; "
                "raise issue_limit or the mirror is incomplete"
            )
        report.repos.append((repo.repo, len(issues)))
        if s.ready_only:
            budget = self._legacy_pull(repo, board, issues, mirrored, report, budget, dry_run)
        else:
            budget = self._mirror_pull(repo, board, issues, mirrored, report, budget, dry_run)
        if s.reconcile:
            self._reconcile(repo, board, mirrored, report, dry_run)
        return budget

    def _legacy_pull(
        self,
        repo: RepoSettings,
        board: HermesBoard,
        issues: list[Issue],
        mirrored: dict[IssueRef, Card],
        report: SyncReport,
        budget: int,
        dry_run: bool,
    ) -> int:
        candidates, skipped = select_candidates(issues, repo, self._settings, mirrored)
        report.skipped.extend(skipped)
        for candidate in candidates:
            wanted = Desired(candidate.issue, TASK, candidate.assignee)
            budget = self._create(wanted, repo, board, mirrored, report, budget, dry_run, ())
        return budget

    def _mirror_pull(
        self,
        repo: RepoSettings,
        board: HermesBoard,
        issues: list[Issue],
        mirrored: dict[IssueRef, Card],
        report: SyncReport,
        budget: int,
        dry_run: bool,
    ) -> int:
        plan = plan_mirror(issues, repo, self._settings, mirrored)
        report.skipped.extend(plan.skipped)
        for transition in plan.transitions:
            self._apply(transition, board, mirrored, report, dry_run)
        for wanted in plan.creates:
            parents = self._parents_for(wanted, plan, mirrored)
            budget = self._create(wanted, repo, board, mirrored, report, budget, dry_run, parents)
        self._link_epics(plan, board, mirrored, report, dry_run)
        return budget

    # -- applying the plan --------------------------------------------------------------------

    def _apply(
        self,
        transition: Transition,
        board: HermesBoard,
        mirrored: dict[IssueRef, Card],
        report: SyncReport,
        dry_run: bool,
    ) -> None:
        report.transitions.append(transition)
        if dry_run:
            if transition.action in ("archive", "replace"):
                mirrored.pop(transition.ref, None)
            return
        try:
            if transition.action in ("archive", "replace"):
                board.archive(transition.card_id)
                mirrored.pop(transition.ref, None)
            elif transition.action == "block":
                board.block(transition.card_id, transition.detail)
            elif transition.action == "unblock":
                board.unblock(transition.card_id, transition.detail)
        except BoardError as exc:
            report.errors.append(f"{transition.ref.short} (card {transition.card_id}): {exc}")

    def _parents_for(
        self, wanted: Desired, plan: Plan, mirrored: Mapping[IssueRef, Card]
    ) -> tuple[str, ...]:
        """Card ids of an epic's open sub-issues: the cards the epic card waits on."""
        if wanted.kind != EPIC:
            return ()
        subs = plan.children.get(wanted.issue.number, ())
        found = (mirrored.get(IssueRef(wanted.issue.repo, number)) for number in subs)
        return tuple(card.id for card in found if card is not None)

    def _spec(self, wanted: Desired, repo: RepoSettings, parents: tuple[str, ...]) -> CardSpec:
        s, issue = self._settings, wanted.issue
        title = card_title(issue)
        key = wanted.ref.idempotency_key
        if wanted.kind == TASK:
            return CardSpec(
                title=title,
                body=render_card_body(issue, repo, s, assignee=wanted.assignee),
                assignee=wanted.assignee,
                idempotency_key=key,
                workspace=repo.workspace,
                branch=branch_for(repo, issue.number),
                max_runtime=repo.max_runtime,
                completion_contract=repo.completion_contract,
                initial_status="blocked" if wanted.blocked else "",
            )
        if wanted.kind == EPIC:
            body = render_epic_body(issue, len(parents))
            return CardSpec(
                title=title,
                body=body,
                assignee=wanted.assignee,
                idempotency_key=key,
                parents=parents,
            )
        return CardSpec(
            title=title,
            body=render_placeholder_body(issue, s, wanted.reason),
            assignee=wanted.assignee,
            idempotency_key=key,
            triage=s.placeholder_status == "triage",
            initial_status="blocked" if s.placeholder_status == "blocked" else "",
        )

    def _create(
        self,
        wanted: Desired,
        repo: RepoSettings,
        board: HermesBoard,
        mirrored: dict[IssueRef, Card],
        report: SyncReport,
        budget: int,
        dry_run: bool,
        parents: tuple[str, ...],
    ) -> int:
        """Create one card. Only work cards count against ``max_cards_per_sync``: placeholders
        and epics never start a worker."""
        ref = wanted.ref
        counts = wanted.kind == TASK
        if counts and budget <= 0:
            report.skipped.append(Skipped(ref, "max_cards_per_sync reached"))
            return budget
        spec = self._spec(wanted, repo, parents)
        record = Created(
            ref, spec.title, None, spec.branch, spec.assignee, wanted.kind, board.board, parents
        )
        if dry_run:
            report.created.append(record)
            mirrored[ref] = Card(f"dry-{ref.number}", spec.title, spec.body, "ready", "", "", 0)
            return budget - 1 if counts else budget
        try:
            card = board.create_card(spec)
        except BoardError as exc:
            report.errors.append(f"{ref.short}: {exc}")
            return budget
        if wanted.kind == TASK and wanted.blocked:  # parked by --initial-status; say why
            self._note(board, card.id, f"Blocked on GitHub: {wanted.reason}")
        mirrored[ref] = card
        report.created.append(
            Created(
                ref,
                spec.title,
                card.id,
                spec.branch,
                spec.assignee,
                wanted.kind,
                board.board,
                parents,
            )
        )
        logger.info(
            "github-issues-sync: created %s card %s for %s", wanted.kind, card.id, ref.short
        )
        return budget - 1 if counts else budget

    @staticmethod
    def _note(board: HermesBoard, card_id: str, text: str) -> None:
        try:
            board.comment(card_id, text)
        except BoardError as exc:  # the card exists; the note is a courtesy
            logger.debug("github-issues-sync: could not comment on %s: %s", card_id, exc)

    def _link_epics(
        self,
        plan: Plan,
        board: HermesBoard,
        mirrored: Mapping[IssueRef, Card],
        report: SyncReport,
        dry_run: bool,
    ) -> None:
        """Add the parent links an existing epic card is missing (new sub-issues, replaced cards).
        Cards created this sync already carry their parents."""
        created = {c.ref for c in report.created}
        for number, subs in plan.children.items():
            ref = next((r for r in mirrored if r.number == number), None)
            epic = mirrored.get(ref) if ref else None
            if ref is None or epic is None or ref in created or epic.id.startswith("dry-"):
                continue
            sub_cards = (mirrored.get(IssueRef(ref.repo, sub)) for sub in subs)
            wanted_ids = {c.id for c in sub_cards if c is not None and not c.id.startswith("dry-")}
            try:
                have = set(board.show(epic.id).get("parents") or [])
            except BoardError as exc:
                report.errors.append(f"{ref.short} (card {epic.id}): {exc}")
                continue
            for parent_id in sorted(wanted_ids - have):
                report.transitions.append(
                    Transition(ref, "link", epic.id, f"waits on card {parent_id}")
                )
                if dry_run:
                    continue
                try:
                    board.link(parent_id, epic.id)
                except BoardError as exc:
                    report.errors.append(f"{ref.short} (card {epic.id}): {exc}")

    # -- reconcile ----------------------------------------------------------------------------

    def _reconcile(
        self,
        repo: RepoSettings,
        board: HermesBoard,
        mirrored: Mapping[IssueRef, Card],
        report: SyncReport,
        dry_run: bool,
    ) -> None:
        for ref, card in mirrored.items():
            if ref.repo.lower() != repo.repo.lower() or card.id.startswith("dry-"):
                continue
            try:
                result = self._pusher.reconcile(card, board=board.board, dry_run=dry_run)
            except (GhError, BoardError, ProfileLoginError) as exc:
                report.errors.append(f"{ref.short} (card {card.id}): {exc}")
                continue
            if result is not None:
                report.pushed.append(result)
