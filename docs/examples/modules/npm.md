# Module examples: npm

Scripts under `examples/modules/npm/` — one per fact or operation of the
[`npm`](../../modules/npm.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_npm.py`) is not rendered here — it is the
shared prerequisite step the scripts below await, not a per-fact or
per-operation demonstration: Ubuntu ships no `node`/`npm` until they are
installed, and the module deliberately drops the baseline's
`requires_command` gating, so a bare `npm` fact read on such a host fails with
`CommandNotFoundError` instead of answering an empty fact.

## Reading the installed global packages

The fact on its own: `NpmPackages` runs `npm ls -g --depth=0 --json` and walks
the top-level `dependencies` map of the answer into sorted
[`NpmPackageInfo`][reemote.modules.npm.npm.NpmPackageInfo] entries — a `name`
plus the `version` npm reports. Depth 0 is the contract, because that is the
tree `npm install`/`npm uninstall` mutate; the JSON reader is what keeps a
scoped name (`@scope/name`), a prerelease (`1.1.0-beta.4`) and a dist-tag
(`latest`) that the baseline's box-drawing tree regex cannot express, and the
leading `npm warn` line a live npm prints ahead of the document is discarded.
The script gathers the fact across the guarded hosts with `gather_fact` — the
read-only driver fan-out, no two-phase plan involved — and prints each host's
entry list as JSON. It passes neither of the fact's two optional scope
arguments, `directory` and `prefix`, so each host reports its own default
global tree; run `npm_packages.py` first to see a tree that actually holds
packages. Like the other examples it reads the shared project-root
`inventory.json` (validated up front with `validate_inventory`) and the gather
is guarded to the `"ubuntu"` group by filtering the host list — `gather_fact`
has no `when` parameter of its own — so the other demo hosts are named in a
`== Skipped (when guard is_ubuntu): ... ==` line and never connected.

::: npm_npm_packages
    options:
      members: []

## Installing and removing global npm packages

The package operation: `npm.packages` installs (or with `present=False`
removes) Node.js packages by bare name or `<pkg>=<version>` pin, reading the
[`NpmPackages`][reemote.modules.npm.npm.NpmPackages] fact
(`npm ls -g --depth=0 --json`) — a pinned request is present only when that
*exact* version is listed and an unpinned one when the name is installed at any
version, so re-running is a no-op. The script installs one pinned package
(`minimist=1.2.8`) beside one unpinned one (`ms`), then removes both again — a
self-restoring cycle that leaves the host's global tree as it found it —
yielding each request twice so the second of each pair is a guaranteed no-op in
the apply phase through the fresh per-step fact cache. The pin is reemote's one
user-facing spelling, shared with apt, dnf and apk; it rides through to the
shell translated into npm's own grammar as `minimist@1.2.8`, and removal feeds
npm the tokens verbatim, since `npm uninstall` — unlike `pip uninstall` —
accepts a versioned token. Every request carries `sudo=True`, because a global
install writes into npm's root-owned prefix; the fact read is unprivileged. The
two environment parameters are left alone here: `prefix` exists to relocate (and
so separately track) a global tree on a host that keeps more than one, and
`unsafe_perm` to let npm run a dependency's lifecycle scripts while elevated —
neither package here ships one. Nor does the script use the project scope
(`global_=False` with `directory=`) or the stateless `package_json=` manifest
direction, because a real project tree would leave `node_modules` behind on the
shared host. `provision_npm.py` installs the `nodejs` and `npm` debs with
`apt.packages` before the deploy runs, so the example works even on a host
where npm was never installed. Like the other examples the script reads the
shared project-root `inventory.json` (validated up front with
`validate_inventory`) and every request carries `when=is_ubuntu` — on a
non-Ubuntu host neither the `npm` commands nor the fact behind them run, and
plan and log render `[skipped: when guard is_ubuntu]`.

::: npm_packages
    options:
      members: []
