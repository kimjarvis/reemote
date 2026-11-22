# Module examples: gem

Scripts under `examples/modules/gem/` — one per fact or operation of the
[`gem`](../../modules/gem.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The provisioning helper (`provision_gem.py`) is not rendered here — it is the
shared prerequisite step the scripts below await, not a per-fact or
per-operation demonstration: Ubuntu ships no `ruby`/`gem` until they are
installed, and the module deliberately drops the baseline's
`requires_command` gating, so a bare `gem` fact read on such a host fails with
`CommandNotFoundError` instead of answering an empty fact.

## Reading the installed gems

The fact on its own: `GemPackages` runs `gem list --local` and parses the
`name (versions)` rows into sorted
[`GemPackageInfo`][reemote.modules.gem.gem.GemPackageInfo] entries — a `name`
plus one installed `version`, one entry per name/version pair because gem keeps
every installed version side by side. The reader is what keeps a multi-version
row (`json (2.6.3, 2.6.1)`), a platform tag (`nokogiri (1.16.5 x86_64-linux)`)
and a default-gem annotation (`stringio (default: 3.0.1)`) that the baseline's
single-numeric-version regex silently drops; the `*** LOCAL GEMS ***` header and
blank lines contribute nothing. The script gathers the fact across the guarded
hosts with `gather_fact` — the read-only driver fan-out, no two-phase plan
involved — and prints each host's entry list as JSON. It passes none of the
fact's optional scope arguments, `user_install`/`gem_home`/`gem_path`/`gem_bin`,
so each host reports its own system store; run `gem_packages.py` first to see a
store that actually holds gems. Like the other examples it reads the shared
project-root `inventory.json` (validated up front with `validate_inventory`) and
the gather is guarded to the `"Ubuntu"` family by filtering the host list —
`gather_fact` has no `when` parameter of its own — so the other demo hosts are
named in a `== Skipped (when guard is_ubuntu): ... ==` line and never connected.

::: gem_gem_packages
    options:
      members: []

## Installing and removing gems

The package operation: `gem.packages` installs (or with `present=False` removes)
RubyGems packages by bare name or `<pkg>=<version>` pin, reading the
[`GemPackages`][reemote.modules.gem.gem.GemPackages] fact (`gem list --local`) —
a pinned request is present only when that *exact* version is one of the
installed ones and an unpinned one when the name is installed at any version, so
re-running is a no-op. The script installs one pinned gem (`rainbow=3.1.1`)
beside one unpinned one (`paint`), then removes both again — a self-restoring
cycle that leaves the host's system store as it found it — yielding each request
twice so the second of each pair is a guaranteed no-op in the apply phase
through the fresh per-step fact cache. The pin is reemote's one user-facing
spelling, shared with apt, dnf, apk and npm; it rides through to the install
shell translated into gem's own grammar as `rainbow:3.1.1`, while removal
renders `--version 3.1.1` for the pin (gem's uninstall has no `name:version`
token) and the bare-name `-a` for `paint` — both non-interactive with `-x` and
`-I`. Every request carries `sudo=True`, because a system-wide install writes
into gem's root-owned `GEM_HOME`; the fact read is unprivileged. The scope
parameters are left alone here: `user_install` targets the login user's own gem
directory (no `sudo` needed), and `gem_home`/`gem_path`/`bindir`/`gem_bin`
relocate the store or name a non-default executable — `user_install` and an
explicit `gem_home`/`gem_path` are mutually exclusive, so the operation refuses
the ambiguous combination with a `ValueError`. `provision_gem.py` installs the
`ruby-rubygems` deb with `apt.packages` before the deploy runs, so the example
works even on a host where gem was never installed. Like the other examples the
script reads the shared project-root `inventory.json` (validated up front with
`validate_inventory`) and every request carries `when=is_ubuntu` — on a
non-Ubuntu host neither the `gem` commands nor the fact behind them run, and plan
and log render `[skipped: when guard is_ubuntu]`.

::: gem_packages
    options:
      members: []
