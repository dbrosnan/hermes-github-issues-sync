from github_issues_sync.cards import (
    DEFAULT_TEMPLATE,
    IssueRef,
    card_title,
    parse_card_ref,
    render_card_body,
)
from github_issues_sync.gh import Issue
from github_issues_sync.settings import load_settings
from tests.conftest import REPO, SETTINGS, issue


def make_issue(**kw):
    return Issue.from_json(REPO, issue(12, **kw))


def settings(**overrides):
    data = {**SETTINGS, **overrides}
    return load_settings(lambda key, default=None: data.get(key, default))


def test_issue_from_json_reads_open_blockers_only():
    raw = issue(5, blockers=(2, 3))
    raw["blockedBy"]["nodes"][0]["state"] = "CLOSED"
    parsed = Issue.from_json(REPO, raw)
    assert parsed.open_blockers == (3,)
    assert parsed.labels == ("agent-ready",)
    assert parsed.url.endswith("/issues/5")


def test_title_and_ref_round_trip():
    i = make_issue(title="Fix the widget " + "x" * 300)
    title = card_title(i)
    assert title.startswith("acme/widgets#12: Fix the widget")
    assert len(title) <= 200
    assert parse_card_ref(title, "") == IssueRef(REPO, 12)


def test_body_source_line_wins_over_title():
    body = "Source issue: https://github.com/acme/other/issues/7\n"
    assert parse_card_ref("acme/widgets#12: x", body) == IssueRef("acme/other", 7)
    assert parse_card_ref("Unrelated card", "no source here") is None


def test_ref_properties():
    ref = IssueRef(REPO, 12)
    assert ref.url == "https://github.com/acme/widgets/issues/12"
    assert ref.short == "acme/widgets#12"
    assert ref.idempotency_key == "gh-acme-widgets-12"


def test_default_body_carries_rules_and_issue_body():
    s = settings()
    body = render_card_body(
        make_issue(body="## Acceptance criteria\n- [ ] it works"), s.repos[0], s
    )
    assert body.startswith("Source issue: https://github.com/acme/widgets/issues/12\n")
    assert "`Fixes #12`" in body
    assert "branch `issue-12`" in body
    assert "`in-progress`" in body and "`blocked`" in body
    assert body.rstrip().endswith("- [ ] it works")


def test_custom_template_and_fallback_on_bad_template():
    s = settings(card_template="Issue {number} for {assignee}\n{body}")
    body = render_card_body(make_issue(), s.repos[0], s)
    assert body.startswith("Source issue: ")  # the mapping line is added when the template lacks it
    assert "Issue 12 for coder" in body
    broken = settings(card_template="{missing_field}")
    assert render_card_body(make_issue(), broken.repos[0], broken).count("Rules for this card") == 1
    assert "{url}" in DEFAULT_TEMPLATE


def test_kind_marker_and_placeholder_and_epic_bodies():
    from github_issues_sync.cards import (
        EPIC,
        PLACEHOLDER,
        TASK,
        card_kind,
        render_epic_body,
        render_placeholder_body,
    )

    s = settings()
    task = render_card_body(make_issue(), s.repos[0], s)
    assert task.splitlines()[1] == "<!-- github-issues-sync: kind=task -->"
    assert card_kind(task) == TASK and card_kind("no marker") == TASK
    placeholder = render_placeholder_body(make_issue(body="idea"), s, "needs-spec")
    assert placeholder.startswith("Source issue: https://github.com/acme/widgets/issues/12\n")
    assert card_kind(placeholder) == PLACEHOLDER
    assert "Stage on GitHub: needs-spec" in placeholder and "`agent-ready`" in placeholder
    assert "Fixes #12" not in placeholder and placeholder.rstrip().endswith("idea")
    epic = render_epic_body(make_issue(), 3)
    assert card_kind(epic) == EPIC and "3 open sub-issues" in epic
    assert "1 open sub-issue on" in render_epic_body(make_issue(), 1)
