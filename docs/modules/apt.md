# apt module

::: reemote.modules.apt.apt
    options:
      show_root_heading: false
      members:
        - AptAutoremovable
        - AptHeldPackages
        - AptKey
        - AptKeys
        - AptPackageInfo
        - AptPackagePolicy
        - AptPackageUpgrade
        - AptPackages
        - AptPolicy
        - AptPolicyVersion
        - AptSource
        - AptSources
        - AptSourcesFile
        - AptUpdateStamp
        - AptUpgradable
        - DebPackage
        - DebPackageInfo
        - auto_remove
        - deb
        - dist_upgrade
        - key
        - mark
        - packages
        - ppa
        - repo
        - sources_file
        - update
        - upgrade


<!---

`reemote.modules.apt.apt` holds the apt facts — `AptAutoremovable`, the
no-longer-needed packages an autoremove would clean up, parsed from the
`Remv` rows of an `apt-get --just-print autoremove` simulation into plain
`AptPackageInfo` TypedDict-shaped dicts (name and a one-element versions
list), `AptHeldPackages`, the
package names marked as held via `apt-mark hold`, parsed from
`apt-mark showhold` into plain `AptPackageInfo` TypedDict-shaped dicts
with an empty versions list,
`AptKeys`, the GPG keys available to APT, gathered
from the modern keyring directories
(`/etc/apt/trusted.gpg.d`, `/etc/apt/keyrings`, `/usr/share/keyrings`) with
`gpg --with-colons` instead of the deprecated `apt-key` command into plain
`AptKey` TypedDict-shaped dicts,
`AptPackages`, the installed packages with their versions, parsed from
`apt list --installed` into plain `AptPackageInfo` TypedDict-shaped dicts,
`AptPolicy`, the install candidate and repository
origins of one package, parsed from `apt-cache policy` into a plain
`AptPackagePolicy` TypedDict-shaped dict, `AptSources`, the configured
repositories, parsed from the legacy `.list` files and the modern deb822
`.sources` files into plain `AptSource` TypedDict-shaped dicts,
`AptSourcesFile`, the raw content of one apt sources file (None when it is
absent), read behind a `test -e`-guarded marker,
`AptUpdateStamp`, the mtime of apt's `/var/lib/apt/periodic/
update-success-stamp` file as a *naive UTC* `datetime` (None when the
stamp is missing or unreadable), parsed from `stat -c %Y` and read by
the `cache_valid_time` freshness gate `apt.packages(update=True, ...)`
and `apt.update(...)` share, and
`AptUpgradable`, the packages `apt upgrade` would move with both ends of
each version jump, parsed from `apt list --upgradable` into plain
`AptPackageUpgrade` TypedDict-shaped dicts, `DebPackage`, the name and
version of a `.deb` file or an installed dpkg package, parsed from
`dpkg -I`/`dpkg -s` into a plain `DebPackageInfo` TypedDict-shaped dict,
and `SimulateOperationWillChange`, the proposed change
counts of a simulated `apt-get` operation, parsed from the `--dry-run`
summary line into a plain `AptSimulationDict` TypedDict-shaped dict.
Beside the facts live the operations: `apt.deb`, which installs or removes a
`.deb` file package, reading `DebPackage` and `AptPackages` to decide whether
the file's package is already installed at the file's exact version;
`apt.dist_upgrade`, which upgrades every package with `dist-upgrade` when
`SimulateOperationWillChange` proposes changes; `apt.auto_remove`, the
standalone cleanup of orphaned dependencies, idempotent on
`AptAutoremovable`; `apt.key`, which installs a GPG key stream as a
keyring file under `/etc/apt/keyrings/` for `signed-by=` references —
deliberately non-idempotent, like `dnf.key`; `apt.mark`, which holds
packages back from upgrades (or releases held ones) with
`apt-mark hold`/`unhold`, idempotent on `AptHeldPackages` — a reemote
original; `apt.packages`, which
installs, upgrades or removes packages by name, reading `AptPackages`
for exact-version idempotence and `SimulateOperationWillChange` to gate
its optional `upgrade`; `apt.ppa`, which adds or removes an Ubuntu
PPA with `apt-add-repository`, idempotent on `AptSources`;
`apt.repo`, which adds or removes one apt source line in
`/etc/apt/sources.list` or a `sources.list.d` file, idempotent on
`AptSources` and on the managed file's own text, and rewrites that file
through the spool-and-install pair; and `apt.sources_file`, which manages a
modern deb822 `.sources` file under `sources.list.d`, idempotent on
`AptSources` for the write direction and on `AptSourcesFile` for the
removal;
`apt.update`, the package-list refresh, non-idempotent by default (it
always yields, like the baseline's) but skippable within a window via
the Ansible-shaped `cache_valid_time` gate `AptUpdateStamp` supplies;
and `apt.upgrade`, which
upgrades every upgradable package when `SimulateOperationWillChange`
proposes changes.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `AptAutoremovable`
takes no arguments and runs `apt-get --just-print autoremove 2>/dev/null`
— a simulation that never removes anything (the `2>/dev/null` suppresses
the lock/permission warnings an unprivileged simulation writes to
stderr). It is a reemote original: the baseline ships no
`apt.AptAutoremovable` counterpart (its `apt.SimulateOperationWillChange`
only counts a simulation's changes, never names them). Only the
machine-readable `Remv <name> [<version>]` rows are read — the progress
lines, the indented names under `The following packages will be REMOVED:`
and the `0 upgraded, ...` count line are never mistaken for package
names, and each row's bracketed version is kept alongside the name as a
one-element `versions` list — the shared `AptPackageInfo` shape
`AptPackages` and `AptHeldPackages` return too. `apt-mark
showauto` is deliberately not used: it lists every auto-installed
package, including ones something still requires, while the simulation
reports exactly the no-longer-needed set. The result is a deterministic,
JSON-printable sorted list of `AptPackageInfo` entries like
`[{"name": "libllvm17t64", "versions": ["1:17.0.6-9ubuntu1"]},
{"name": "linux-headers-6.8.0-45", "versions": ["6.8.0-45.45"]}]`, with
architecture-suffixed names keeping
their suffix; nothing autoremovable reads as `[]`. The fact fills a
coverage gap of `AptPackages`: the `apt list --installed` rows cannot
tell a legacy dependency from one still in use, so a deploy cleaning up
after itself reads the removable names here — and only collects the
list; removal stays an explicit operator decision, never a fact side
effect — the `apt.auto_remove` operation performs that cleanup. The
simulation needs no root, and a missing `apt-get` exits
127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`AptHeldPackages`
takes no arguments and runs `apt-mark showhold`, which prints one held
package name per line — the names marked with `apt-mark hold`, which keeps
them out of upgrades. It is a reemote original: the baseline ships no
`apt.AptHeldPackages` counterpart. Blank lines are skipped and the entries
are de-duplicated and sorted, so the result is a deterministic,
JSON-printable list of `AptPackageInfo` entries like
`[{"name": "docker-ce", "versions": []},
{"name": "libc6:i386", "versions": []},
{"name": "linux-image-generic", "versions": []}]`,
with architecture-suffixed names keeping their suffix exactly as `apt-mark`
prints them; `apt-mark showhold` prints no version, so `versions` is
always `[]` — the shared `AptPackageInfo` shape `AptPackages` and
`AptAutoremovable` return too; nothing held reads as `[]`. The fact fills a coverage gap of
`AptPackages`: the `apt list --installed` rows that fact parses report
versions but never the hold state, so a deploy that must not upgrade
critical packages reads the held names here — and only collects the
list; marking stays an explicit operator decision, never a fact side
effect — the `apt.mark` operation sets or clears those marks. A missing
`apt-mark` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`AptKeys` takes an
optional `directories` argument — a list of paths, a single path string or
`None` for the three defaults — and runs two `find` sweeps that read every
`.gpg`/`.kbx` binary keyring and every `.asc` armored keyring with
`gpg --with-colons`, each behind a `KEYRING:` marker line, mirroring the baseline's
`apt.AptKeys`. The parsed keys are flattened into one list of `AptKey`
entries — `{id, validity, length, subkeys, fingerprint, uid_hash, uid}`
dicts, with `length` an int and `subkeys` a list of further `AptKey`
entries; if the same key id appears
in several keyring files, the last one wins and entries keep the order
their ids first appeared in. The baseline keys its answer by key id (with
subkeys re-keyed by their own ids); reemote returns the typed entry list
instead. The baseline builds the fact on its
`gpg.GpgKeyrings` infrastructure; reemote collapses that stack into the one
class, so the per-keyring paths and formats the parent reports are
deliberately dropped (`AptKeys` discards them anyway). reemote's own
[`GpgKeyrings`][reemote.modules.gpg.gpg.GpgKeyrings] in the gpg namespace
is the general-purpose keyring inspection fact that keeps those
per-keyring paths and formats. The baseline's
`requires_command` gating on `gpg` is deliberately dropped too: a missing
`gpg` is swallowed by the command's `|| true` and reads as `[]`, while a
missing `find` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`AptPackages` takes no arguments and runs `apt list --installed 2>/dev/null`
(the redirect suppresses the "Listing..." progress notice modern apt writes
to stderr), mirroring the baseline's `apt.AptPackages`. Each
`name/source-info version arch [installed,...]` row is matched with
the baseline's package regex, so architecture-suffixed names like `libc6:i386`
keep their suffix, epoch-prefixed versions stay intact, and the
`Listing...` header and any other noise lines are ignored. The result is a
sorted list of `AptPackageInfo` entries — each a name plus its sorted,
de-duplicated version list, the shared shape `AptAutoremovable` and
`AptHeldPackages` return too; the baseline keys its answer by name (the
list-of-versions shape it shares with `deb.DebPackages`), while reemote
returns the typed entry list (the same deliberate divergence
`ApkInstalledPackages` made) so reads stay deterministic and JSON-printable.
the baseline's `requires_command` gating on `apt` is deliberately dropped: a
missing `apt` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError]. Hold state
is not visible in these rows: `AptHeldPackages` reports the names marked
with `apt-mark hold`. Candidate versions and repository origins are not
visible either: `AptPolicy` reports which version `apt install` would
pick and which repositories offer it. Which packages an upgrade would
move is not visible either: `AptUpgradable` reports every pending
version jump, and `AptAutoremovable` the no-longer-needed packages an
autoremove would clean up.

`AptPolicy` takes a package name (shell-quoted) and runs
`LC_ALL=C apt-cache policy <package>` — the `LC_ALL=C` prefix keeps apt's
field labels untranslated on localized hosts, overriding any `LC_*`
variable the login environment sets (a bare `LANG=C` would lose to an
exported `LC_ALL`). It is a reemote original: the baseline ships no `apt.AptPolicy`
counterpart. The result is an `AptPackagePolicy` dict — `{installed,
candidate, versions}` — where `installed` is the installed version and
`candidate` the version `apt install` would pick right now (either maps
`(none)` to `None`), and `versions` is the version table as
`AptPolicyVersion` dicts — `{version, priority, origins}` — in apt's
printed order, each origin line stripped of its leading priority (a
repository line like
`http://archive.ubuntu.com/ubuntu noble/main amd64 Packages` or the local
`/var/lib/dpkg/status` for the installed copy). The `***` marker on the
installed row is ignored (`installed` already conveys it). The fact
fills a coverage gap of `AptPackages`: the `apt list --installed` rows
that fact parses report only installed versions, never which version an
install would pick or which repository offers it, so a deploy validating
that a specific version is available before rolling it out reads the
candidate and origins here. An unknown package prints nothing on stdout
(apt's "Unable to locate package" notice goes to stderr) — that reads as
`None`. A missing `apt-cache` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`AptSources` takes no arguments and cats `/etc/apt/sources.list`, every
`/etc/apt/sources.list.d/*.list` (legacy one-line format) and every
`/etc/apt/sources.list.d/*.sources` (modern deb822 format), each behind a
`#reemote-aptsource` marker line naming the file so the parser can pick
the format by extension, mirroring the baseline's `apt.AptSources` (whose
`##UPSTREAM_FILE` marker is renamed here, like `DnfRepositories`' filename
marker). Legacy lines parse with the baseline's regex — bracketed `[key=value]`
options and flat `/`-suite repositories with no components included;
deb822 stanzas split on blank lines, continuation lines fold into their
field (a bare `.` encodes an empty line, an inline armored `Signed-By`
PGP block stays whole), stanzas missing `Types`/`URIs`/`Suites` or
disabled with `Enabled: no` are skipped, and multi-valued
`Types`/`URIs`/`Suites` expand into one entry per combination in the baseline's
type-then-uri-then-suite order. The result is a list of `AptSource`
dicts — `{type, url, distribution, components, options}` — in file order;
no sources files at all read as `[]`.

