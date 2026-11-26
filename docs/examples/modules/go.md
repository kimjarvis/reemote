# Module examples: go

Scripts under `examples/modules/go/` — one per fact of the
[`go`](../../modules/go.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_go.py`) is not rendered here — it is the
shared prerequisite step the script below awaits, not a per-fact demonstration:
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
