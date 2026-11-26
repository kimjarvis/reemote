# go module

::: reemote.modules.go.go
    options:
      show_root_heading: false
      members:
        - GoPackageInfo
        - GoPackages



<!---

`reemote.modules.go.go` holds the go fact. `GoPackages` reports the Go
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

--->
