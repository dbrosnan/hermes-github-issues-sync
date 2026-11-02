from pathlib import Path

import pytest

from github_issues_sync.profile_env import (
    ProfileIdentity,
    ProfileLoginError,
    gh_env_for_profile,
    hermes_identity,
)

AMBIENT = {"PATH": "/bin", "GH_TOKEN": "ambient", "HOME": "/home/me"}


def resolver_for(identity):
    return lambda profile: identity


def test_empty_profile_keeps_ambient_env():
    assert gh_env_for_profile("", environ=AMBIENT) == AMBIENT


def test_process_home_keeps_ambient_env():
    ident = ProfileIdentity(Path("/h/p"), True, {"GH_TOKEN": "theirs"})
    assert gh_env_for_profile("p", environ=AMBIENT, resolver=resolver_for(ident)) == AMBIENT


def test_profile_credentials_replace_ambient_ones():
    ident = ProfileIdentity(Path("/h/p"), False, {"GH_CONFIG_DIR": "/h/p/gh", "OTHER": "x"})
    env = gh_env_for_profile("p", environ=AMBIENT, resolver=resolver_for(ident))
    assert "GH_TOKEN" not in env
    assert env["GH_CONFIG_DIR"] == "/h/p/gh"
    assert "OTHER" not in env
    assert env["PATH"] == "/bin"


def test_missing_credentials_fall_back_to_gh_login_unless_strict(caplog):
    ident = ProfileIdentity(Path("/h/p"), False, {})
    env = gh_env_for_profile("p", environ=AMBIENT, resolver=resolver_for(ident))
    assert "GH_TOKEN" not in env  # the launch process's token is dropped; gh uses its own login
    assert env["PATH"] == "/bin"
    strict = gh_env_for_profile(
        "p", environ=AMBIENT, require_login=True, resolver=resolver_for(ident)
    )
    assert strict["GH_CONFIG_DIR"] == "/h/p/gh"
    assert "GH_TOKEN" not in strict


def test_unknown_profile(caplog):
    ident = ProfileIdentity(None, False, {})
    env = gh_env_for_profile("ghost", environ=AMBIENT, resolver=resolver_for(ident))
    assert env == AMBIENT
    assert "not found" in caplog.text
    with pytest.raises(ProfileLoginError):
        gh_env_for_profile(
            "ghost", environ=AMBIENT, require_login=True, resolver=resolver_for(ident)
        )


def test_hermes_identity_without_hermes_is_ambient():
    ident = hermes_identity("anything")
    assert ident.is_process_home is True
    assert ident.home is None
