# Module examples: pip

Scripts under `examples/modules/pip/` — one per fact or operation of the
[`pip`](../../modules/pip.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_pip.py`) is not rendered here — it is the
shared prerequisite step the script below awaits, not a per-fact or
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
