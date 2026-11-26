# cargo module

::: reemote.modules.cargo.cargo
    options:
      show_root_heading: false
      members:
        - CargoPackageInfo
        - CargoPackages
        - CargoConfig
        - packages
        - cache_clean



<!---

`reemote.modules.cargo.cargo` holds the cargo facts and the `cargo.packages`
and `cargo.cache_clean` operations. `CargoPackages` reports
the crates installed globally via `cargo install` as `CargoPackageInfo`
entries — the crate `name` plus one installed `version` — parsed from the
`<crate> v<version>` header rows of `cargo install --list`. It mirrors the
baseline's `cargo.CargoPackages` fact and runs its command verbatim: the
`- <path>` binary lines under each header, blank lines and stray noise match
no row and contribute nothing, and the `v` prefix is stripped from the
version, the baseline's own rule.

Two deliberate widenings of the baseline's parser. Its regex
`^([a-zA-Z0-9\-]+)\sv([0-9\.]+)` excludes `_` from a crate name, so it
silently drops crates.io names like `lazy_static`, and its version class
truncates a pre-release or build-metadata version (`1.0.0-beta.2` reads as
`1.0.0`, a version the host does not have); reemote's name class allows
letters, digits, `_` and `-`, and its version token captures the whole bare
semver — no row is lost and no wrong version is reported.

Two deviations beyond the parser. The baseline returns a
`dict[str, set[str]]` keyed by crate name; reemote reports sorted, unique
`CargoPackageInfo` entries — the deterministic, JSON-printable answer shape
every package-list fact here uses (the `GemPackages` precedent) — with one
entry per name/version pair, since `cargo install --list` prints one header
row per installed version. And the baseline's `requires_command = "cargo"`
gate silently yields its `default = dict` when cargo is missing; reemote
runs the command bare, so a missing cargo exits 127 and surfaces as
`CommandNotFoundError` — an environment without cargo is an error the
deployer hears about, not a silently empty fact (the npm/gem precedent). A
host with cargo but nothing installed still reads the natural `[]`.

`CargoConfig` answers a different question — the configuration cargo itself
reads — so an operation can check configured registries, build settings or
target paths before writing changes. Like the rustup facts it comes from a
design note rather than a pyinfra baseline. One shell resolves
`$CARGO_HOME` (default `$HOME/.cargo`, or the caller's explicit
`cargo_home`) and prints `config.toml` inside it, falling back to the legacy
`config` name — each file behind an `[ -f ]` test, deliberately not the
note's `cat ... 2>/dev/null || true` shape: a `cat` that fails on a *present
but unreadable* config keeps its non-zero exit and its stderr, instead of the
shell lying success about a read it did not perform. The unchecked fact read
tolerates that failed print as empty output either way — it parses to the
same `{}` an absent file reads — so the form records the difference rather
than erasing it.
The chain never invokes the `cargo` binary, so no host can answer 127 here —
the note's `requires_command = "cargo"` gate is dropped at no cost, and a
missing `CARGO_HOME` is just an absent file. Parsing is control-side stdlib
`tomllib` over the whole printed document (both config names are TOML; the
legacy file differs only in name), and the answer is the parsed mapping.
The note is silent on malformed content; like every fact parser here
`parse` is total, so unparseable text — like no file at all — reads the
note's `default = dict` `{}`: an empty answer means "no readable
configuration to compare against", never a crash mid-gather.

`cargo.packages` is the operation counterpart of the baseline's `cargo.packages`.
It installs, upgrades or removes `cargo install` crates given as bare crate names
or `<pkg>=<version>` pins. Its idempotence reads `CargoPackages` once: a pinned
request is present only when that *exact* version is installed (the
`ensure_packages` rule `apt.packages` and `npm.packages` also follow), while an
unpinned one — and one pinned to the special `latest` — is present when the
crate is installed at any version. The pin spelling is reemote's one
user-facing format, `<pkg>=<version>`, translated for the shell by
`_cargo_package_spec` into cargo's own `<pkg>@<version>` token (npm's
`=`-to-`@` precedent); the baseline's `@` input spelling is therefore not
accepted. An unpinned request — and a `latest` pin — renders as the *bare*
crate name, deliberately unlike go's `@latest`: cargo accepts bare names and
resolves the newest version from the registry, and crates.io has no `latest`
version to name, so the baseline would emit a literal `react@latest` token
cargo rejects.

