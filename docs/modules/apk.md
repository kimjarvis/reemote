# apk module

## apk Facts

::: reemote5.modules.apk.apk
    options:
      show_root_heading: false
      members:
        - ApkAutoremovablePackages
        - ApkAutoremovablePackage
        - ApkExplicitPackages
        - ApkExplicitPackage
        - ApkHeldPackages
        - ApkHeldPackage
        - ApkInstalledPackages
        - ApkInstalledPackage
        - ApkPackage
        - ApkPackageInfo
        - ApkRepositories
        - ApkRepository

## apk Operations

::: reemote5.modules.apk.apk
    options:
      show_root_heading: false
      members:
        - autoremove
        - hold
        - packages
        - virtual



<!---

`reemote5.modules.apk.apk` combines the apk facts and the apk operations in a
single source. The facts are `ApkAutoremovablePackages`, the orphaned packages an
`apk add --purge` would remove — each one an `ApkAutoremovablePackage` dict
naming it and its version-and-release — parsed from the `(N/M) Purging
<name>-<version>` rows of its `--simulate` sibling, `ApkInstalledPackages`,
the installed Alpine Linux (apk) packages as `ApkInstalledPackage` entries of
a name and its versions, parsed from the
`name-version arch {origin} (license) [installed]` rows of
`apk list --installed`, `ApkExplicitPackages`, the explicitly installed
(world) packages as `ApkExplicitPackage` name-only entries parsed line
by line from `/etc/apk/world`,
`ApkHeldPackages`, the version-pinned (held) packages as `ApkHeldPackage`
entries of name, constraint and repository tag parsed from the same
`/etc/apk/world`,
`ApkPackage`, one package's detailed metadata (version, description, size,
dependencies, provides) parsed from the labelled field sections of
`apk info -a`, and `ApkRepositories`, the configured repositories as
`ApkRepository` entries of URL and repository tag parsed line by line from
`/etc/apk/repositories`. Beside the facts lives the
`apk.autoremove` operation, which removes the orphaned packages
`ApkAutoremovablePackages` names with `apk add --purge`, the
`apk.packages` operation, which installs, upgrades or removes packages by
name, reading `ApkInstalledPackages` for exact-version idempotence, and the
`apk.virtual` operation, a reemote5 original that manages an apk *virtual
package* group (`apk add -t <tag>` to create it, `apk del <tag>` to remove
it) with world-name idempotence against `ApkExplicitPackages`' entries, and the
`apk.hold` operation, the apk counterpart of `apt.mark`, which pins packages
back from upgrades (`apk add <pkg>=<version>` to hold, `apk add <pkg>` to
release the pin) with version-aware idempotence against `ApkHeldPackages`.
pyinfra's
remaining `apk` operations (`apk.upgrade`, `apk.key`, `apk.repo`) are not
migrated.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.
`ApkAutoremovablePackages` takes no arguments and runs
`apk add --purge --simulate 2>/dev/null` — apk's own orphan-purge
simulation, which never removes anything (the `2>/dev/null` suppresses the
index-noise warnings an offline simulation writes to stderr). It is a
reemote5 original: pyinfra ships no `apk.ApkAutoremovablePackages` counterpart
(its `apk` namespace publishes only `ApkPackages`). Only the
machine-readable `(N/M) Purging <name>-<version>-r<release>` rows are read
— the `WARNING: Ignoring APKINDEX...` index-noise lines, the
`OK: N packages, ...` summary footer and any progress chatter are never
mistaken for package rows, and each row's `name-version-release` token
splits at the same last `-<version>-r<release>` boundary `ApkInstalledPackages`
splits at, so multi-dash names like `alpine-baselayout-data` and names
carrying punctuation (`libstdc++`, `p11-kit`) stay intact — but the version
is kept, not dropped, in one `ApkAutoremovablePackage` dict per row
(`name` and `version`), the same `name`/`version` pair opening
`ApkPackageInfo`'s detail. That is one deliberate deviation from the sibling
`AptAutoremovable`, which drops the version its `Remv` rows carry and
returns a bare name list: apk's purge row always prints a version, so
there is one to keep. Entries come back sorted and de-duplicated by name,
so a deploy cleaning up after itself reads the purgable packages here —
and only collects the list; removal stays an explicit operator decision,
never a fact side effect — the `apk.autoremove` operation performs that
cleanup. `apk fix --purge`
is deliberately not used: it also reinstalls damaged packages, so its
simulation output mixes a repair plan with the purge plan, while
`apk add --purge --simulate` names exactly the orphan set. The fact
fills a coverage gap of `ApkExplicitPackages`: the world names it parses
cannot tell a legacy dependency from one still in use, so the installed
names missing from the world are candidates a cleanup would consider
removing — this fact names exactly those `apk add --purge` would purge.
The simulation needs no root, and a missing `apk` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`ApkInstalledPackages` takes no arguments and runs `apk list --installed`,
mirroring pyinfra's `apk.ApkPackages`. Each row is matched with pyinfra's
apk regex, so multi-dash names like `alpine-baselayout-data` and
`openssh-client-common` split from their `3.6.5-r0`-style
version-and-release at the last `-r<release>` boundary before the
`{origin}` tail, names carrying punctuation (`libstdc++`, `p11-kit`,
`json-c`) stay intact, and the `WARNING: Ignoring APKINDEX...: No such
file or directory` index-noise lines — which carry no `{origin}
(license)` tail — never match. The result is a list of
`ApkInstalledPackage` entries — one per installed name, its `versions` the
sorted, de-duplicated version list that pyinfra's `parse_packages` collects
into a set per name — the typed-entry shape the sibling
`ApkAutoremovablePackages`, `ApkExplicitPackages` and `ApkHeldPackages`
report, with the entries sorted by name so reads stay deterministic and
JSON-printable; `apt.AptPackages` and `dnf.DnfPackages` still report the
name-keyed mapping pyinfra documents, so apk is deliberately no longer
shape-symmetric with them. Nothing installed reads as `[]` — the empty answer
every sibling typed-list apk fact gives, in place of pyinfra's
`default = dict`.
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
`[!]name{@tag}{[<>~=]version}` notation and contributes a name-only
`ApkExplicitPackage` entry; entries come back sorted and
de-duplicated by name, the typed-list shape the sibling
`ApkAutoremovablePackages` reports with its `ApkAutoremovablePackage` dicts — only
this fact's entry keeps just the `name`, the name-only half of what
`DnfPackages` returns — so the entries' names compare
directly against `ApkInstalledPackages`' entry names — the installed
names missing from this list are the dependency-only ones an audit is
after; the sibling `ApkAutoremovablePackages` names exactly the orphans
`apk add --purge` would remove, and the `apk.autoremove` operation performs
that cleanup. The
constraint itself is deliberately dropped, exactly as `AptAutoremovable`
drops the version its simulation rows carry: a `@tag` a package may be
selected from and an `=`, `<`, `>`, `>=`, `~` or `><` version bound say
nothing about *which* packages were asked for. The version bound a *hold*
is after — the constraint this fact drops — is exactly what the sibling
`ApkHeldPackages` keeps. A leading `!` marks a
conflict constraint — a package that must NOT be installed — so it never
contributes a name, and blank lines, `#` comments and lines carrying no
name (a lone `@tag` or operator) are skipped the way the siblings skip
unparseable rows. Like the sibling entry types `ApkAutoremovablePackage` and
`ApkHeldPackage` the entries are TypedDict-shaped plain dicts, so
equality and `json.dumps` work without a dual access style. The fact is
named for the package-list family rather
than for the file it reads (`ApkWorld` would name a file, not answer a
question) and keeps apk's own vocabulary: apk documents these as the
*explicitly* installed packages, where apt calls them `manual` and dnf
`userinstalled`.

