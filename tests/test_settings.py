import pytest

from github_issues_sync.settings import ConfigError, load_settings
from tests.conftest import SETTINGS


def getter(overrides=None):
    data = {**SETTINGS, **(overrides or {})}
    return lambda key, default=None: data.get(key, default)


def test_defaults_and_repo_derivation():
    s = load_settings(getter())
    repo = s.repos[0]
    assert repo.repo == "acme/widgets"
    assert repo.assignee == "coder"
    assert repo.workspace == "worktree:/srv/checkouts/widgets"
    assert repo.completion_contract == "local-only"
    assert repo.max_runtime == "90m"
    assert s.ready_label == "agent-ready"
    assert s.push_events == {"claimed", "completed", "blocked"}
    assert s.repo("ACME/Widgets") is repo
    assert s.repo("other/repo") is None


def test_repo_map_overrides_and_bare_workspace_path():
    s = load_settings(
        getter(
            {
                "repos": [
                    {
                        "repo": "acme/api",
                        "assignee": "reviewer",
                        "workspace": "/home/me/api",
                        "completion_contract": "acme/api",
                        "max_runtime": "2h",
                        "branch_prefix": "gh-",
                    }
                ]
            }
        )
    )
    repo = s.repos[0]
    assert repo.workspace == "worktree:/home/me/api"
    assert repo.assignee == "reviewer"
    assert repo.completion_contract == "acme/api"
    assert repo.max_runtime == "2h"
    assert repo.branch_prefix == "gh-"


def test_workspace_spec_passes_through():
    s = load_settings(getter({"repos": [{"repo": "acme/api", "workspace": "dir:/tmp/x"}]}))
    assert s.repos[0].workspace == "dir:/tmp/x"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"repos": ["not-a-repo"]}, "not owner/repo"),
        ({"repos": [42]}, "must be owner/repo"),
        ({"assignee": ""}, "no assignee"),
        ({"workspace_root": ""}, "no workspace"),
        ({"completion_contract": "nope"}, "completion_contract"),
        ({"max_runtime": "soon"}, "max_runtime"),
        ({"repos": ["acme/a", "ACME/A"]}, "twice"),
        ({"push_events": ["claimed", "exploded"]}, "unknown events"),
    ],
)
def test_bad_settings_are_named(overrides, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(getter(overrides))


def test_string_and_none_values_are_coerced():
    s = load_settings(
        getter(
            {
                "repos": "acme/widgets, acme/api",
                "skip_labels": "epic, meta",
                "sync_on_dispatch_tick": "off",
                "max_cards_per_sync": "3",
                "push_events": "claimed",
                "board": None,
                "issue_limit": "zero",
            }
        )
    )
    assert s.repo_slugs == ("acme/widgets", "acme/api")
    assert s.skip_labels == ("epic", "meta")
    assert s.sync_on_dispatch_tick is False
    assert s.max_cards_per_sync == 3
    assert s.push_events == {"claimed"}
    assert s.board == ""
    assert s.issue_limit == 100


def test_lanes_merge_per_repo_over_global_and_make_assignee_optional():
    s = load_settings(
        getter(
            {
                "assignee": "",
                "lanes": {"lane:code": "opus", "lane:docs": "librarian"},
                "repos": [{"repo": "acme/api", "lanes": {"lane:docs": "writer"}}],
            }
        )
    )
    repo = s.repos[0]
    assert repo.assignee == ""
    assert dict(repo.lanes) == {"lane:code": "opus", "lane:docs": "writer"}
    assert repo.identity_profile == "opus"  # no assignee: the lane whose label sorts first
    assert load_settings(getter()).repos[0].identity_profile == "coder"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"assignee": "", "lanes": {}}, "no assignee profile; set assignee or lanes"),
        ({"lanes": "lane:code=opus"}, "lanes must be a map"),
        ({"lanes": {"lane:code": ""}}, "needs both a label and a profile"),
        ({"repos": [{"repo": "acme/api", "lanes": {"": "opus"}}]}, "acme/api: lanes"),
    ],
)
def test_lane_errors(overrides, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(getter(overrides))


def test_board_per_repo_derives_slugs_and_names():
    s = load_settings(getter({"board_per_repo": True}))
    assert (s.repos[0].board, s.repos[0].board_name) == ("widgets", "widgets")
    assert s.repos[0].checkout == "/srv/checkouts/widgets"
    s = load_settings(
        getter(
            {
                "board_per_repo": True,
                "repos": [
                    {"repo": "acme/My.Service_v2", "board_name": "My Service"},
                    {"repo": "acme/api", "board": "APIs-Board", "workspace": "dir:/srv/api"},
                    {"repo": "acme/raw", "workspace": "scratch"},
                ],
            }
        )
    )
    assert [(r.board, r.board_name) for r in s.repos] == [
        ("my-service_v2", "My Service"),
        ("apis-board", "api"),
        ("raw", "raw"),
    ]
    assert [r.checkout for r in s.repos] == ["/srv/checkouts/My.Service_v2", "/srv/api", ""]
    assert load_settings(getter({"board": "Shared"})).repos[0].board == "shared"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"mirror": "everything"}, "mirror 'everything' must be one of ready-only, tasks, full"),
        ({"placeholder_status": "todo"}, "placeholder_status 'todo' must be one of"),
        ({"repos": [{"repo": "acme/api", "board": "bad slug"}]}, "not a valid board slug"),
        ({"board_per_repo": True, "repos": ["acme/---"]}, "cannot derive a board slug"),
    ],
)
def test_v3_setting_errors(overrides, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(getter(overrides))


def test_bounds_and_v3_defaults():
    s = load_settings(getter())
    assert (s.board_per_repo, s.mirror, s.epic_label) == (False, "ready-only", "epic")
    assert (s.placeholder_status, s.placeholder_assignee) == ("ready", "github")
    assert s.ready_only and not s.mirrors_epics
    s = load_settings(
        getter(
            {
                "mirror": "FULL",
                "max_cards_per_sync": 5000,
                "issue_limit": 0,
                "sync_interval_seconds": -5,
                "summary_chars": "lots",
                "command_timeout_seconds": 0,
            }
        )
    )
    assert s.mirror == "full" and s.mirrors_epics
    assert s.max_cards_per_sync == 1000 and s.issue_limit == 1
    assert s.sync_interval_seconds == 0 and s.summary_chars == 2000
    assert s.command_timeout_seconds == 1
