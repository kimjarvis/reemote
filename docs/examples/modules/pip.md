# Module examples: pip

Scripts under `examples/modules/pip/` — one per fact or operation of the
[`pip`](../../modules/pip.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_pip.py`) is not rendered here — it is the
shared prerequisite step the scripts below await, not a per-fact or
per-operation demonstration.

## Installing and removing pip packages

The package operation: `pip.packages` installs (or with `present=False`
removes) Python packages by bare name or `<pkg>==<version>` pin, reading the
[`PipPackages`][reemote.modules.pip.pip.PipPackages] fact
(`<pip> freeze --all`) — a pinned request is present only when that *exact*
version is frozen and an unpinned one when the name is installed at any
version, so re-running is a no-op. The script installs one pinned package,
then removes it again — a self-restoring cycle that leaves the environment
as it found it — yielding each request twice so the second of each pair is
a guaranteed no-op in the apply phase through the fresh per-step fact
cache. Every command targets the interpreter named by `pip`, here the
virtualenv the sibling `provision_pip.py` provisions up front: the baseline's
`virtualenv` option is deliberately dropped, and a venv is its documented
replacement — which also keeps the whole example unprivileged, since a
system interpreter's site-packages would need `sudo=True` and, on modern
Ubuntu, a `--break-system-packages` extra argument (PEP 668 marks the
system interpreter externally managed). `provision_pip.py` installs the
`python3-venv` wheel package with `apt.packages` and creates the fresh
(`--clear`-ed) venv through a `Shell` action before the deploy runs,
because the fact read on a missing interpreter would otherwise raise
`CommandNotFoundError` at plan time. Like the other examples the script
reads the shared project-root `inventory.json` (validated up front with
`validate_inventory`) and every request carries `when=is_ubuntu` — on a
non-Ubuntu host neither the `pip` commands nor the fact behind them run,
and plan and log render `[skipped: when guard is_ubuntu]`.

::: pip_packages
    options:
      members: []

## Creating and removing stdlib virtualenvs

`pip.venv` in action: the deploy creates a virtualenv at
`/tmp/reemote-pip-venv-example` from the base interpreter `python3`
(`python3 -m venv <path>`), re-runs the same create so the apply log shows
the per-phase fact cache — the second step probes the freshly created tree,
finds the requested interpreter at `major.minor`, and reports no change —
then removes the tree with the whole-tree `rm -rf` the baseline delegates
to `files.directory(present=False)`, followed by its own no-op repeat: a
self-restoring cycle for the shared demo hosts. The idempotence goes
*beyond* the baseline: a private guarded probe reads the virtualenv's own
interpreter (answering `None` when the tree is absent) and, when `python=`
is given, the requested interpreter is resolved through the
[`PythonVersion`][reemote.modules.server.server.PythonVersion] fact, so a
`major.minor` mismatch plans a remove-then-create *recreate* instead of
the baseline's silent no-op (upstream bug #2028) — and a micro/patch bump
of the base interpreter never recreates. `site_packages` and
`always_copy` render the command's flags. The tree lives in the SSH user's
own `/tmp` path, so every request is unprivileged (a root-owned parent
would be the case for flagging `sudo=True`). The stdlib `ensurepip`
bootstrap needs Ubuntu's separate `python3-venv` deb, so the script first
awaits the sibling `provision_pip.py`'s idempotent apt prerequisite deploy
before the reconciliation reads any facts. Like every pip example the
script validates the shared project-root `inventory.json` up front and
every request carries `when=is_ubuntu`.

::: pip_venv
    options:
      members: []

## Creating and removing virtualenv-tool environments

`pip.virtualenv` in action: the same reconciliation asked of the
standalone `virtualenv` tool — `virtualenv -p python3 <path>` at
`/tmp/reemote-pip-virtualenv-example`, the no-op re-run, the `rm -rf`
removal and its no-op repeat. The two operations share one decision core;
only the command shape differs (the tool takes the interpreter as `-p`
where the stdlib branch names it before `-m venv`, and `always_copy`
renders `--always-copy` here rather than `--copies`), so the probe, the
mismatch-recreate policy and the reasons are exactly as in the section
above. The prerequisite here is the tool itself: Ubuntu ships `virtualenv`
in the `python3-virtualenv` deb, so the script drives its own idempotent,
`sudo`-flagged `apt.packages` install of that package before deploying —
and a host still without the binary fails the create with a bare
`CommandNotFoundError` at apply. As in the sibling example the tree is
unprivileged and the whole deploy carries `when=is_ubuntu`.

::: pip_virtualenv
    options:
      members: []