`ApkHeldPackages` takes no arguments and cats the *same* `/etc/apk/world`
`ApkExplicitPackages` reads — behind the identical `|| true`, so a host
without apk reads as `[]` — but keeps only the lines carrying a version
bound, answering the apk counterpart of `AptHeldPackages`: which packages
are pinned back from upgrades, and to what. apk has no `apt-mark hold`; it
holds a package by writing a version *constraint* into its world, so each
held line becomes one `ApkHeldPackage` entry —
`name`, the `constraint` (the operator and version kept together) and the
`tag` repository selector the line may carry (`edge` for a
`curl@edge=8.14.0-r0` line, the empty string when it carries none):

```python
[
    {"name": "curl", "constraint": "=8.14.0-r0", "tag": ""},
    {"name": "openssh", "constraint": ">~9.7_p1-r4", "tag": "edge"},
]
```

The entry type follows the namespace convention: like
`ApkAutoremovablePackage` and `ApkPackageInfo` the entries are TypedDict-shaped
plain dicts, so equality and `json.dumps` work without a dual access style —
a deliberate usability choice over the bare constraint strings this
fact returned before, the same one `apt.AptSources` made with its
`AptSource` entries over pyinfra's collapse convention, and the `tag` field
now keeps the `@tag` a name-only reading used to drop. The list shape still
deliberately differs from the sibling `AptHeldPackages`' sorted name
list: `apt-mark hold` records a bare name with no version, where an apk
pin's whole purpose is the constraint, so dropping it would make a re-pin to
a different version undetectable. A bare name (`busybox`) and a
repository-tag-only line (`nginx@edge`, a selection rather than a hold)
never contribute; comments, blank lines, a leading `!` conflict and a
nameless constraint are skipped exactly as `ApkExplicitPackages` skips them,
and the entries keep file order (a repeated name keeps the last line's
constraint at its first position). The
`apk.hold` operation reads this fact for version-aware idempotence and
writes it back through `apk add <pkg><constraint>`.

