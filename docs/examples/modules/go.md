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
the fact commands are self-guarding — a host without go reads as an
empty `[]` or `{}` — or `None` for the single-valued facts — rather than
failing or erroring. `GoInstalledSDKs` needs no guard at all: its scan loop
never invokes `go` itself, only the SDK binaries it finds.

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

## Reading the toolchain version

`GoVersion` answers the toolchain question a build or install precondition
needs: the active `go` version with its `go` prefix stripped, the full header
line kept `raw`, and the default `goos`/`goarch` target — one
[`GoVersionInfo`][reemote.modules.go.go.GoVersionInfo] entry gathered by a
single guarded shell (`go version && go env GOOS GOARCH`, positional `go env`
printing bare values the parser reads by line). Absence is `None`, not an
empty dict: a host without go — or a toolchain too old to print the expected
three lines — has no answer at all, and the script prints the `None` alongside
the versioned hosts. Like the other fact scripts it reads the shared
project-root `inventory.json`, filters the gather to the `"Ubuntu"` family
(`gather_fact` has no `when` parameter), awaits `provision_go.py` first, and
prints each answer as JSON.

::: go_go_version
    options:
      members: []

## Reading the go environment

`GoEnv` returns the effective `go env` configuration as a `KEY -> value`
mapping — where modules, caches and binaries actually live on a host that
overrides `GOPATH`, `GOBIN`, `GOMODCACHE` or `GOPROXY` in a profile. The
shell is always the full `go env` listing; the script's filtered read names
its keys and `process` narrows the parsed mapping, keeping the requested
order and dropping unset variables (the design's filtered `go env <KEY>...`
form prints bare, unparseable values — the docs page records the fix). A
host without go answers `{}`. Same shared flow as the other fact scripts:
`inventory.json`, the `is_ubuntu` host filter, `provision_go.py` awaited
first, JSON output per host.

::: go_go_env
    options:
      members: []

## Listing side-by-side go SDKs

`GoInstalledSDKs` maps each installed SDK version to its binary path by
scanning search directories (the defaults, `/usr/local` and `/opt`, are the
tarball-install locations) for executable `bin/go` files and printing each
one's version line. The script passes its own directory list so the
parameterized command builder renders on the demo host; a version shared by
two SDKs keeps the later scan, and a host with none of the directories
answers `{}` — the loop never calls `go` itself, so this fact works even
without a toolchain on `PATH`. One path matching both glob shapes lands
twice and collapses in the mapping. Same shared fact-script flow.

::: go_go_installed_sdks
    options:
      members: []

## Reading a module directory's go.mod

`GoModuleInfo` reports a project tree's `go.mod` metadata — the module path,
the `go` directive, and the mapping of direct requirements (indirect ones
deliberately dropped) — by asking go itself through the guarded
`cd '<path>' && go mod edit -json`, the authoritative machine view that
handles block-form `require ( ... )` without any network access. The script
gathers it for a checkout path on each host; a directory without a parsable
`go.mod` — or no `go` at all — reads `None`. Same shared flow, and the
checkout path is a module-level constant so both gather passes key the fact
cache identically.

::: go_go_module_info
    options:
      members: []

## Reading a binary's embedded build info

`GoBinaryBuildInfo` inspects a compiled Go binary on disk with
`go version -m <path>` and answers which compiler built it (`go_version`,
`go` prefix kept), from which module (`module_path`/`module_version`) and
with which `settings` — so a deployment can verify the provenance of a
service binary without mtimes or checksums. Unlike the install listing of
`GoPackages`, a `(devel)` module version is kept: a `go build` artifact
still reports where it came from, which is exactly the question this fact
answers. The script reads the demo host's toolchain binary; a missing file
or non-Go binary reads `None`. Same shared fact-script flow.

::: go_go_binary_build_info
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
