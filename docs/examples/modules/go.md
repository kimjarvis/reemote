# Module examples: go

Scripts under `examples/modules/go/` — one per fact or operation of the
[`go`](../../modules/go.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_go.py`) is not rendered here — it is the
shared prerequisite step the scripts below await, not a per-fact or
per-operation demonstration:
Ubuntu ships no `go` toolchain until the `golang-go` deb is installed, and the
module deliberately drops the baseline's `requires_command` gating, so — because
the fact's compound command is self-guarding — a host without go reads as an
empty `[]` rather than failing or erroring.

## Reading the installed go binaries

The fact on its own: `GoPackages` resolves the go bin directory (`GOBIN`, else
`$GOPATH/bin`) in the shell, runs `go version -m` over every regular executable
in it, and pairs each binary's `path` row with its `mod` row's version into
sorted [`GoPackageInfo`][reemote.modules.go.go.GoPackageInfo] entries — an
import `path` plus one installed `version`, one entry per path/version pair
because two binaries can carry the same module built at different versions. The
reader keeps the `dep` and `build` rows and the per-binary `<file>: go1.x`
headers out of the answer and skips a `(devel)` version (a `go build` artifact,
not an install), exactly like the baseline. The script gathers the fact across
the guarded hosts with `gather_fact` — the read-only driver fan-out, no
two-phase plan involved — and prints each host's entry list as JSON. The fact
takes no arguments, so each host reports its own go bin directory; a host with
the tooling but no `go install` binaries — and a host without the tooling at
all, where the command's own guards swallow the missing `go` — both answer
`[]`. Like the other examples it reads the shared project-root `inventory.json`
(validated up front with `validate_inventory`) and the gather is guarded to the
`"Ubuntu"` family by filtering the host list — `gather_fact` has no `when`
parameter of its own — so the other demo hosts are named in a
`== Skipped (when guard is_ubuntu): ... ==` line and never connected. It awaits
`provision_go.py` first, so the fact command runs against a real `go` on the
demo host.

::: go_go_packages
    options:
      members: []

## Installing and removing go binaries

The package operation: `go.packages` installs (or with `present=False` removes)
`go install` binaries by bare import path or `<pkg>=<version>` pin, reading the
[`GoPackages`][reemote.modules.go.go.GoPackages] fact (the `go version -m`
listing of the go bin directory) — a pinned request is present only when that
*exact* version is installed and an unpinned one (or one pinned to the special
`latest`) when the path is installed at any version, so re-running is a no-op.
The script installs one pinned tool (`staticcheck=v0.4.5`) beside one unpinned
one (`mvdan.cc/gofumpt`), then removes both again — a self-restoring cycle that
leaves the host's go bin directory as it found it — yielding each request twice
so the second of each pair is a guaranteed no-op in the apply phase through the
fresh per-step fact cache. The pin is reemote's one user-facing spelling,
shared with apt, dnf, apk and npm; it rides through to the install shell
translated into go's own grammar as `staticcheck@v0.4.5`, while the unpinned
request renders `@latest` exactly as the baseline emits it. Removal is
name-based — a compiled binary carries no per-version state, so `rm -f` cannot
select or spare a version: the command resolves the bin directory (`GOBIN`,
else `$GOPATH/bin`) in the shell and drops `"$BINDIR"/<binary>`, the binary name
stripping a trailing Semantic-Import-Versioning `/vN` segment. No request is
elevated: both commands work on the connecting user's go tree, and `sudo` would
install into root's `GOPATH`, a tree the unprivileged fact read never sees.
`latest=True` keeps the baseline's documented non-idempotence — an installed
unpinned request re-issues `go install <pkg>@latest` and reports changed every
run — which the pytest suite exercises rather than this script.
`provision_go.py` installs the `golang-go` deb with `apt.packages` before the
deploy runs, so the example works even on a host where go was never installed.
Like the other examples the script reads the shared project-root
`inventory.json` (validated up front with `validate_inventory`) and every
request carries `when=is_ubuntu` — on a non-Ubuntu host neither the `go`
commands nor the fact behind them run, and plan and log render
`[skipped: when guard is_ubuntu]`.

::: go_packages
    options:
      members: []
