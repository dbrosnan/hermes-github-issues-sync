"""github-issues-sync: GitHub Issues <-> Hermes kanban bridge.

``register()`` only wires callbacks. ``hermes plugins validate`` probes it with a stub context,
so nothing here opens a process, a file or a socket. All work happens when a hook fires, a
dispatcher tick passes the sync interval, or someone runs ``/gh-sync`` or
``hermes github-issues-sync sync``.
"""

import logging

logger = logging.getLogger("github_issues_sync")

HOOKS = {
    "kanban_task_claimed": "on_claimed",  # dispatcher process, inside the dispatch lock
    "kanban_task_completed": "on_completed",  # worker process
    "kanban_task_blocked": "on_blocked",  # worker process
    "on_kanban_dispatch_tick": "on_dispatch_tick",  # dispatcher process, after the lock
}


def register(ctx) -> None:
    from .github_issues_sync.plugin import Plugin

    plugin = Plugin(ctx)
    for hook, method in HOOKS.items():
        ctx.register_hook(hook, getattr(plugin, method))
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        register_command(
            "gh-sync",
            plugin.slash,
            description="Mirror agent-ready GitHub issues onto the kanban board",
            args_hint="[--dry-run] [owner/repo]",
        )
    register_cli = getattr(ctx, "register_cli_command", None)
    if callable(register_cli):
        register_cli(
            name="github-issues-sync",
            help="GitHub Issues <-> kanban sync: sync [--dry-run], status",
            setup_fn=plugin.cli_setup,
            handler_fn=plugin.cli,
        )
    on_unload = getattr(ctx, "on_unload", None)
    if callable(on_unload):
        on_unload(plugin.shutdown)
