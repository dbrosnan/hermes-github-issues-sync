# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |

Only the latest release gets fixes. The Hermes catalog pins one commit, so a fix reaches
catalog users after the pin is updated. See [docs/RELEASING.md](docs/RELEASING.md).

## Reporting a vulnerability

**Do not open a public issue.** Report privately through GitHub:
[Report a vulnerability](https://github.com/dbrosnan/hermes-github-issues-sync/security/advisories/new).

Please include what an attacker could do, how to reproduce it, and the versions of the plugin,
Hermes and `gh` you used.

What to expect:

- An acknowledgement within 5 days.
- An assessment and a planned fix date within 14 days.
- Credit in the advisory and changelog, unless you'd rather stay anonymous.

## In scope

- Issue text reaching a repository other than the one it came from, or a card's content being
  posted on an issue it does not mirror.
- A way to make the plugin close an issue, create cards outside the configured repositories, or
  post without the per-event marker check.
- `gh` running as a profile other than the card's assignee when that profile has its own login.
- Anything that lets a hook block or crash the Hermes dispatcher or a worker.

## Out of scope

- The security of `gh`, GitHub, or the login stored by `gh` itself.
- Issue bodies reaching the kanban board. Mirroring them is the point of the plugin.
