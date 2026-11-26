# Module examples: cargo

Scripts under `examples/modules/cargo/` — one per fact or operation of the
[`cargo`](../../modules/cargo.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_cargo.py`) is not rendered here — it is
the shared prerequisite step the scripts below await, not a per-fact or
per-operation demonstration: Ubuntu ships no `cargo` until the `cargo` deb is
installed, and the module deliberately drops the baseline's `requires_command`
gating, so — because the fact command is bare — a host without cargo exits 127
and the gather raises `CommandNotFoundError` rather than answering an empty
list. Installing the toolchain first is what makes the read, and the operation
behind it, a meaningful one.

## Reading the installed crates

The fact on its own: `CargoPackages` runs `cargo install --list` and keeps
its `<crate> v<version>` header rows as sorted, de-duplicated
[`CargoPackageInfo`][reemote.modules.cargo.cargo.CargoPackageInfo] entries —
a crate `name` plus one installed `version`, one entry per name/version pair
because the listing prints one header row per installed version. The reader
keeps the `- <path>` binary lines under each header, blank lines and stray
noise out of the answer and strips the `v` prefix from the version, exactly
like the baseline — while (unlike the baseline) keeping underscore crate
names and whole pre-release version tokens. The script gathers the fact
across the guarded hosts with `gather_fact` — the read-only driver fan-out,
no two-phase plan involved — and prints each host's entry list as JSON. The
fact takes no arguments, so each host reports its own global install tree; a
host with cargo but nothing installed answers `[]`. Like the other examples
it reads the shared project-root `inventory.json` (validated up front with
`validate_inventory`) and the gather is guarded to the `"Ubuntu"` family by
filtering the host list — `gather_fact` has no `when` parameter of its own —
so the other demo hosts are named in a `== Skipped (when guard is_ubuntu):
... ==` line and never connected. It awaits `provision_cargo.py` first, so
the fact command runs against a real `cargo` on the demo host.

::: cargo_cargo_packages
    options:
      members: []

## Installing and removing crates

The package operation: `cargo.packages` installs (or with `present=False`
removes) `cargo install` crates by bare crate name or `<pkg>=<version>` pin,
reading the
[`CargoPackages`][reemote.modules.cargo.cargo.CargoPackages] fact (the
`cargo install --list` header rows) — a pinned request is present only when
that *exact* version is installed and an unpinned one (or one pinned to the
special `latest`) when the crate is installed at any version, so re-running is
a no-op. The script installs one pinned crate (`cargo-edit=0.11.6`) beside one
unpinned one (`ripgrep`), then removes both again — a self-restoring cycle that
leaves the host's `~/.cargo` tree as it found it — yielding each request twice
so the second of each pair is a guaranteed no-op in the apply phase through the
fresh per-step fact cache. The pin is reemote's one user-facing spelling,
shared with apt, dnf, apk, npm and go; it rides through to the install shell
translated into cargo's own grammar as `cargo-edit@0.11.6`, while the unpinned
request renders as the bare crate name — cargo resolves the newest version and
crates.io has no `latest` version to name (deliberately unlike go, whose shell
demands `@latest`). Removal is version-aware: a bare name removes the crate at
any version, a pin only fires for that *exact* installed version, and each
token keeps its pin in `cargo uninstall` — `cargo-edit=0.11.6` uninstalls as
`cargo-edit@0.11.6`, cargo selecting the version through its package-ID spec.
No request is elevated: both commands work on the connecting user's cargo
tree, and `sudo` would install into root's `~/.cargo`, a tree the
unprivileged fact read never sees. `latest=True` keeps the baseline's
documented non-idempotence — an installed unpinned request re-issues
`cargo install <crate>` and reports changed every run — which the pytest suite
exercises rather than this script. `provision_cargo.py` installs the `cargo`
deb with `apt.packages` before the deploy runs, so the example works even on a
host where cargo was never installed. Like the other examples the script reads
the shared project-root `inventory.json` (validated up front with
`validate_inventory`) and every request carries `when=is_ubuntu` — on a
non-Ubuntu host neither the `cargo` commands nor the fact behind them run, and
plan and log render `[skipped: when guard is_ubuntu]`.

::: cargo_packages
    options:
      members: []