Two deliberate usability deviations from the baseline, which optimizes for
backward compatibility: entries are plain dicts typed by the `AptSource`
TypedDict instead of its `AptRepo` frozen-dataclass/dict hybrid, so there
is exactly one access style (`source["url"]`), equality against a literal
dict works and `json.dumps` serializes entries directly; and every
`options` value is a list of strings — the baseline collapses single values to
a bare string, forcing callers to type-check, while here
`options["arch"]` is always e.g. `["amd64"]`. The baseline's `requires_command`
gating on `apt` is deliberately dropped too: the command is pure shell
and `cat` and never runs `apt`, so a host without apt simply reports its
files (usually `[]`).

`AptSourcesFile` takes a file path and reports the raw text of that one
apt sources file, or `None` when it is absent — the `DnfRepoFile` pattern
in this namespace, generic over a `.list` or a `.sources` file because the
fact never interprets the text. The real-transport command cats the file
only behind a `test -e` guard and leads with a `#reemote-sourcesfile`
marker line, so a missing file (no marker) reads as `None` while an
existing but empty file reads as `""` — the distinction that lets a removal
delete an empty leftover. Where `AptSources` parses *every* configured
repository into `AptSource` dicts, this fact answers the single-file content
question twice over: `apt.sources_file(present=False)` reads it to make its
removal idempotent, and `apt.repo` reads it for the text it transforms and
spools back — both without importing another namespace's facts.

