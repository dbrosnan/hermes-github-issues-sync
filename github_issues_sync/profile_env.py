"""The ``gh`` identity for a card's assignee profile.

Hermes's completion-contract gate runs ``gh`` with the assignee profile's own ``GH_TOKEN`` /
``GH_CONFIG_DIR`` rather than whichever login the dispatcher process happens to have. This module
does the same through public Hermes modules: ``hermes_constants`` finds the profile home and
``agent.secret_scope.build_profile_secret_scope`` reads that profile's secrets. The plugin never
opens a ``.env`` file itself.

When the profile has no gh credentials of its own, the gate fails closed. A sync plugin that
silently did nothing would be worse than one that used the host's login, so the default here is
to let ``gh`` use its own stored login (``gh auth status``) and say so in the log;
``require_profile_login: true`` restores the gate's behaviour. In both cases the launch process's
``GH_TOKEN`` / ``GITHUB_TOKEN`` / ``GH_CONFIG_DIR`` are dropped first: Hermes loads its own
``.env`` into the process, and that token belongs to the launch profile, not to the assignee.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("github_issues_sync")

GH_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR")


class ProfileLoginError(RuntimeError):
    """``require_profile_login`` is on and the profile has no gh identity of its own."""


@dataclass(frozen=True)
class ProfileIdentity:
    home: Path | None
    is_process_home: bool
    secrets: Mapping[str, str]


def hermes_identity(profile: str) -> ProfileIdentity:
    """Resolve a profile the way Hermes lays profiles out: ``<root>/profiles/<name>``."""
    try:
        from agent.secret_scope import build_profile_secret_scope
        from hermes_constants import get_default_hermes_root, get_hermes_home
    except ImportError:  # running outside Hermes (tests, a plain shell)
        return ProfileIdentity(None, True, {})
    root = Path(get_default_hermes_root())
    home = root if profile in ("", "default") else root / "profiles" / profile
    if not home.is_dir():
        return ProfileIdentity(None, False, {})
    try:
        is_process_home = home.resolve() == Path(get_hermes_home()).resolve()
    except OSError:
        is_process_home = False
    try:
        secrets = build_profile_secret_scope(home) or {}
    except Exception as exc:  # a broken secret source must not stop the sync
        logger.warning("github-issues-sync: cannot read secrets of profile %r: %s", profile, exc)
        secrets = {}
    return ProfileIdentity(home, is_process_home, secrets)


def gh_env_for_profile(
    profile: str,
    *,
    environ: Mapping[str, str] | None = None,
    require_login: bool = False,
    resolver: Callable[[str], ProfileIdentity] = hermes_identity,
) -> dict[str, str]:
    """Child environment for ``gh`` acting as ``profile``."""
    base = dict(os.environ if environ is None else environ)
    if not profile:
        return base
    identity = resolver(profile)
    if identity.is_process_home:
        return base  # this process already is that profile; its login is the ambient one
    if identity.home is None:
        if require_login:
            raise ProfileLoginError(f"profile {profile!r} not found; cannot pick a gh login")
        logger.warning(
            "github-issues-sync: profile %r not found; using the ambient gh login", profile
        )
        return base
    for name in GH_VARS:
        base.pop(name, None)  # the launch profile's token is not this profile's identity
    own = {name: identity.secrets[name] for name in GH_VARS if identity.secrets.get(name)}
    if own:
        base.update(own)
        return base
    if require_login:
        base["GH_CONFIG_DIR"] = str(identity.home / "gh")  # no login there: gh exits 4
        return base
    logger.debug(
        "github-issues-sync: profile %r has no GH_TOKEN or GH_CONFIG_DIR; using gh's own login",
        profile,
    )
    return base
