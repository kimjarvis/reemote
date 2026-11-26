# go module

::: reemote.modules.go.go
    options:
      show_root_heading: false
      members:
        - GoPackageInfo
        - GoPackages
        - GoVersionInfo
        - GoVersion
        - GoEnv
        - GoInstalledSDKs
        - GoModInfo
        - GoModuleInfo
        - GoBuildInfo
        - GoBinaryBuildInfo
        - packages



<!---

`reemote.modules.go.go` holds the go facts and the `go.packages` operation.
`GoPackages` reports the Go
binaries installed via `go install` as `GoPackageInfo` entries — the binary's
main-package import `path` plus one module `version` — parsed from the
`go version -m` rows the fact prints for every regular executable in the go
bin directory (`GOBIN`, else `$GOPATH/bin`). It mirrors the baseline's
`go.GoPackages` fact and runs its compound command verbatim: the listing
resolves the bin directory in the shell, short-circuits to nothing when that
directory is absent, and `2>/dev/null` hides `go version` failures on binaries
it cannot read. `parse` keeps each binary's `path` row paired with the version
of the `mod` row that follows it; `dep` and `build` rows, the per-binary
`<file>: go1.x` header lines and blank lines contribute nothing, and a
`(devel)` version — `go build` output, not an install — is skipped so only
versioned installs are reported. A per-binary header closes the current path,
so a `mod` row never attaches to the previous binary's `path`.

Two deliberate deviations beyond the parser. The baseline returns a
`dict[str, set[str]]` keyed by import path; reemote reports sorted, unique
`GoPackageInfo` entries — the deterministic, JSON-printable answer shape every
package-list fact here uses (the `GemPackages` precedent) — with one entry per
path/version pair, since two binaries in the go bin directory can carry the
same module built at different versions. And the baseline's
`requires_command = "go"` gate is dropped, but unlike the bare-command facts
(where a missing tool exits 127 as `CommandNotFoundError`) nothing is heard
about: the command is self-guarding end to end, so on a host without go the
substitutions and `find -exec` failures stay hidden, the exit is 0 or 1 rather
than 127, and fact gathering reads the unparsable silence as `[]` — exactly
the answer the baseline's `default = dict` gate produced. A host with the
tooling but no `go install` binaries answers `[]` the same honest way.

`GoVersion` asks a different question — the active toolchain itself: its
version and default target platform, so an operation can check a minimum
version or a target architecture before building from source. One guarded
shell runs `go version && go env GOOS GOARCH` and the answer is a
`GoVersionInfo` entry: the `version` with its `go` prefix stripped, the full
header line kept `raw`, plus the `goos`/`goarch` pair. Because positional
`go env KEY...` prints bare values, the parser reads the three lines
positionally rather than as `KEY=` rows. The design proposed a
`requires_command = "go"` gate reporting absence as `{}`; reemote drops the
gate the way `GoPackages` does — the command leads with
`command -v go || exit 0`, so a host without go reads empty output and never
exits 127 — and a single-valued fact answers `None` when absent (the `Which`
precedent), so callers branch on the answer directly. A `devel` toolchain
header still answers, with an empty `version` and the raw line intact.

`GoEnv` reads the effective `go env` configuration as a `KEY -> value`
mapping — hosts override `GOPATH`, `GOBIN`, `GOMODCACHE` or `GOPROXY` in
profiles, and reading `go env` avoids hardcoding the defaults. The shell is
always the full `go env` listing (also the cache-key-stable choice); a
filtered read names its `keys` and narrows the parsed mapping in `process`,
keeping the requested order and dropping unset variables. That is a fix to
the design's filtered form, which ran `go env <KEY>...` — a command that
prints bare values with no `KEY=` prefix, unparseable back into a mapping.
`go env` single-quotes every value; the parser strips one matched outer
quote pair and otherwise splits on the first `=` only, so a `GOPROXY`
comma or a `GOFLAGS` space survives whole. Absence — a host without go — is
the natural empty `{}`.

