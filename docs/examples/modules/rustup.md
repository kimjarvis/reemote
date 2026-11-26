# Module examples: rustup

Scripts under `examples/modules/rustup/` — one per fact and operation of the
[`rustup`](../../modules/rustup.md) module (the cargo facts and operations are
demonstrated on the [cargo page](cargo.md)). See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_rustup.py`) is not rendered here — it is
the shared prerequisite step the scripts below await, not a per-fact
demonstration: Ubuntu ships no `rustup` until the `rustup` deb is installed,
and the module deliberately drops the design's `requires_command` gating, so
— because the fact commands are bare — a host without rustup exits 127 and
the gather raises `CommandNotFoundError` rather than answering an empty
collection. Note what the deb does and does not do: it installs the
*installer*, not any toolchain, so on a freshly provisioned host every fact
here honestly answers empty (`{}`, `None`, `[]`) until `rustup default stable`
or an operation downloads one.

## Listing the installed toolchains

The collection fact: `RustupToolchains` runs `rustup toolchain list` and
detaches rustup's trailing status marker from each row — the toolchain name is
what precedes the parenthesized part, and the entry is the default when the
marker's flags (`(default)`, `(active)` or `(active, default)`) mention
`default` — reporting a sorted
[`RustupToolchainInfo`][reemote.modules.rustup.rustup.RustupToolchainInfo]
mapping keyed by toolchain name, with the release channel derived from the
whole leading name field (`stable`, `beta`, `nightly`, else `custom`). The
fact's only argument, `rustup_home`, rides along as a `RUSTUP_HOME=<quoted> `
env prefix and is left at its default here, so each host answers for its own
install tree. Like the other examples it reads the shared project-root
`inventory.json` (validated up front with `validate_inventory`) and the gather
is guarded to the `"Ubuntu"` family by filtering the host list — `gather_fact`
has no `when` parameter of its own — so the other demo hosts are named in a
`== Skipped (when guard is_ubuntu): ... ==` line and never connected. It
awaits `provision_rustup.py` first, so the fact command runs against a real
`rustup` on the demo host.

::: rustup_rustup_toolchains
    options:
      members: []

## Reading the default toolchain

The convenience short fact: [`RustupDefaultToolchain`][reemote.modules.rustup.rustup.RustupDefaultToolchain]
spares a caller the scan of `RustupToolchains` for the entry whose `default`
flag is set and answers the name directly — a `str | None`, `None` when no
toolchain carries the marker (an `(active)`-only marker is *not* a default).
It composes rather than re-reads: `process` delegates to
[`RustupToolchains`][reemote.modules.rustup.rustup.RustupToolchains] and
projects the marked entry, so the gather issues exactly one
`rustup toolchain list` shell and the two facts can never disagree about what
the default is. Its optional `rustup_home` argument forwards to the delegated
listing. The script follows the same inventory → guard → `gather_fact` →
JSON-print flow as its sibling — and because the answer is single-valued, the
absent case prints visibly as `null` rather than an empty line.

::: rustup_rustup_default_toolchain
    options:
      members: []

## Listing the installed targets

[`RustupTargets`][reemote.modules.rustup.rustup.RustupTargets] runs
`rustup target list --installed` and keeps every non-blank row, de-duplicated
and sorted — the deterministic list shape the module's facts share. Without a
`toolchain` argument the listing covers the *active* toolchain, exactly what
plain `rustup` reports; with one, the fact places rustup's own `+<toolchain>`
selector where rustup parses it — as a top-level argument, *before* the
subcommand (`rustup +stable target list --installed`), the whole selector
`shlex`-quoted. This gather asks about the active toolchain and leaves the
optional `rustup_home` at its default. A toolchain that is not *installed* is
not an error: rustup exits 1, the unchecked fact read tolerates that as empty
output, and the answer is the honest `[]` — while a host without `rustup` at
all raises `CommandNotFoundError` from the bare command, which is why the
script awaits `provision_rustup.py` before gathering.

::: rustup_rustup_targets
    options:
      members: []

## Listing the installed components

[`RustupComponents`][reemote.modules.rustup.rustup.RustupComponents] is the
target fact's mirror for `rustup component list --installed` — rows like
`rustfmt-x86_64-unknown-linux-gnu` and `clippy-...` kept verbatim, sorted and
de-duplicated, since the target triple is part of a component's identity.
This is also the example that shows argument forwarding: it gathers with
`gather_fact(hosts, RustupComponents, "stable")`, and `gather_fact` forwards
the positional argument to every host's `process` — the `FactCache.get` rule —
so the shell asks per-toolchain about `stable` specifically. The same empty
tolerance applies: on a freshly provisioned host — installer present, no
toolchain downloaded — `stable` is not installed, rustup exits 1, and the
honest answer is `[]`.

::: rustup_rustup_components
    options:
      members: []

## Managing toolchains

The toolchain operation: [`rustup.toolchains`](../../modules/rustup.md) installs,
updates or removes rustup toolchains and selects the default, reading the
[`RustupToolchains`](../../modules/rustup.md) fact (`rustup toolchain list`) for
its idempotence — `present=True` installs only the requested names the listing
lacks, in one batch carrying the optional `profile`/`components`/`targets` flags,
and marks `default` only when it differs from the current one; `present=False`
uninstalls only the requested names it finds installed. The script installs
`stable` and `nightly` with the `minimal` profile and selects `stable`, re-runs
the identical request (the honest no-op the apply phase reports through the fresh
per-step fact cache), then removes `nightly` again — the non-default half of the
pair, so `rustup`'s own "cannot uninstall the default toolchain" guard does not
fire. Unlike the package examples this one deliberately leaves `stable` installed:
a default toolchain *is* the meaningful end state of the operation, and the
shared-host sweep is left with a usable Rust install rather than a mutation that
breaks a later example. No request is elevated — `rustup` writes the connecting
user's `~/.rustup` tree the facts read. An unknown `profile` and a `default` given
with `present=False` raise `ValueError` up front, exercised by the pytest suite.
The script awaits `provision_rustup.py` first and guards every request
`when=is_ubuntu`.

::: rustup_toolchains
    options:
      members: []

## Managing cross-compilation targets

The target operation: [`rustup.targets`](../../modules/rustup.md) adds or removes
cross-compilation target triples of one toolchain, gated on the
[`RustupTargets`](../../modules/rustup.md) fact read for exactly the toolchain the
commands mutate — `present=True` adds only the requested triples the fact lacks
(`rustup target add`, one batch), `present=False` removes only the ones it reports
installed (`rustup target remove`). The `+<toolchain>` selector rides where rustup
parses it — as a top-level argument *before* the subcommand (`rustup +stable
target add ...`), the grammar the fact itself follows. The script opens with a
`rustup.toolchains` request that merely *ensures a stable toolchain exists* (a
target verb needs an installed toolchain to extend, and a freshly provisioned
rustup has none), then adds `x86_64-unknown-linux-musl` to `stable`, re-runs the
add as a no-op, and removes the target again — fully self-restoring for the
toolchain's target list. Unprivileged, `provision_rustup.py` awaited first, every
request `when=is_ubuntu`.

::: rustup_targets
    options:
      members: []

## Managing toolchain components

The component operation: [`rustup.components`](../../modules/rustup.md) is the exact
sibling of `rustup.targets` for `rustup component add`/`remove`, gated on the
[`RustupComponents`](../../modules/rustup.md) fact — component names carry their
target suffix verbatim (`rustfmt-x86_64-unknown-linux-gnu`), so the fact reports
what rustup prints and the comparison is name-for-name (the bare `clippy`/`rustfmt`
names this script uses are exactly what a host toolchain reports). The setup
request installs `stable` with the `minimal` profile — minimal deliberately omits
the lint components, so the add below has real work to do — then adds `clippy` and
`rustfmt`, re-runs the add as a no-op, and removes them again (`present=False`),
leaving the component list where the setup step left it. Same `+<toolchain>`
top-level placement, same unprivileged request, `provision_rustup.py` awaited
first, every request `when=is_ubuntu`.

::: rustup_components
    options:
      members: []
