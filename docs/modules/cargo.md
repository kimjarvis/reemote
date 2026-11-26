# cargo module

::: reemote.modules.cargo.cargo
    options:
      show_root_heading: false
      members:
        - CargoPackageInfo
        - CargoPackages
        - packages



<!---

`reemote.modules.cargo.cargo` holds the cargo facts and the `cargo.packages`
operation. `CargoPackages` reports
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

--->