`latest=True` keeps the baseline's documented non-idempotence: with no registry
to compare against, an installed unpinned request re-issues
`cargo install <crate>` and reports changed on every run (the legacy latest
behavior `pip.packages`/`gem.packages` follow, deliberately unlike the
version-checked `latest` of `apt.packages`). Install and upgrade batches are
yielded as two separate `CargoInstallCommand`s, installs first, mirroring the
baseline's two identical `cargo install` commands — cargo has no update verb —
and a pinned request joins the install batch when missing but is never an
upgrade candidate.

Removal is version-aware, the baseline verbatim and deliberately unlike go's
name-based one: cargo tracks its global installs, so a bare name removes the
crate when it is installed at any version while a pin fires only for that
*exact* installed version — the baseline's own `remove_packages` fixture leaves
a `git@1.1` request untouched while git sits at 1.0 — and each token keeps its
pin in the command (`cargo uninstall react test@1.1 ansible`), cargo selecting
the version through its package-ID spec. Exact duplicate tokens collapse to
one, in request order, keeping the request sequence deterministic.

Both commands act on the connecting user's cargo tree, so the operation runs
unprivileged: flagging `sudo=True` would install into root's `~/.cargo`, a tree
the unprivileged fact read never sees. The `CargoInstallCommand` and
`CargoRemoveCommand` descriptors render every package token `shlex`-quoted,
byte-identical to the baseline's `QuoteString` output. All three baseline
options map — `packages`, `present` and `latest` — with the
single-package-as-string shorthand and the `packages=None` no-op kept; `cargo`
has no further baseline options to drop.

Beyond the baseline, a design note extends `cargo.packages` toward real CI and
deployment installs, and these options are the module's first non-pyinfra
surface. `locked`, `features` (one comma-joined `--features`), `all_features`
and `no_default_features` ride on every install command of the request — they
choose *how* the crate is built, so the version comparison is untouched and a
changed feature selection on an installed crate is what `latest` re-installs.
`git` installs from a repository (`cargo install --git <url> [crate…]`, whose
cargo grammar takes *names*, so a pin drops to its bare name) and, like
`latest`, is deliberately non-idempotent — with no installed-source revision
view to compare, a git request always re-yields, always with `--force`: the
re-run installs over the binary its previous git install left, which cargo
refuses unprompted; it means only the install
direction, so a `present=False` request uninstalls by name and ignores it.
`branch`/`tag`/`rev` are cargo's mutually exclusive ref selectors for `--git`;
a request carrying two, or a selector without a source, is a static argument
refused up front with `ValueError` (the note's pyinfra-idiomatic
`OperationError` mapped to the gem/npm combination-validation precedent) so
the plan fails rather than the SSH shell. `cargo_home` scopes the whole
request, forwarding to the `CargoPackages` read *and* both descriptors as one
`CARGO_HOME=<quoted>` env prefix — the `gem.packages` invariant that the fact
reads and the commands mutate one tree. The note's `latest` re-install also
does not exist in pyinfra and adopts cargo's own `--force`: without it cargo
refuses to overwrite a current
crate's binary, so a bare re-install could never actually update on a host
where the crate is already present. The same refusal decides the plain install
batch: it carries `--force` whenever a requested pin *moves* an installed
crate to another version (overwriting that crate's binary), and stays
unprompted when every requested crate is new — a divergence from pyinfra's
literal `add_packages` fixture command, which would fail the same way on a
real host.

`cargo.cache_clean` empties cargo's caches or one project's build directory.
It is a design-note operation ported *without* the note's `min_bytes` size
gate — the exact call `go.cache_clean` made for its `min_total_mb`: the gate
would read a `CargoCacheSize` fact that does not exist here, so rather than
add a fact for a threshold no caller asked for, the operation stays stateless
like `dnf.clean`, leaving the caller to gate it in the deploy (a `gather_fact`
of disk usage plus a `when` predicate reaches the same decision). A `path`
requests `cargo clean --manifest-path <path>/Cargo.toml` (cargo's own verb for
a project); with no path the selected `$CARGO_HOME` cache subdirectories are
removed — cargo offers no cache-purge command, so the guarded `rm -rf` of
`$CARGO_HOME/registry` and/or `$CARGO_HOME/git` (each behind an `[ -d ]` test)
is the operation's shape, never an unqualified `rm -rf $CARGO_HOME` that would
take the config file and `bin/` tree too. Both trees belong to the connecting
user, so the request stays unprivileged; selecting neither cache and giving no
path is a no-op.

--->
