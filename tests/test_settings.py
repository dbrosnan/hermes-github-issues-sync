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