`ApkRepositories` takes no arguments and cats `/etc/apk/repositories`
behind a `|| true`, so a host without apk simply reads as `[]` — the same
missing-file tolerance `DnfRepositories` builds into its command. It is a
reemote5 original: pyinfra's `apk` namespace ships no repositories fact
(only `ApkPackages`), so the file cannot be audited or conditionally
modified through pyinfra facts. Each non-comment line names one
repository — a bare URL, or an apk repository tag (`@testing`-style, for
repositories referenced by tag) followed by its URL — and becomes one
`ApkRepository` entry, a `{"url": ..., "tag": ...}` dict with the tag the
empty string for an untagged line. `#` comments, blank lines and malformed
lines (a lone `@tag`, several URLs on one line) never contribute entries;
entries keep file order — no dedup, no sorting — like `apt.AptSources` and
`DnfRepositories`, the two sibling facts this one mirrors in shape and
command style. The return shape deliberately differs in size, not kind,
from the siblings: apk's one-line format carries only a URL and an
optional tag, where apt entries hold distribution/components/options and
dnf entries hold every INI option. Like the sibling entry types
`ApkAutoremovablePackage`, `ApkExplicitPackage` and `ApkHeldPackage` the
entries are TypedDict-shaped plain dicts named by the singular of the fact,
so equality and `json.dumps` work without a dual access style and the shape
is importable rather than anonymous.

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
at the last `-r<release>` boundary exactly as `ApkInstalledPackages` splits its
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
what `ApkInstalledPackages` parses and the configured repositories are what
`ApkRepositories` reports. Like `apt.AptPolicy`, the name is quoted with
`shlex.quote`, so a caller-supplied package name cannot break out of the
argument.


## Operations

Operations live in namespace modules so a deploy reads `apk.packages`,
mirroring pyinfra's API. Every operation is an async generator with the same
fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where it
decides a change is needed, and yields one descriptor per command that would
mutate the host.