`GoInstalledSDKs` lists the side-by-side SDK binaries under a set of search
directories (the design's `/usr/local` and `/opt` by default) as version ->
path, for hosts that keep several toolchains installed. A POSIX loop probes
every executable `bin/go` under a `go*`-prefixed or any other subdirectory
and prints `<path>TAB<version line>`; no toolchain gate is needed because
the loop never invokes `go` itself, only the SDKs it finds. One path
matching both glob shapes lands twice and collapses in the mapping, two SDKs
reporting the same version share the key (the later scan wins, the design's
rule), and a `devel` build — no `go`-prefixed version field — contributes
nothing. The design's own loop was mis-quoted and cannot parse; this is the
equivalent working shell, each search directory `shlex`-quoted.

`GoModuleInfo` reports a module directory's `go.mod` metadata — the
`module` path, the `go` directive, and its `require` mapping — so an
operation can decide `go mod download`/`go mod verify` or a rebuild without
parsing the file by hand. Instead of the design's awk excerpt (which could
not reproduce block-form `require ( ... )` or the `// indirect` markers
faithfully, and which it inconsistently paired with `go env -json` —
environment, not module metadata), the guarded command asks go itself for
the authoritative machine view: `cd '<path>' && go mod edit -json`, which
performs no network access. Only direct requirements are mapped, the
design's stated intent; every failure mode (missing go, missing directory,
no or unparsable `go.mod`) hides its stderr and reaches the same empty
output, which reads `None` — the single-valued rule, where the design
proposed `{}`.

`GoBinaryBuildInfo` inspects a compiled binary on disk through
`go version -m <path>`, answering which compiler built it (`go_version`, the
`go` prefix kept verbatim here) from which module (`module_path` and
`module_version`, the `GoPackageInfo` vocabulary rather than the design's
one joined `mod` string) and with which settings — so a check no longer
relies on mtimes or checksums. `build` rows arrive in two printed forms
(verified against real go output): `-flag=value` inside one column, and the
long `vcs.*` fields as a separate tab column; both map into the same
`settings` dictionary, a flag name's leading `-` stripped. Unlike the
`GoPackages` install listing a `(devel)` version is kept here — a
`go build` artifact still reports its provenance, which is exactly the
question this fact answers — and `dep` rows contribute nothing. A missing
toolchain, missing file or non-Go binary all read `None`.

`go.packages` is the operation counterpart of the baseline's `go.packages`. It
installs, upgrades or removes `go install` binaries given as bare import paths
or `<pkg>=<version>` pins. Its idempotence reads `GoPackages` once: a pinned
request is present only when that *exact* version is installed (the
`ensure_packages` rule `apt.packages` and `npm.packages` also follow), while an
unpinned one — and one pinned to the special `latest` — is present when the
path is installed at any version. The pin spelling is reemote's one
user-facing format, `<pkg>=<version>`, translated for the shell by
`_go_package_spec` into go's own `<pkg>@<version>` token (npm's `=`-to-`@`
precedent); the baseline's `@` input spelling is therefore not accepted, and
an unpinned request renders `@latest` exactly as the baseline emits it.

`latest=True` keeps the baseline's documented non-idempotence: with no
registry to compare against, an installed unpinned request re-issues
`go install <pkg>@latest` and reports changed on every run (the legacy latest
behavior `pip.packages`/`gem.packages` follow, deliberately unlike the
version-checked `latest` of `apt.packages`). Install and upgrade batches are
yielded as two separate `GoInstallCommand`s, installs first, mirroring the
baseline's two identical `go install` commands — go has no update verb — and a
pinned request joins the install batch when missing but is never an upgrade
candidate.

Removal is name-based, the baseline verbatim and deliberately unlike the
version-aware removal of `apt`/`gem`: a compiled binary carries no per-version
state, so `rm -f` cannot select or spare a version — a pin on removal requests
the path, not the pin. The `GoRemoveCommand` resolves the bin directory in the
shell (`GOBIN`, else `$GOPATH/bin`, the fact's own guard) and removes
`"$BINDIR"/<binary>` for each requested path, where the binary name drops a
trailing Semantic-Import-Versioning `/vN` segment (`github.com/foo/bar/v2`
installs `bar`). Two distinct import paths can share a binary name — mirroring
how `go install` itself clobbers one with the other — so removals dedup
requested paths but not resolved names.

Both commands act on the connecting user's go tree, so the operation runs
unprivileged: flagging `sudo=True` would install into root's `GOPATH`, a tree
the unprivileged fact read never sees. The `GoInstallCommand`/`GoRemoveCommand`
descriptors render every package token `shlex`-quoted, byte-identical to the
baseline's `QuoteString` output. All three baseline options map — `packages`,
`present` and `latest` — with the single-package-as-string shorthand and the
`packages=None` no-op kept; `go` has no further baseline options to drop.

--->
