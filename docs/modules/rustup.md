# rustup module

::: reemote.modules.rustup.rustup
    options:
      show_root_heading: false
      members:
        - RustupToolchainInfo
        - RustupToolchains
        - RustupDefaultToolchain
        - RustupTargets
        - RustupComponents
        - toolchains
        - targets
        - components



<!---

`reemote.modules.rustup.rustup` holds the rustup facts. The module was
specified by a design note written in pyinfra idiom, but no such facts exist
in any pyinfra release, so there is no baseline parser to mirror — the note
is the whole specification, and the places where it mis-states rustup's own
behaviour are fixed here rather than copied.

`RustupToolchains` reports the toolchains installed under a `RUSTUP_HOME` as
a toolchain-name -> `RustupToolchainInfo` mapping — whether each one is the
default, plus its release channel — parsed from `rustup toolchain list`. Two
bugs in the note's parser are fixed. Its `"(default)" in line` test and
`replace("(default)", "")` strip cannot see rustup's `(active, default)`
marker: rustup prints `<name> (active, default)` when the default toolchain
also answers in its own directory (upstream `rustup_mode.rs` renders exactly
`(active, default)`, `(active)` or `(default)`), and the naive replace would
leave `<name> (active,` glued onto the key. These facts detach any trailing
parenthesized marker instead: the name is what precedes it, and the
toolchain is the default when the marker's comma-separated flags mention
`default`. And the note's bare `startswith` channel test files a custom
`nightlyish` toolchain under `nightly`; `_toolchain_channel` requires the
channel to be a whole leading field, so `nightly-2026-05-01-...` is nightly
(as rustup itself resolves it) while `nightlyish` is `custom` — versioned
installs like `1.83.0-x86_64-...` are `custom` exactly as the note maps
only the three named channels. The result is sorted by name, the
deterministic answer shape every collection fact here uses.

`RustupDefaultToolchain` is the note's convenience short fact — the default
toolchain's name, or `None` when none is marked — so callers do not scan the
mapping themselves. reemote has no `ShortFactBase`; a short fact is simply a
fact that composes its sibling (the `hardware.DefaultIpv4Address` /
`NetworkDevices` precedent): `process` delegates to `RustupToolchains` and
projects the marked entry, so it issues no shell of its own, needs no
command or seam of its own, and on a simulated host the two facts can never
disagree the way two independently seeded stores could.

`RustupTargets` and `RustupComponents` list one toolchain's installed target
triples and component names through rustup's own
`target list --installed` / `component list --installed` verbs; without a
`toolchain` the listing covers the *active* toolchain — the default, or a
directory override. The note appended its `+<toolchain>` selector *after* the
subcommand, which rustup rejects: `+toolchain` is a top-level argument
parsed before the subcommand (upstream declares it on the root command), so
the builder renders `rustup +stable target list --installed`, the whole
selector `shlex`-quoted as one token. The note also promised "sorted target
triples" while returning lines in the tool's own order; the sort — with
dedup — is actually applied here, and component rows keep rustup's
target-qualified names (`rustfmt-x86_64-unknown-linux-gnu`) verbatim.

All four read bare `rustup` commands, so the note's `requires_command =
"rustup"` gate is deliberately dropped the way the cargo module drops its
counterpart: a host without rustup exits 127 and the gather raises
`CommandNotFoundError` — an environment without the toolchain is an error the
deployer hears about — while a host *with* rustup but nothing installed (or
a toolchain that is not installed, whose error rustup prints on stderr and
the unchecked fact read tolerates) answers the note's `default` empty `dict`
/ `list`. The optional `rustup_home` rides along as a `RUSTUP_HOME=<quoted>`
env-assignment prefix on every shell.

The three operations — `rustup.toolchains`, `rustup.targets` and
`rustup.components` — come from the same design note (no pyinfra `rustup`
exists; reemote is the origin of this surface) and read the facts above for
their idempotence. `rustup.toolchains` installs, updates or removes toolchains
and selects the default: `present=True` installs only the requested names the
`RustupToolchains` read lacks, in one batch carrying the optional
`--profile`/`--component`/`--target` flags, and `present=False` uninstalls only
those still installed. The read keys on the fully-qualified names
`rustup toolchain list` prints (`stable-x86_64-unknown-linux-gnu`), so every
comparison resolves a request's short alias through the same leading-field rule
the channel derivation uses — guarded at the separator so a bare `nightly` is
not satisfied by a dated `nightly-2026-05-01-…` install, which rustup treats as
a distinct toolchain — while the yielded commands keep carrying the alias rustup
resolves on the host. A `default` differing from the current one yields
`rustup default <toolchain>` — the current default derived from that same
read rather than awaiting `RustupDefaultToolchain`, whose process-level
composition would issue a second shell outside the fact cache for an answer
this one already holds. `profile` is validated against rustup's three
documented values and a `default` is refused with `present=False` (the
operation never installs what it marks), both up front with `ValueError` like
the cargo module's combination guards. `rustup.targets` and
`rustup.components` add or remove one toolchain's triples and components in a
single batch each, gated on `RustupTargets`/`RustupComponents` read at exactly
the `toolchain`/`rustup_home` scope the command mutates — add takes the set
difference, remove the intersection — and the `+<toolchain>` selector renders
before the subcommand, where rustup parses it, the facts' own grammar fix.
`rustup_home` forwards to the read and every descriptor as one env prefix, the
same scope invariant as `cargo.packages`. Installing a toolchain downloads
over the network and acts on the connecting user's `~/.rustup` tree, so the
request stays unprivileged; provisioning the `rustup` binary itself is the
examples' concern (see `examples/modules/rustup/provision_rustup.py`).

--->