`apk.autoremove` removes orphaned packages — those apk pulled in as
dependencies that nothing in `/etc/apk/world` requires any more — a
reemote5 original: pyinfra ships no apk autoremove, and apk itself has no
`autoremove` verb; it exposes orphan purge as the `--purge` flag of
`apk add`/`apk fix`, which reemote5 splits out into this standalone
operation (the same split [`apt.auto_remove`][reemote5.modules.apt.apt.auto_remove]
made against `apt.dist_upgrade`'s `auto_remove=True`). The
[`ApkAutoremovablePackages`][reemote5.modules.apk.apk.ApkAutoremovablePackages] fact
simulates the purge and names exactly the orphaned packages: a non-empty
list yields the real `apk add --purge` (the reason names the packages,
already sorted so both phases report identically), an empty list is a
no-op. Purging writes to the root-owned apk database, so flag the request
`sudo=True`, `doas=True` or `su=True` — stock Alpine ships no `sudo`, so
`doas` is usually the escalator on an apk host (see `apk.packages` above for
its passwordless `doas -n` prerequisites).
Yielding `apk.packages` with `present=False`
then `apk.autoremove` in one deploy composes the remove-then-cleanup flow
— each half an explicit, separately plannable step. apk uses no lock file
(the way apt's `DPkg::Lock::Timeout` guards concurrent `apt-get` runs), so
there is deliberately no `wait_for_lock` parameter — a keyword passed for
consistency with `apt.auto_remove` is absorbed by `**kwargs` with no
effect.

`apk.packages` installs, upgrades or removes packages by name (pyinfra's
`apk.packages` parity). `packages` accepts a single name, a list/tuple or
`None`; a name can be pinned as `<pkg>=<version>`, and the `ApkInstalledPackages`
fact supplies the installed versions, so a pin counts as present only when
that *exact* version is installed (pyinfra's `ensure_packages` version
match, the same rule `apt.packages` follows). `present=True` installs only
what is missing with `apk add`; `latest=True` additionally upgrades every
installed, *unpinned* name with `apk upgrade` — pyinfra's legacy behavior,
which upgrades blindly without checking whether a newer version exists.
`present=False` removes only what is installed with `apk del`. apk never
prompts, so the commands carry no `-y` flag (unlike apt's and dnf's).
Two apk-native install modifiers ride on the add/upgrade verbs:
`no_cache` adds `--no-cache` (bypass the package cache entirely — neither
read from nor write to `/var/cache/apk`) and `no_scripts` adds
`--no-scripts` (skip post-install triggers and scriptlets); both flow into
the `latest` upgrade path too, are ignored by the removal direction, and
change only how the install runs — never what `ApkInstalledPackages`
reports — so idempotence is unchanged. They are a deliberate apk-only
asymmetry: apt's
`no_recommends`/`allow_downgrades` and dnf's `nobest` are the closest
cousins, but neither tool exposes a cache-bypass or script-skip flag on its
package operation. Installing, upgrading and removing all need root, so flag
the request `sudo=True`, `doas=True` or `su=True`. Stock Alpine ships no
`sudo`, so on an apk host `doas` is usually the escalator to reach for:
reemote5 runs it passwordless as `doas -n`, which needs a `permit nopass`
rule for the login user in `/etc/doas.conf` and raises `PrivilegeError`
without one. Two pyinfra options are deliberately
dropped: `update` and `upgrade`, which prefix the operation with a
whole-system `apk update` / `apk upgrade` — reemote5 keeps the whole-system
refresh/upgrade out of the package operation, so both keywords are absorbed
by `**kwargs` with no effect.

`apk.virtual` manages an apk *virtual package* group — a reemote5 original:
pyinfra's `apk` namespace ships only `upgrade`, `update` and `packages`, with
no virtual-group management. Alpine's `apk add -t <tag> <packages>`
(`--virtual`) creates a temporary virtual package `<tag>` that depends on
`<packages>`, records `<tag>` in `/etc/apk/world`, and lets the whole group be
torn down later with a single `apk del <tag>` — the build-dependency pattern a
compile step wraps itself in so its toolchain does not linger on the finished
image. `tag` names the group, `packages` accepts a single name, a list/tuple
or `None` (a name can be pinned as `<pkg>=<version>`), and `present` selects
the direction. Idempotence reads the
[`ApkExplicitPackages`][reemote5.modules.apk.apk.ApkExplicitPackages] fact
(the world entries, name-only `ApkExplicitPackage` dicts): the group is
present exactly when `tag` matches an entry's `name`, so
`present=True` creates it with `apk add -t` only when `tag` is absent (and
only when at least one member is requested), while `present=False` removes it
with `apk del` only when `tag` is present. A deliberate simplification: the
fact's entries carry the world *names* only, so a changed member set for an
already-existing `tag` is not detected — the group reads as present and the
operation is a no-op — because apk records a virtual group's identity in
world, not its membership. Both directions render through one present-flagged
`ApkVirtualCommand` descriptor (the `server.modprobe` single-descriptor
pattern), and both write the root-owned apk database and world file, so flag
the request `sudo=True`, `doas=True` or `su=True` — stock Alpine ships no
`sudo`, so `doas` is usually the escalator on an apk host (see its
passwordless `doas -n` prerequisites above).

`apk.hold` holds packages back from upgrades, or releases held ones — the apk
counterpart of [`apt.mark`][reemote5.modules.apt.apt.mark]: pyinfra ships no
apk hold, and apk has no `apt-mark` verb, so it keeps a package out of
upgrades by pinning a version *constraint* into `/etc/apk/world`. `packages`
accepts a single name or a list/tuple, `present` selects the direction, and
`version` is the optional constraint to pin to — a bare `1.2.3-r0`
(normalized to `=1.2.3-r0`) or an operator-led `~1.2`/`<2.0`; omitted with
`present=True` it pins each name to its currently installed version
(`ApkInstalledPackages`' newest). Idempotence reads the
[`ApkHeldPackages`][reemote5.modules.apk.apk.ApkHeldPackages] fact
(a list of `ApkHeldPackage` entries, looked up by `name` and compared on the
entry's `constraint`):
`present=True` re-adds with `apk add <pkg>=<version>`
only the names whose current pin differs from the wanted constraint (a name
already pinned to it is a no-op), while `present=False` re-adds the bare
`apk add <pkg>` only for the names currently held, rewriting the world line
without a bound so the package stays installed and only the pin goes. Both
directions render through one present-flagged `ApkHoldCommand` descriptor
(the `apk.virtual` single-descriptor pattern — both are the same `apk add`
verb). A deliberate consequence of pinning to the installed version: with
`version` omitted, a name that is *not* installed cannot be pinned and is
reported but not held — pass `version` to pin a package that is not yet
installed. Both directions write the root-owned apk database and world file,
so flag the request `sudo=True`, `doas=True` or `su=True` — stock Alpine
ships no `sudo`, so `doas` is usually the escalator on an apk host (see its
passwordless `doas -n` prerequisites above).

--->


<!---

## Example

The read-only fact-gathering scripts under
[`examples/modules/apk/`](../examples/modules/apk.md) load the shared
project-root inventory `inventory.json` — validated with `validate_inventory`
before any host is materialized — and guard the gather with an `is_alpine`
`when` predicate that filters the host list before `gather_fact`'s fan-out,
since `gather_fact` takes no `when` parameter of its own. They gather
`ApkInstalledPackages` (`apk_apk_packages.py`), `ApkExplicitPackages`
(`apk_apk_explicit_packages.py`), `ApkAutoremovablePackages`
(`apk_apk_autoremovable.py`), `ApkRepositories`
(`apk_apk_repositories.py`) or `ApkPackage` for one package
(`apk_apk_package.py`) on the Alpine hosts only with
[`gather_fact`][reemote5.drivers.gather_fact], printing each result as
JSON.

The `apk_packages.py` script beside them demonstrates the `apk.packages`
operation: it installs packages by name with exact-version idempotence,
yielding the same request twice so the second is a guaranteed no-op in the
apply phase. The `apk_autoremove.py` script demonstrates `apk.autoremove`
the same way — it yields the purge request twice so the second is a
guaranteed no-op once the first cleanup left nothing purgable. Both are
rendered on the [apk examples page](../examples/modules/apk.md).

--->