`AptUpdateStamp` takes no arguments and runs
`stat -c %Y /var/lib/apt/periodic/update-success-stamp 2>/dev/null || true`
-- the stamp `apt-get update` itself touches on success (the operation
never yields a separate `touch`), and the freshness gate
`apt.packages(update=True, cache_valid_time=N)` and
`apt.update(cache_valid_time=N)` share. `stat -c %Y` prints the file's
mtime as an epoch-seconds int (e.g. `1731523200`), which the fact parses
into a *naive UTC* `datetime` -- the `files.File` convention, kept here
so the answer compares cleanly against
`datetime.now(UTC).replace(tzinfo=None)` on the control side without a
tzinfo mismatch. A missing stamp (a machine that has never run
`apt-get update`, or one where `apt-daily` has not fired yet) reads as
`None`: the `2>/dev/null || true` chain swallows `stat`'s non-zero exit
and empty output, so the caller sees an absent stamp rather than a
`CommandError`. Any other non-integer first line -- noise from a broken
shell -- is likewise treated as absent rather than raising, matching the
tolerant read the freshness gate needs (a corrupt stamp should force an
update, not abort the deploy). It is a reemote original: the baseline's
`apt.update`/`apt.packages` reach for a generic `files.File`/
`server.Date` pair to read the same stamp, which reemote's apt namespace
cannot do -- namespaces never reuse each other's facts -- so the read
lives here as a dedicated single-path fact. This is the fact that
reverses the earlier "the baseline's `cache_time` is deliberately
dropped" decision: with `AptUpdateStamp` in-namespace, the Ansible-shaped
`cache_valid_time` parameter is expressible without breaking the
zero-cross-module-import invariant. A missing `stat` exits 127 --
reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`AptUpgradable` takes no arguments and runs
`apt list --upgradable 2>/dev/null` (the redirect suppresses the
"Listing..." progress notice modern apt writes to stderr, like
`AptPackages`' command). It is a reemote original: the baseline ships no
`apt.AptUpgradable` counterpart. Each
`name/source-info version arch [upgradable from: old-version]` row is
matched with a package regex modeled on `AptPackages`', so
architecture-suffixed names like `libc6:i386` keep their suffix,
epoch-prefixed versions stay intact, and the `Listing...` header and any
other noise lines are ignored. The result is a sorted list of
`AptPackageUpgrade` entries — `{name, installed, candidate}`, the same
`installed`/`candidate` keys
`AptPackagePolicy` uses — naming both ends of the version jump
`apt upgrade` would perform; the row's source-info (offering suite) and
architecture columns are deliberately dropped, like `AptPackages` drops
them (`AptPolicy` reports the offering repositories when they matter),
and a repeated name keeps its last row. The fact fills a coverage gap of
`AptPackages` and `AptPolicy`: the installed rows report only what is on
the machine and a policy query answers one package at a time, while this
listing names every package an upgrade would move and both versions of
each jump — the change-management and auditing view to read before
executing `apt upgrade`. No pending upgrades reads as `[]`. A missing
`apt` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`DebPackage` takes a `.deb` file path or an installed package name and runs
the baseline's `test -f ... && case ...` form: when the path exists and ends in
`.deb` it inspects the archive (`dpkg -I`), otherwise it queries the installed
package database (`dpkg -s`), with a trailing `|| true` so an unknown name
produces no output rather than failing the gather. The `Package:`/`Version:`
control fields parse into a `DebPackageInfo` dict — `{"name", "version"}`
(each line stripped first, so
`dpkg -I`'s tab-indented fields parse); a name that is neither an existing
`.deb` file nor installed reads as `None`. The baseline returns an empty dict in
that case; reemote reports `None`, mirroring `RpmPackage`'s None-on-absent
convention so `apt.deb` can test `info is not None`. The fact is the read side
of `apt.deb`: the operation reads the file's metadata here, then compares the
file's version against the installed versions `AptPackages` reports for that
name. The baseline's `requires_command` gating on `dpkg` is deliberately dropped: a
missing `dpkg` is swallowed by the command's `|| true` and reads as `None`.

`SimulateOperationWillChange` takes an `apt-get` subcommand phrase (e.g.
`"dist-upgrade"`, interpolated verbatim — baseline parity) and runs it with
`--dry-run` behind `LC_ALL=C` and the non-interactive
`DEBIAN_FRONTEND=noninteractive apt-get -y` prefix, mirroring the baseline's
`apt.SimulateOperationWillChange`. The simulation never changes anything
and needs no root; the
`N upgraded, N newly installed, N to remove and N not upgraded.` summary
line parses into an `AptSimulationDict` — `{upgraded, newly_installed,
removed, not_upgraded}` — while the banner, progress lines, indented
package lists and `Inst`/`Conf`/`Remv` detail rows are skipped.
`apt.dist_upgrade` reads it to decide whether the real `dist-upgrade`
would do anything, counting newly-installed and removed packages too —
which `AptUpgradable` (a plain `upgrade` listing) cannot see. Deliberate
deviation from the baseline: when no summary line appears, upstream raises an
exception (which its caller swallows into "run the real command anyway");
reemote reports `None` instead, so the caller branches on a value rather
than on exception control flow. A non-zero exit of the simulation itself
still raises `CommandError` — reemote's fail-fast convention. The baseline's
`requires_command` gating on `apt-get` is deliberately dropped: a missing
`apt-get` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].


## Operations

Operations live in namespace modules so a deploy reads `apt.deb`, mirroring
the baseline's API. Every operation is an async generator with the same fixed
signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where it
decides a change is needed, and yields one descriptor per command that would
mutate the host.

`apt.deb` adds or removes a `.deb` file package. `src` names a `.deb` that
already exists on the *target host* — upload one from the controller with
`files.put` first. The `DebPackage` fact reads the file's `name`/`version` and
the package counts as present only when `AptPackages` lists that exact version
for the file's name, so `present=True` is a no-op when it is already installed
at that version and otherwise installs with the baseline's three-pass sequence: a
tolerant `dpkg -i` (ignoring unmet dependencies), an `apt-get install -f` to
resolve them, then a final `dpkg -i` that configures the package and fails fast
(`CommandError`) if dependencies are still missing. `present=False` removes the
file's package by name with `apt-get remove` only when that exact version is
installed; because removal reads the name from the file, a `present=False`
whose `.deb` is absent cannot resolve a name and is a no-op. `wait_for_lock`
(`True` → 60s, an int → that many seconds) makes apt wait for its lock instead
of failing instantly. Installing or removing needs root, so flag the request
`sudo=True` (or `su=True`). Two the baseline options are deliberately dropped: a URL
`src` (`apt.deb` installs a binary `.deb` that must already be on the host, and
reemote's URL download `files.download` is text-only, so it raises `ValueError`,
mirroring `dnf.rpm`) and `force` (`--force-yes` is deprecated in modern apt).

`apt.dist_upgrade` updates all apt packages, employing `dist-upgrade`
(the baseline's `apt.dist_upgrade` parity). The `SimulateOperationWillChange`
fact simulates the `dist-upgrade` first: when every proposed count is zero
the operation is a no-op (the baseline's "skipped, no changes would be
performed"), otherwise it yields the `dist-upgrade` — which, unlike a
plain `upgrade`, may also install new dependencies and remove conflicting
packages, all of which the simulation counts. A failed simulation (`None`)
runs the real command anyway, the baseline's fallback. `wait_for_lock` (`True`
→ 60s, an int → that many seconds) makes apt wait for its lock instead of
failing instantly. Upgrading needs root, so flag the request `sudo=True`
(or `su=True`). The baseline's `auto_remove` parameter (appending
`--autoremove` to the same command) is deliberately split out into the
standalone `apt.auto_remove` operation, so orphaned dependencies can be
cleaned up without also performing an upgrade; `apt.dist_upgrade` never
passes `--autoremove`.

`apt.auto_remove` removes transitive dependencies that are no longer
needed — a reemote original: the baseline only offers autoremove as the
`auto_remove` parameter of `apt.dist_upgrade`/`apt.upgrade`, never as a
standalone operation. The `AptAutoremovable` fact simulates the autoremove
and names exactly the no-longer-needed packages: a non-empty list yields
the `apt-get autoremove` (the reason names the packages, already sorted so
both phases report identically), an empty list is a no-op. `wait_for_lock`
behaves as above; removing packages needs root, so flag the request
`sudo=True` (or `su=True`). Yielding `apt.dist_upgrade` then
`apt.auto_remove` in one deploy composes the upgrade-then-cleanup flow
the baseline expresses with a single `auto_remove=True` flag — each half an
explicit, separately plannable step.

`apt.key` installs a GPG keyring file for APT — the install half of
the baseline's `apt.key`, the modern replacement for the deprecated `apt-key
add`: each vendor key lives in its own file under `/etc/apt/keyrings/`,
referenced per repository via `signed-by=`. `src` is the key content as a
seekable IO-like stream on the control machine (never a filename or URL —
a `str` raises `ValueError`; fetch a URL on the controller and pass its
bytes), and `dest` is the keyring filename or absolute path: a relative
name resolves under `/etc/apt/keyrings/`, an explicit path is used
verbatim. The operation is **non-idempotent** — a deliberate deviation
from the baseline, which skips installation when the destination file exists:
it always reports a change and yields two descriptors, an
`AptKeyUploadCommand` transfer that spools the stream into a dest-derived
`/tmp` directory over scp, then an `AptKeyInstallCommand` running one elevated
`sh -c` that creates the destination directory, dearmors the key with
`gpg --batch --yes --dearmor` when the content is ASCII-armored (`--yes`
overwrites a keyring a previous run left behind, which `--batch` alone
refuses; binary content is copied verbatim), chmods the keyring `0644`
and removes the spool directory.
Flag the request `sudo=True` (or `su=True`) for the install; the transfer
stays unprivileged regardless, so the log shows a plain `scp` followed by
`sudo sh -c`. The stream is compared by identity across the prepare and
apply phases, so a deploy must define it once (e.g. at module level).
the baseline's `keyserver`/`keyid` fetch and its `present=False` removal are
deliberately dropped: keys arrive as streams from the controller, and
removal is expressible with the `files` primitives. `dest` is required
because an anonymous stream provides no name to derive one from.

`apt.mark` holds packages back from upgrades, or releases held ones — a
reemote original: the baseline ships no operation managing package hold
states. `packages` accepts a single name or a list/tuple, and `held`
selects the direction: `held=True` runs `apt-mark hold`, keeping the
packages out of `apt upgrade`/`apt.dist_upgrade` until released;
`held=False` runs `apt-mark unhold`. The operation is **idempotent** on
`AptHeldPackages`: only the requested names not already held are marked
(and only the names currently held are released), a request whose names
are all in the wanted state is a no-op, and the command keeps the
requested order so both phases report identically. Names compare
verbatim against what `apt-mark showhold` prints, so an
architecture-suffixed package must be requested in its suffixed form
(`libc6:i386`). Marking writes to the root-owned dpkg database, so flag
the request `sudo=True` (or `su=True`); a missing `apt-mark` exits 127 —
reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].
`apt-mark`'s `auto`/`manual` installation-reason marks are deliberately
out of scope (only the hold state `AptHeldPackages` reports is managed),
and there is no `wait_for_lock` option — `apt-mark` is not `apt-get` and
takes no `DPkg::Lock::Timeout` wrapper.

`apt.packages` installs, upgrades or removes packages by name (the baseline's
`apt.packages` parity). `packages` accepts a single name, a list/tuple or
`None`; a name can be pinned as `<pkg>=<version>`, and the `AptPackages`
fact supplies the installed versions, so a pin counts as present only
when that *exact* version is installed (the baseline's `ensure_packages`
version match). `present=True` installs only what is missing;
`latest=True` additionally upgrades every installed, *unpinned* name —
the baseline's legacy behavior, which upgrades blindly without checking
whether a newer version exists (the upgrade reuses the install command
without `extra_install_args`, as the baseline builds it). `present=False`
removes only what is installed, with `purge=True` switching `remove` to
`purge` so configuration files go too. `update=True` runs `apt-get
update` first and is **stateless** — it always yields, like the baseline's
non-idempotent `apt.update`; `upgrade=True` runs `apt-get upgrade`
first, gated on `SimulateOperationWillChange` exactly like
`apt.dist_upgrade` (all-zero proposed counts are a no-op, a failed
simulation runs anyway). `no_recommends` and `allow_downgrades` add the
matching `apt-get` flags to installs; `extra_install_args` /
`extra_uninstall_args` append raw arguments to the install / remove
command; `wait_for_lock` behaves as above. `default_release` adds apt's
`-t <release>` (`--target-release`) to the install so unpinned names
resolve their candidate version from that release — a first-class
alternative to smuggling `-t` through `extra_install_args`, and, unlike
that raw string (the `latest` upgrade drops it), it is carried on the
upgrade command too, so release targeting stays consistent and a re-run
converges. It scopes the command only: the `AptPackages` presence
comparison has no release dimension and stays name/version based (the
`dnf.packages` `repos` command-scoping precedent). Installing, upgrading and
removing need root, so flag the request `sudo=True` (or `su=True`).
`cache_valid_time` (Ansible's `apt` module option of the same name)
gates the `update=True` prefix on the `AptUpdateStamp` fact: when it is
`> 0` and the stamp is at most that many seconds old, the update is
skipped with reason `apt update skipped, cache is fresh` and the
stateless prefix is not yielded — reversing the earlier “the baseline's
`cache_time` is deliberately dropped” decision, since the in-namespace
`AptUpdateStamp` fact answers the freshness question without a
cross-module import. The age is computed against the *controller's* UTC
clock (Ansible's shape — a fact read of the target's clock would double
the gather for a gate that is only ever approximate); a missing or
unreadable stamp counts as stale, so the update still runs;
`cache_valid_time=0` (the default) keeps the always-update behaviour;
and the gate applies only to the `update=True` prefix — installs and
upgrades are unaffected. The baseline's `cache_time` keyword is still
absorbed by `**kwargs` with no effect — the first-class spelling is
`cache_valid_time`, matching Ansible. One further baseline option is
deliberately dropped: `force` (`--force-yes` is deprecated in modern apt,
the same decision as `apt.deb`). The installed-versions fact is
`AptPackages` (`apt list --installed`) rather than the baseline's
`DebPackages` (`dpkg -l`) — the same substitution `apt.deb` made.

`apt.ppa` adds or removes an Ubuntu PPA repository (the baseline's `apt.ppa`
parity). `src` is the full `ppa:user/repo` name and `present` selects the
direction: `present=True` runs `apt-add-repository -y <src>`,
`present=False` runs `apt-add-repository -y --remove <src>`. The operation
is **idempotent** on `AptSources`: it reduces `src` to its `user/repo`
Launchpad path and matches that against every configured source's URL (the
`ppa.launchpad.net` host or the modern `ppa.launchpadcontent.net`, either
scheme), so a PPA already configured in *any* sources file is a no-op for
`present=True` and an unconfigured one is a no-op for `present=False`. That
is a deliberate deviation from the baseline, which reads no fact and always
re-runs `apt-add-repository`; reporting the real delta is what unblocks the
`register` + `when: ... is changed` pattern an Ansible `apt_repository`
migration relies on. A `src` that is not a resolvable `ppa:user/repo` name
cannot be proven present or absent, so it falls back to always reporting a
change rather than silently skipping a legitimate add or remove. Adding or
removing a repository writes to the root-owned apt sources, so flag the request
`sudo=True` (or `su=True`). It requires
`apt-add-repository` (from `software-properties-common`) on the target host;
the baseline's `requires_command` gating is deliberately dropped, so a missing
binary exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError]. Unlike
`apt.repo` and `apt.sources_file`, this operation deliberately stays outside
the spool-and-install family: its payload is a CLI invocation, not the
file's text — `apt-add-repository` owns the sources file and also fetches
the PPA's Launchpad signing key, adds the deb-src entry and resolves the
distro codename, none of which a hand-rolled content transform can
reproduce. To manage the PPA's source *line* through the spool family
instead, `apt.repo` (plus `apt.key` for the signing key) is that shape.

`apt.repo` adds or removes one apt repository line (the baseline's `apt.repo`
parity). `src` is the full source string (`deb http://X hardy main`),
`present` selects the direction, and `filename` scopes the edit to
`/etc/apt/sources.list.d/<filename>.list` instead of the main
`/etc/apt/sources.list`. Presence is decided by parsing `src` with
`AptSources`' legacy line parser and comparing against the fact's entries
— the baseline's `parse_apt_repo(src) in AptSources` check — so a repository
already configured in *any* sources file (legacy or deb822) is a no-op,
and an unparseable `src` counts as absent but is still added when
`present=True` (the baseline's tolerance). Either direction then *rewrites* the
managed file rather than patching it in place: `AptSourcesFile` reads the
file's current text, one pure transform adds the line exactly once or
deletes every line the anchored, escaped pattern matches, and when the
result differs from what is there the request yields two commands at
different privilege levels, the `apt.key` pattern:
`AptRepoUploadCommand` spools the new text to a `/tmp` path named after the
managed file over scp as the login user, then `AptRepoInstallCommand` runs
one elevated `sh -c` that creates the directory, copies the file, chmods it
`0644` (the baseline's fixed mode) and removes the spool — so flag the request
`sudo=True` (or `su=True`); the transfer stays unprivileged regardless.
The spool name is derived from the destination rather than a fixed
constant, because `apt.repo` edits several distinct files and two
repository edits in one deploy must not clobber each other's spool. Two
content-level no-ops round out the baseline's fact-level ones: an add whose line
the file already carries, and a remove of a repository configured in some
*other* file — the transforms are fixpoints, so a repeat converges instead
of writing a second copy. Deliberate deviations from the baseline, which
delegates the edit to `files.line` and so mutates the protected file in
place with `echo >>` and `sed -i`: reemote has no line operation
(`files.edit` is its generic editing primitive) and namespaces never reuse
each other's operations, so the write rides self-contained apt descriptors
of the spool-and-install family. That also drops the baseline's timestamped sed
backup (its timestamp would differ between the prepare and apply phases,
breaking the determinism guard) and keeps the file's other lines, comments
and blank lines exactly as they were, because the transform is line-based
over the real content.

`apt.sources_file` manages a deb822 `.sources` file under
`/etc/apt/sources.list.d/` (the baseline's `apt.sources_file` parity).
`filename` is the base name (the `.sources` extension is appended), and
`types`, `uris`, `suites`, `components` and `architectures` each accept a
list, a tuple or a space-separated string; `signed_by` names the keyring
file for signature verification (the natural pairing with `apt.key`).
`present=True` requires at least one URI and one suite — otherwise
`ValueError` is raised with the baseline's message (reemote's single
validation error type) — and is idempotent on `AptSources`: the fields
expand into one desired repository per type/URI/suite combination (in the
fact's list-valued `AptSource` shape), and when every combination is already
configured the operation is a no-op. Otherwise one request yields two
commands at different privilege levels, the `apt.key` pattern:
`AptSourcesFileUploadCommand` spools the generated deb822 text into a
dest-derived `/tmp` directory over scp as the login user, then `AptSourcesFileInstallCommand`
runs one elevated `sh -c` that creates the directory, copies the file,
chmods it `0644` (the baseline's fixed mode) and removes the spool — so flag the
request `sudo=True` (or `su=True`); the transfer stays unprivileged
regardless. This spool-and-install pair is the deliberate replacement for
the baseline's `files.put` delegation: reemote's transfers are unprivileged
scp and namespaces never reuse each other's operations.
`present=False` is idempotent on `AptSourcesFile`: it yields `rm -f`
only when the file exists (an empty leftover included) and is a no-op
when it is already absent. The baseline checks its `files.File` fact for the
same gate; reemote's apt namespace never imports another module's
facts, so the in-namespace `AptSourcesFile` content fact provides the
existence answer — the same pattern `dnf.repo` uses with `DnfRepoFile`.

`apt.update` refreshes the package lists (the baseline's `apt.update` parity)
— the standalone form of `apt.packages`' `update=True` prefix. By
default it runs the bare `apt-get update` (no non-interactive wrapper,
since `update` asks no questions) and is **non-idempotent**: it reads no
fact and always reports a change. `cache_valid_time` (Ansible's `apt`
module option of the same name) gates that yield on the `AptUpdateStamp`
fact — the mtime of `/var/lib/apt/periodic/update-success-stamp`, which
`apt-get update` itself touches on success: when it is `> 0` and the
stamp is at most that many seconds old (compared against the
*controller's* UTC clock, Ansible's shape), the operation returns with
reason `apt update skipped, cache is fresh` and no change is reported,
making the operation idempotent within that window. A missing or
unreadable stamp counts as stale, so the update still runs;
`cache_valid_time=0` (the default) keeps the always-update behaviour.
This is the reversal of the earlier “the baseline's `cache_time` is
deliberately dropped” decision — the in-namespace `AptUpdateStamp` fact
answers the freshness question without breaking the namespaces'
zero-cross-module-import invariant (the `File`/`Date` fact reads the
baseline would need live in other namespaces). The baseline's
`cache_time` keyword is still absorbed by `**kwargs` with no effect —
the first-class spelling is `cache_valid_time`, matching Ansible.
`wait_for_lock` (`True` → 60s, an int → that
many seconds) makes apt wait for its lock instead of failing instantly.
Refreshing the lists needs root, so flag the request `sudo=True` (or
`su=True`).

`apt.upgrade` upgrades all apt packages (the baseline's `apt.upgrade` parity)
— the standalone form of `apt.packages`' `upgrade=True` prefix. The
`SimulateOperationWillChange` fact simulates the `upgrade` first: when
every proposed count is zero the operation is a no-op (the baseline's
"skipped, no changes would be performed"), otherwise it yields the
`upgrade` — which, unlike `dist-upgrade`, never installs new dependencies
or removes conflicting packages. A failed simulation (`None`) runs the
real command anyway, the baseline's fallback. `wait_for_lock` behaves as
above; upgrading needs root, so flag the request `sudo=True` (or
`su=True`). The baseline's `auto_remove` parameter (appending `--autoremove`
to the same command) is deliberately dropped in favour of the standalone
`apt.auto_remove` operation — the same split `apt.dist_upgrade` made —
so `apt.upgrade` never passes `--autoremove` and the keyword is absorbed
with no effect. Yielding `apt.update` then `apt.upgrade` in one deploy
composes the classic refresh-then-patch flow, each half an explicit,
separately plannable step.

## Example

The read-only fact-gathering scripts `apt_apt_autoremovable.py`,
`apt_apt_held_packages.py`, `apt_apt_keys.py`, `apt_apt_packages.py`,
`apt_apt_policy.py`, `apt_apt_sources.py`, `apt_apt_update_stamp.py`,
`apt_apt_upgradable.py` and
`apt_simulate_operation_will_change.py`
under
[`examples/modules/apt/`](../examples/modules/apt.md) gather
`AptAutoremovable`, `AptHeldPackages`, `AptKeys`, `AptPackages`,
`AptPolicy`, `AptSources`, `AptUpdateStamp`, `AptUpgradable` and
`SimulateOperationWillChange`
on an inventory of Ubuntu hosts with
[`gather_fact`][reemote.drivers.gather_fact] and print each host's result
as JSON.

The `apt_deb.py` script beside them demonstrates the `apt.deb` operation: it
installs a `.deb` already on the host with version idempotence, yielding the
same request twice so the second is a guaranteed no-op in the apply phase.
The `apt_dist_upgrade.py` and `apt_auto_remove.py` scripts demonstrate the
upgrade-then-cleanup pair the same way — each yields its request twice to
show simulation-gated idempotence. The `apt_key.py` script demonstrates
`apt.key`: it installs a module-level key stream as
`/etc/apt/keyrings/reemote-demo.gpg`, unprivileged spool transfer plus
elevated install in one request. The `apt_mark.py` script demonstrates
`apt.mark`: it holds the baseline packages back from upgrades with
`apt-mark hold`, yielding the same request twice so the second is a
guaranteed no-op in the apply phase. The `apt_packages.py` script demonstrates
`apt.packages`: it installs packages by name with exact-version
idempotence, yielding the same request twice so the second is a guaranteed
no-op in the apply phase. The `apt_ppa.py` script demonstrates `apt.ppa`:
it adds the Bitcoin PPA with `apt-add-repository` under `sudo`, yielding the
same request twice so the second is a guaranteed no-op in the apply phase
(the operation is idempotent on `AptSources`), then removes it again so the
example leaves the host's sources as it found them. The `apt_repo.py` script demonstrates `apt.repo`:
it rewrites the Canonical partner repository line into its own
`sources.list.d` file — unprivileged spool transfer plus elevated install
in one request — yielding the same request twice so the second is a
guaranteed no-op in the apply phase, then removing it again so the example
leaves the host's sources as it found them. The `apt_sources_file.py` script
demonstrates `apt.sources_file`: it writes the Docker repository as a
modern deb822 `docker.sources` file — unprivileged spool transfer plus
elevated install in one request — yielding the same request twice so the
second is a guaranteed no-op in the apply phase. The `apt_update.py`
script demonstrates `apt.update`: it refreshes the package lists under
`sudo` — a single request, since the operation is non-idempotent by
default and has no state for a second request to converge on (a
`cache_valid_time=N` gate would make it idempotent within a window, but
the example keeps the always-update shape). The `apt_upgrade.py` script
demonstrates `apt.upgrade`: it upgrades all packages gated by the dry-run
simulation, yielding the same request twice to show simulation-gated
idempotence. All twelve are rendered on the
[apt examples page](../examples/modules/apt.md).

--->
