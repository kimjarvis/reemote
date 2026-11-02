# apk module

`reemote5.modules.apk.apk` holds the apk facts — `ApkPackages`, the
installed Alpine Linux (apk) packages with their versions, parsed from the
`name-version arch {origin} (license) [installed]` rows of
`apk list --installed`, `ApkExplicitPackages`, the explicitly installed
(world) package names parsed line by line from `/etc/apk/world`,
`ApkPackage`, one package's detailed metadata (version, description, size,
dependencies, provides) parsed from the labelled field sections of
`apk info -a`, and `ApkRepositories`, the configured repositories parsed
line by line from `/etc/apk/repositories`. It is a fact-only namespace so
far: pyinfra's `apk` operations (`apk.packages`, `apk.upgrade`, `apk.key`,
`apk.repo`) are not migrated, so no operations live here yet.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.
`ApkPackages` takes no arguments and runs `apk list --installed`,
mirroring pyinfra's `apk.ApkPackages`. Each row is matched with pyinfra's
apk regex, so multi-dash names like `alpine-baselayout-data` and
`openssh-client-common` split from their `3.6.5-r0`-style
version-and-release at the last `-r<release>` boundary before the
`{origin}` tail, names carrying punctuation (`libstdc++`, `p11-kit`,
`json-c`) stay intact, and the `WARNING: Ignoring APKINDEX...: No such
file or directory` index-noise lines — which carry no `{origin}
(license)` tail — never match. The result is a dict of package name →
sorted, de-duplicated version list — the list-of-versions shape pyinfra
documents; pyinfra's `parse_packages` collects versions into a set per
name, reemote5 returns the sorted list so reads stay deterministic and
JSON-printable, the same shape `apt.AptPackages` and `dnf.DnfPackages`
report. Nothing installed reads as `{}` (pyinfra's `default = dict`).
pyinfra's `requires_command` gating on `apk` is deliberately dropped: a
missing `apk` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError]. Whether a
row names a package the operator asked for or one apk pulled in as a
dependency is invisible in it too: `ApkExplicitPackages` reports the world
names. Neither a row's `{origin}` nor its `apk list` presence says anything
about a single package's dependencies, size or description either:
`ApkPackage` queries one package's detail directly.

`ApkExplicitPackages` takes no arguments and cats `/etc/apk/world` — apk's
*world*, the list of constraints the package selection must fulfil —
behind a `|| true`, so a host without apk reads as `[]`, the same
missing-file tolerance `ApkRepositories` builds into its command. Like
that fact it is a reemote5 original: pyinfra's `apk` namespace ships only
`ApkPackages`, so nothing upstream separates the packages an operator
explicitly installed from the ones that came in as dependencies. Each
line of the file is one dependency constraint in
`[!]name{@tag}{[<>~=]version}` notation and contributes its bare package
name; names come back sorted and de-duplicated, the shape the apt-side
name-list facts `AptHeldPackages` and `AptAutoremovable` report and the
name-only half of what `DnfPackages` returns, so the result compares
directly against `ApkPackages`' keys — the installed names missing from
this list are the dependency-only ones an audit is after (apk has no
autoremove, so cleanup stays an explicit operator decision). The
constraint itself is deliberately dropped, exactly as `AptAutoremovable`
drops the version its simulation rows carry: a `@tag` a package may be
selected from and an `=`, `<`, `>`, `>=`, `~` or `><` version bound say
nothing about *which* packages were asked for. A leading `!` marks a
conflict constraint — a package that must NOT be installed — so it never
contributes a name, and blank lines, `#` comments and lines carrying no
name (a lone `@tag` or operator) are skipped the way the siblings skip
unparseable rows. The fact is named for the package-list family rather
than for the file it reads (`ApkWorld` would name a file, not answer a
question) and keeps apk's own vocabulary: apk documents these as the
*explicitly* installed packages, where apt calls them `manual` and dnf
`userinstalled`.

`ApkRepositories` takes no arguments and cats `/etc/apk/repositories`
behind a `|| true`, so a host without apk simply reads as `[]` — the same
missing-file tolerance `DnfRepositories` builds into its command. It is a
reemote5 original: pyinfra's `apk` namespace ships no repositories fact
(only `ApkPackages`), so the file cannot be audited or conditionally
modified through pyinfra facts. Each non-comment line names one
repository — a bare URL, or an apk repository tag (`@testing`-style, for
repositories referenced by tag) followed by its URL — and becomes a
`{"url": ..., "tag": ...}` dict, the tag the empty string for an
untagged line. `#` comments, blank lines and malformed lines (a lone
`@tag`, several URLs on one line) never contribute entries; entries keep
file order — no dedup, no sorting — like `apt.AptSources` and
`DnfRepositories`, the two sibling facts this one mirrors in shape and
command style. The return shape deliberately differs in size, not kind,
from the siblings: apk's one-line format carries only a URL and an
optional tag, where apt entries hold distribution/components/options and
dnf entries hold every INI option.

`ApkPackage` takes a package name and runs `apk info -a <package>`, which
prints every field apk knows about the package as a
`<name>-<version> Label:` header followed by its value lines. The tracked
sections become one `ApkPackageInfo` dict — `name`, `version`,
`description`, `url`, `license`, `size` and the `depends`/`provides`
lists. apk capitalizes most labels (`Description`, `Installed size`,
`Webpage`, `Depends on`, `Provides`) but spells one lower-case (`is
required by`), so labels are matched case-insensitively and mapped onto
the result keys (`Webpage` → `url`, `Installed size` → the `size` int,
`Depends on` → `depends`); the version-and-release is split from the name
at the last `-r<release>` boundary exactly as `ApkPackages` splits its
rows, so multi-dash names (`alpine-baselayout-data`) stay intact. Every
other `-a` section apk prints — the `Contains` file listing, the
`is required by` reverse dependencies, `triggers` — is deliberately not
tracked: any header closes the field being collected, so those sections
contribute nothing rather than leaking into the last tracked list. An
absent field reads as its empty default (`""`, `0`, or `[]`). This is a
reemote5 original — pyinfra's `apk` namespace ships only `ApkPackages`,
whose `apk list --installed` rows report only whether a name is installed
and at which version, never its dependencies, size or description, so
nothing upstream answers the version-comparison or dependency-verification
question this fact fills. An absent package — or a host without apk —
prints no field header (the command's `2>/dev/null || true` swallows the
non-zero exit), so it reports `None`, the `apt.DebPackage` and
`dnf.RpmPackage` None-on-absent convention rather than pyinfra's
empty-dict answer. One deliberate gap against the sibling `apt.AptPolicy`
(which surfaces each version's repository origins): apk's `apk info`
prints no repository field at all, so this fact reports the package's own
metadata only — the `{origin}` source package of every installed name is
what `ApkPackages` parses and the configured repositories are what
`ApkRepositories` reports. Like `apt.AptPolicy`, the name is quoted with
`shlex.quote`, so a caller-supplied package name cannot break out of the
argument.

::: reemote5.modules.apk.apk
    options:
      members:
        - ApkExplicitPackages
        - ApkPackage
        - ApkPackageInfo
        - ApkPackages
        - ApkRepositories

## Example

The read-only fact-gathering scripts under
[`examples/modules/apk/`](../examples/modules/apk.md) load their
inventory from `inventory_alpine.json` in the project root — validated
with `validate_inventory` before any host is materialized — and gather
`ApkPackages` (`apk_apk_packages.py`), `ApkExplicitPackages`
(`apk_apk_explicit_packages.py`), `ApkRepositories`
(`apk_apk_repositories.py`) or `ApkPackage` for one package
(`apk_apk_package.py`) on the Alpine hosts with
[`gather_fact`][reemote5.drivers.gather_fact], printing each result as
JSON.
