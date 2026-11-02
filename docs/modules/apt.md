# apt module

`reemote5.modules.apt.apt` holds the apt facts — `AptAutoremovable`, the
no-longer-needed packages an autoremove would clean up, parsed from the
`Remv` rows of an `apt-get --just-print autoremove` simulation,
`AptHeldPackages`, the
package names marked as held via `apt-mark hold`, parsed from
`apt-mark showhold`, `AptKeys`, the GPG keys available to APT, gathered
from the modern keyring directories
(`/etc/apt/trusted.gpg.d`, `/etc/apt/keyrings`, `/usr/share/keyrings`) with
`gpg --with-colons` instead of the deprecated `apt-key` command,
`AptPackages`, the installed packages with their versions, parsed from
`apt list --installed`, `AptPolicy`, the install candidate and repository
origins of one package, parsed from `apt-cache policy` into a plain
`AptPackagePolicy` TypedDict-shaped dict, `AptSources`, the configured
repositories, parsed from the legacy `.list` files and the modern deb822
`.sources` files into plain `AptSource` TypedDict-shaped dicts,
`AptSourcesFile`, the raw content of one `.sources` file (None when it is
absent), read behind a `test -e`-guarded marker, and
`AptUpgradable`, the packages `apt upgrade` would move with both ends of
each version jump, parsed from `apt list --upgradable` into plain
`AptPackageUpgrade` TypedDict-shaped dicts, `DebPackage`, the name and
version of a `.deb` file or an installed dpkg package, parsed from
`dpkg -I`/`dpkg -s`, and `SimulateOperationWillChange`, the proposed change
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
`apt-mark hold`/`unhold`, idempotent on `AptHeldPackages` — a reemote5
original; `apt.packages`, which
installs, upgrades or removes packages by name, reading `AptPackages`
for exact-version idempotence and `SimulateOperationWillChange` to gate
its optional `upgrade`; `apt.ppa`, which adds or removes an Ubuntu
PPA with `apt-add-repository`, deliberately non-idempotent like pyinfra's;
`apt.repo`, which adds or removes one apt source line in
`/etc/apt/sources.list` or a `sources.list.d` file, idempotent on
`AptSources`; and `apt.sources_file`, which manages a modern deb822
`.sources` file under `sources.list.d`, idempotent on `AptSources` for
the write direction and on `AptSourcesFile` for the removal;
`apt.update`, the stateless package-list refresh,
deliberately non-idempotent like pyinfra's; and `apt.upgrade`, which
upgrades every upgradable package when `SimulateOperationWillChange`
proposes changes.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `AptAutoremovable`
takes no arguments and runs `apt-get --just-print autoremove 2>/dev/null`
— a simulation that never removes anything (the `2>/dev/null` suppresses
the lock/permission warnings an unprivileged simulation writes to
stderr). It is a reemote5 original: pyinfra ships no
`apt.AptAutoremovable` counterpart (its `apt.SimulateOperationWillChange`
only counts a simulation's changes, never names them). Only the
machine-readable `Remv <name> [<version>]` rows are read — the progress
lines, the indented names under `The following packages will be REMOVED:`
and the `0 upgraded, ...` count line are never mistaken for package
names, and the bracketed versions are deliberately dropped. `apt-mark
showauto` is deliberately not used: it lists every auto-installed
package, including ones something still requires, while the simulation
reports exactly the no-longer-needed set. The result is a deterministic,
JSON-printable sorted name list like `["libllvm17t64",
"linux-headers-6.8.0-45"]`, with architecture-suffixed names keeping
their suffix; nothing autoremovable reads as `[]`. The fact fills a
coverage gap of `AptPackages`: the `apt list --installed` rows cannot
tell a legacy dependency from one still in use, so a deploy cleaning up
after itself reads the removable names here — and only collects the
list; removal stays an explicit operator decision, never a fact side
effect — the `apt.auto_remove` operation performs that cleanup. The
simulation needs no root, and a missing `apt-get` exits
127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`AptHeldPackages`
takes no arguments and runs `apt-mark showhold`, which prints one held
package name per line — the names marked with `apt-mark hold`, which keeps
them out of upgrades. It is a reemote5 original: pyinfra ships no
`apt.AptHeldPackages` counterpart. Blank lines are skipped and the names
are de-duplicated and sorted, so the result is a deterministic,
JSON-printable list like `["docker-ce", "libc6:i386", "linux-image-generic"]`,
with architecture-suffixed names keeping their suffix exactly as `apt-mark`
prints them; nothing held reads as `[]`. The fact fills a coverage gap of
`AptPackages`: the `apt list --installed` rows that fact parses report
versions but never the hold state, so a deploy that must not upgrade
critical packages reads the held names here — and only collects the
list; marking stays an explicit operator decision, never a fact side
effect — the `apt.mark` operation sets or clears those marks. A missing
`apt-mark` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`AptKeys` takes an
optional `directories` argument — a list of paths, a single path string or
`None` for the three defaults — and runs two `find` sweeps that read every
`.gpg`/`.kbx` binary keyring and every `.asc` armored keyring with
`gpg --with-colons`, each behind a `KEYRING:` marker line, mirroring pyinfra's
`apt.AptKeys`. The parsed keys are flattened into one dict of key id →
`{validity, length, subkeys, fingerprint, uid_hash, uid}` dict, with `length`
an int and `subkeys` keyed by the subkeys' own ids; if the same key id appears
in several keyring files, the last one wins. pyinfra builds the fact on its
`gpg.GpgKeyrings` infrastructure; reemote5 collapses that stack into the one
class, so the per-keyring paths and formats the parent reports are
deliberately dropped (`AptKeys` discards them anyway). pyinfra's
`requires_command` gating on `gpg` is deliberately dropped too: a missing
`gpg` is swallowed by the command's `|| true` and reads as `{}`, while a
missing `find` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`AptPackages` takes no arguments and runs `apt list --installed 2>/dev/null`
(the redirect suppresses the "Listing..." progress notice modern apt writes
to stderr), mirroring pyinfra's `apt.AptPackages`. Each
`name/source-info version arch [installed,...]` row is matched with
pyinfra's package regex, so architecture-suffixed names like `libc6:i386`
keep their suffix, epoch-prefixed versions stay intact, and the
`Listing...` header and any other noise lines are ignored. The result is a
dict of package name → sorted, de-duplicated version list — the
list-of-versions shape pyinfra documents (and shares with `deb.DebPackages`);
pyinfra's `parse_packages` collects versions into a set per name, reemote5
returns the sorted list so reads stay deterministic and JSON-printable.
pyinfra's `requires_command` gating on `apt` is deliberately dropped: a
missing `apt` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError]. Hold state
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
exported `LC_ALL`). It is a reemote5 original: pyinfra ships no `apt.AptPolicy`
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
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`AptSources` takes no arguments and cats `/etc/apt/sources.list`, every
`/etc/apt/sources.list.d/*.list` (legacy one-line format) and every
`/etc/apt/sources.list.d/*.sources` (modern deb822 format), each behind a
`#reemote5-aptsource` marker line naming the file so the parser can pick
the format by extension, mirroring pyinfra's `apt.AptSources` (whose
`##PYINFRA_FILE` marker is renamed here, like `DnfRepositories`' filename
marker). Legacy lines parse with pyinfra's regex — bracketed `[key=value]`
options and flat `/`-suite repositories with no components included;
deb822 stanzas split on blank lines, continuation lines fold into their
field (a bare `.` encodes an empty line, an inline armored `Signed-By`
PGP block stays whole), stanzas missing `Types`/`URIs`/`Suites` or
disabled with `Enabled: no` are skipped, and multi-valued
`Types`/`URIs`/`Suites` expand into one entry per combination in pyinfra's
type-then-uri-then-suite order. The result is a list of `AptSource`
dicts — `{type, url, distribution, components, options}` — in file order;
no sources files at all read as `[]`.

Two deliberate usability deviations from pyinfra, which optimizes for
backward compatibility: entries are plain dicts typed by the `AptSource`
TypedDict instead of its `AptRepo` frozen-dataclass/dict hybrid, so there
is exactly one access style (`source["url"]`), equality against a literal
dict works and `json.dumps` serializes entries directly; and every
`options` value is a list of strings — pyinfra collapses single values to
a bare string, forcing callers to type-check, while here
`options["arch"]` is always e.g. `["amd64"]`. pyinfra's `requires_command`
gating on `apt` is deliberately dropped too: the command is pure shell
and `cat` and never runs `apt`, so a host without apt simply reports its
files (usually `[]`).

`AptSourcesFile` takes a file path and reports the raw text of that one
`.sources` file, or `None` when it is absent — the `DnfRepoFile` pattern
in this namespace. The real-transport command cats the file only behind
a `test -e` guard and leads with a `#reemote5-sourcesfile` marker line,
so a missing file (no marker) reads as `None` while an existing but empty
file reads as `""` — the distinction that lets the removal delete an
empty leftover. Where `AptSources` parses *every* configured repository
into `AptSource` dicts, this fact answers the single-file existence and
content question `apt.sources_file(present=False)` reads to make its
removal idempotent without importing another namespace's facts.

`AptUpgradable` takes no arguments and runs
`apt list --upgradable 2>/dev/null` (the redirect suppresses the
"Listing..." progress notice modern apt writes to stderr, like
`AptPackages`' command). It is a reemote5 original: pyinfra ships no
`apt.AptUpgradable` counterpart. Each
`name/source-info version arch [upgradable from: old-version]` row is
matched with a package regex modeled on `AptPackages`', so
architecture-suffixed names like `libc6:i386` keep their suffix,
epoch-prefixed versions stay intact, and the `Listing...` header and any
other noise lines are ignored. The result is a dict of package name →
`AptPackageUpgrade` dict — `{installed, candidate}`, the same keys
`AptPackagePolicy` uses — naming both ends of the version jump
`apt upgrade` would perform; the row's source-info (offering suite) and
architecture columns are deliberately dropped, like `AptPackages` drops
them (`AptPolicy` reports the offering repositories when they matter),
and a repeated name keeps its last row. The fact fills a coverage gap of
`AptPackages` and `AptPolicy`: the installed rows report only what is on
the machine and a policy query answers one package at a time, while this
listing names every package an upgrade would move and both versions of
each jump — the change-management and auditing view to read before
executing `apt upgrade`. No pending upgrades reads as `{}`. A missing
`apt` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`DebPackage` takes a `.deb` file path or an installed package name and runs
pyinfra's `test -f ... && case ...` form: when the path exists and ends in
`.deb` it inspects the archive (`dpkg -I`), otherwise it queries the installed
package database (`dpkg -s`), with a trailing `|| true` so an unknown name
produces no output rather than failing the gather. The `Package:`/`Version:`
control fields parse into `{"name", "version"}` (each line stripped first, so
`dpkg -I`'s tab-indented fields parse); a name that is neither an existing
`.deb` file nor installed reads as `None`. pyinfra returns an empty dict in
that case; reemote5 reports `None`, mirroring `RpmPackage`'s None-on-absent
convention so `apt.deb` can test `info is not None`. The fact is the read side
of `apt.deb`: the operation reads the file's metadata here, then compares the
file's version against the installed versions `AptPackages` reports for that
name. pyinfra's `requires_command` gating on `dpkg` is deliberately dropped: a
missing `dpkg` is swallowed by the command's `|| true` and reads as `None`.

`SimulateOperationWillChange` takes an `apt-get` subcommand phrase (e.g.
`"dist-upgrade"`, interpolated verbatim — pyinfra parity) and runs it with
`--dry-run` behind `LC_ALL=C` and the non-interactive
`DEBIAN_FRONTEND=noninteractive apt-get -y` prefix, mirroring pyinfra's
`apt.SimulateOperationWillChange`. The simulation never changes anything
and needs no root; the
`N upgraded, N newly installed, N to remove and N not upgraded.` summary
line parses into an `AptSimulationDict` — `{upgraded, newly_installed,
removed, not_upgraded}` — while the banner, progress lines, indented
package lists and `Inst`/`Conf`/`Remv` detail rows are skipped.
`apt.dist_upgrade` reads it to decide whether the real `dist-upgrade`
would do anything, counting newly-installed and removed packages too —
which `AptUpgradable` (a plain `upgrade` listing) cannot see. Deliberate
deviation from pyinfra: when no summary line appears, upstream raises an
exception (which its caller swallows into "run the real command anyway");
reemote5 reports `None` instead, so the caller branches on a value rather
than on exception control flow. A non-zero exit of the simulation itself
still raises `CommandError` — reemote5's fail-fast convention. pyinfra's
`requires_command` gating on `apt-get` is deliberately dropped: a missing
`apt-get` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

::: reemote5.modules.apt.apt
    options:
      members:
        - AptAutoremovable
        - AptHeldPackages
        - AptKeys
        - AptPackagePolicy
        - AptPackageUpgrade
        - AptPackages
        - AptPolicy
        - AptPolicyVersion
        - AptSimulationDict
        - AptSource
        - AptSources
        - AptSourcesFile
        - AptUpgradable
        - DebPackage
        - SimulateOperationWillChange

## Operations

Operations live in namespace modules so a deploy reads `apt.deb`, mirroring
pyinfra's API. Every operation is an async generator with the same fixed
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
at that version and otherwise installs with pyinfra's three-pass sequence: a
tolerant `dpkg -i` (ignoring unmet dependencies), an `apt-get install -f` to
resolve them, then a final `dpkg -i` that configures the package and fails fast
(`CommandError`) if dependencies are still missing. `present=False` removes the
file's package by name with `apt-get remove` only when that exact version is
installed; because removal reads the name from the file, a `present=False`
whose `.deb` is absent cannot resolve a name and is a no-op. `wait_for_lock`
(`True` → 60s, an int → that many seconds) makes apt wait for its lock instead
of failing instantly. Installing or removing needs root, so flag the request
`sudo=True` (or `su=True`). Two pyinfra options are deliberately dropped: a URL
`src` (reemote5 has no download operation, so it raises `ValueError`, mirroring
`dnf.rpm`) and `force` (`--force-yes` is deprecated in modern apt).

`apt.dist_upgrade` updates all apt packages, employing `dist-upgrade`
(pyinfra's `apt.dist_upgrade` parity). The `SimulateOperationWillChange`
fact simulates the `dist-upgrade` first: when every proposed count is zero
the operation is a no-op (pyinfra's "skipped, no changes would be
performed"), otherwise it yields the `dist-upgrade` — which, unlike a
plain `upgrade`, may also install new dependencies and remove conflicting
packages, all of which the simulation counts. A failed simulation (`None`)
runs the real command anyway, pyinfra's fallback. `wait_for_lock` (`True`
→ 60s, an int → that many seconds) makes apt wait for its lock instead of
failing instantly. Upgrading needs root, so flag the request `sudo=True`
(or `su=True`). pyinfra's `auto_remove` parameter (appending
`--autoremove` to the same command) is deliberately split out into the
standalone `apt.auto_remove` operation, so orphaned dependencies can be
cleaned up without also performing an upgrade; `apt.dist_upgrade` never
passes `--autoremove`.

`apt.auto_remove` removes transitive dependencies that are no longer
needed — a reemote5 original: pyinfra only offers autoremove as the
`auto_remove` parameter of `apt.dist_upgrade`/`apt.upgrade`, never as a
standalone operation. The `AptAutoremovable` fact simulates the autoremove
and names exactly the no-longer-needed packages: a non-empty list yields
the `apt-get autoremove` (the reason names the packages, already sorted so
both phases report identically), an empty list is a no-op. `wait_for_lock`
behaves as above; removing packages needs root, so flag the request
`sudo=True` (or `su=True`). Yielding `apt.dist_upgrade` then
`apt.auto_remove` in one deploy composes the upgrade-then-cleanup flow
pyinfra expresses with a single `auto_remove=True` flag — each half an
explicit, separately plannable step.

`apt.key` installs a GPG keyring file for APT — the install half of
pyinfra's `apt.key`, the modern replacement for the deprecated `apt-key
add`: each vendor key lives in its own file under `/etc/apt/keyrings/`,
referenced per repository via `signed-by=`. `src` is the key content as a
seekable IO-like stream on the control machine (never a filename or URL —
a `str` raises `ValueError`; fetch a URL on the controller and pass its
bytes), and `dest` is the keyring filename or absolute path: a relative
name resolves under `/etc/apt/keyrings/`, an explicit path is used
verbatim. The operation is **non-idempotent** — a deliberate deviation
from pyinfra, which skips installation when the destination file exists:
it always reports a change and yields two descriptors, an
`AptKeyUploadCommand` transfer that spools the stream to a fixed `/tmp`
path over scp, then an `AptKeyInstallCommand` running one elevated
`sh -c` that creates the destination directory, dearmors the key with
`gpg --batch --dearmor` when the content is ASCII-armored (binary content
is copied verbatim), chmods the keyring `0644` and removes the spool file.
Flag the request `sudo=True` (or `su=True`) for the install; the transfer
stays unprivileged regardless, so the log shows a plain `scp` followed by
`sudo sh -c`. The stream is compared by identity across the prepare and
apply phases, so a deploy must define it once (e.g. at module level).
pyinfra's `keyserver`/`keyid` fetch and its `present=False` removal are
deliberately dropped: keys arrive as streams from the controller, and
removal is expressible with the `files` primitives. `dest` is required
because an anonymous stream provides no name to derive one from.

`apt.mark` holds packages back from upgrades, or releases held ones — a
reemote5 original: pyinfra ships no operation managing package hold
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
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].
`apt-mark`'s `auto`/`manual` installation-reason marks are deliberately
out of scope (only the hold state `AptHeldPackages` reports is managed),
and there is no `wait_for_lock` option — `apt-mark` is not `apt-get` and
takes no `DPkg::Lock::Timeout` wrapper.

`apt.packages` installs, upgrades or removes packages by name (pyinfra's
`apt.packages` parity). `packages` accepts a single name, a list/tuple or
`None`; a name can be pinned as `<pkg>=<version>`, and the `AptPackages`
fact supplies the installed versions, so a pin counts as present only
when that *exact* version is installed (pyinfra's `ensure_packages`
version match). `present=True` installs only what is missing;
`latest=True` additionally upgrades every installed, *unpinned* name —
pyinfra's legacy behavior, which upgrades blindly without checking
whether a newer version exists (the upgrade reuses the install command
without `extra_install_args`, as pyinfra builds it). `present=False`
removes only what is installed, with `purge=True` switching `remove` to
`purge` so configuration files go too. `update=True` runs `apt-get
update` first and is **stateless** — it always yields, like pyinfra's
non-idempotent `apt.update`; `upgrade=True` runs `apt-get upgrade`
first, gated on `SimulateOperationWillChange` exactly like
`apt.dist_upgrade` (all-zero proposed counts are a no-op, a failed
simulation runs anyway). `no_recommends` and `allow_downgrades` add the
matching `apt-get` flags to installs; `extra_install_args` /
`extra_uninstall_args` append raw arguments to the install / remove
command; `wait_for_lock` behaves as above. Installing, upgrading and
removing need root, so flag the request `sudo=True` (or `su=True`). Two
pyinfra options are deliberately dropped: `cache_time` (pyinfra skips
`update` while its stamp file is fresh and `touch`es it afterwards —
here `update` is always stateless and the keyword is absorbed with no
effect) and `force` (`--force-yes` is deprecated in modern apt, the
same decision as `apt.deb`). The installed-versions fact is
`AptPackages` (`apt list --installed`) rather than pyinfra's
`DebPackages` (`dpkg -l`) — the same substitution `apt.deb` made.

`apt.ppa` adds or removes an Ubuntu PPA repository (pyinfra's `apt.ppa`
parity). `src` is the full `ppa:user/repo` name and `present` selects the
direction: `present=True` runs `apt-add-repository -y <src>`,
`present=False` runs `apt-add-repository -y --remove <src>`. The operation
is **non-idempotent** — like pyinfra's it reads no fact and always reports a
change, yielding one command whether or not the PPA is already configured
(`apt-add-repository` is itself idempotent on the host, so a repeat add is
harmless). Adding or removing a repository writes to the root-owned apt
sources, so flag the request `sudo=True` (or `su=True`). It requires
`apt-add-repository` (from `software-properties-common`) on the target host;
pyinfra's `requires_command` gating is deliberately dropped, so a missing
binary exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`apt.repo` adds or removes one apt repository line (pyinfra's `apt.repo`
parity). `src` is the full source string (`deb http://X hardy main`),
`present` selects the direction, and `filename` scopes the edit to
`/etc/apt/sources.list.d/<filename>.list` instead of the main
`/etc/apt/sources.list`. Presence is decided by parsing `src` with
`AptSources`' legacy line parser and comparing against the fact's entries
— pyinfra's `parse_apt_repo(src) in AptSources` check — so a repository
already configured in *any* sources file (legacy or deb822) is a no-op,
and an unparseable `src` counts as absent but is still appended when
`present=True` (pyinfra's tolerance). An absent, wanted line is appended
with `echo`; a present, unwanted one is deleted with `sed`. Writing the
root-owned apt sources needs root, so flag the request `sudo=True` (or
`su=True`). Deliberate deviations from pyinfra, which delegates the edit
to `files.line`: the mutation rides self-contained apt descriptors
(reemote5 has no line operation — `files.edit` is its generic editing
primitive — and namespaces never reuse each other's operations), the
append wraps `sh -c` so `sudo` elevates the `>>` redirect rather than
opening it as the login user, and the removal uses a bare `sed -i`
without pyinfra's timestamped backup dance (the timestamp would differ
between the prepare and apply phases, breaking the determinism guard,
and an `&& rm` chain would leave its second command unprivileged under a
`sudo` prefix).

`apt.sources_file` manages a deb822 `.sources` file under
`/etc/apt/sources.list.d/` (pyinfra's `apt.sources_file` parity).
`filename` is the base name (the `.sources` extension is appended), and
`types`, `uris`, `suites`, `components` and `architectures` each accept a
list, a tuple or a space-separated string; `signed_by` names the keyring
file for signature verification (the natural pairing with `apt.key`).
`present=True` requires at least one URI and one suite — otherwise
`ValueError` is raised with pyinfra's message (reemote5's single
validation error type) — and is idempotent on `AptSources`: the fields
expand into one desired repository per type/URI/suite combination (in the
fact's list-valued `AptSource` shape), and when every combination is already
configured the operation is a no-op. Otherwise one request yields two
commands at different privilege levels, the `apt.key` pattern:
`AptSourcesFileUploadCommand` spools the generated deb822 text to a fixed
`/tmp` path over scp as the login user, then `AptSourcesFileInstallCommand`
runs one elevated `sh -c` that creates the directory, copies the file,
chmods it `0644` (pyinfra's fixed mode) and removes the spool — so flag the
request `sudo=True` (or `su=True`); the transfer stays unprivileged
regardless. This spool-and-install pair is the deliberate replacement for
pyinfra's `files.put` delegation: reemote5's transfers are unprivileged
scp and namespaces never reuse each other's operations.
`present=False` is idempotent on `AptSourcesFile`: it yields `rm -f`
only when the file exists (an empty leftover included) and is a no-op
when it is already absent. pyinfra checks its `files.File` fact for the
same gate; reemote5's apt namespace never imports another module's
facts, so the in-namespace `AptSourcesFile` content fact provides the
existence answer — the same pattern `dnf.repo` uses with `DnfRepoFile`.

`apt.update` refreshes the package lists (pyinfra's `apt.update` parity)
— the standalone form of `apt.packages`' stateless `update=True` prefix.
It runs the bare `apt-get update` (no non-interactive wrapper, since
`update` asks no questions) and is **non-idempotent**: it reads no fact
and always reports a change. `wait_for_lock` (`True` → 60s, an int → that
many seconds) makes apt wait for its lock instead of failing instantly.
Refreshing the lists needs root, so flag the request `sudo=True` (or
`su=True`). pyinfra's `cache_time` option (skip the update while the
`/var/lib/apt/periodic/update-success-stamp` file is fresh, and `touch`
it afterwards) is deliberately dropped — the same decision
`apt.packages` made, since the `File`/`Date` fact reads it needs would
break the namespaces' zero-cross-module-import invariant — so the
keyword is absorbed with no effect.

`apt.upgrade` upgrades all apt packages (pyinfra's `apt.upgrade` parity)
— the standalone form of `apt.packages`' `upgrade=True` prefix. The
`SimulateOperationWillChange` fact simulates the `upgrade` first: when
every proposed count is zero the operation is a no-op (pyinfra's
"skipped, no changes would be performed"), otherwise it yields the
`upgrade` — which, unlike `dist-upgrade`, never installs new dependencies
or removes conflicting packages. A failed simulation (`None`) runs the
real command anyway, pyinfra's fallback. `wait_for_lock` behaves as
above; upgrading needs root, so flag the request `sudo=True` (or
`su=True`). pyinfra's `auto_remove` parameter (appending `--autoremove`
to the same command) is deliberately dropped in favour of the standalone
`apt.auto_remove` operation — the same split `apt.dist_upgrade` made —
so `apt.upgrade` never passes `--autoremove` and the keyword is absorbed
with no effect. Yielding `apt.update` then `apt.upgrade` in one deploy
composes the classic refresh-then-patch flow, each half an explicit,
separately plannable step.

::: reemote5.modules.apt.apt
    options:
      members:
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

## Example

The read-only fact-gathering scripts `apt_apt_autoremovable.py`,
`apt_apt_held_packages.py`, `apt_apt_keys.py`, `apt_apt_packages.py`,
`apt_apt_policy.py`, `apt_apt_sources.py`, `apt_apt_upgradable.py` and
`apt_simulate_operation_will_change.py`
under
[`examples/modules/apt/`](../examples/modules/apt.md) gather
`AptAutoremovable`, `AptHeldPackages`, `AptKeys`, `AptPackages`,
`AptPolicy`, `AptSources`, `AptUpgradable` and
`SimulateOperationWillChange`
on an inventory of Ubuntu hosts with
[`gather_fact`][reemote5.drivers.gather_fact] and print each host's result
as JSON.

The `apt_deb.py` script beside them demonstrates the `apt.deb` operation: it
installs a `.deb` already on the host with version idempotence, yielding the
same request twice so the second is a guaranteed no-op in the apply phase.
The `apt_dist_upgrade.py` and `apt_auto_remove.py` scripts demonstrate the
upgrade-then-cleanup pair the same way — each yields its request twice to
show simulation-gated idempotence. The `apt_key.py` script demonstrates
`apt.key`: it installs a module-level key stream as
`/etc/apt/keyrings/reemote5-demo.gpg`, unprivileged spool transfer plus
elevated install in one request. The `apt_mark.py` script demonstrates
`apt.mark`: it holds the baseline packages back from upgrades with
`apt-mark hold`, yielding the same request twice so the second is a
guaranteed no-op in the apply phase. The `apt_packages.py` script demonstrates
`apt.packages`: it installs packages by name with exact-version
idempotence, yielding the same request twice so the second is a guaranteed
no-op in the apply phase. The `apt_ppa.py` script demonstrates `apt.ppa`:
it adds the Bitcoin PPA with `apt-add-repository` under `sudo` — a single
request, since the operation is non-idempotent and has no state for a second
request to converge on. The `apt_repo.py` script demonstrates `apt.repo`:
it appends the Canonical partner repository line to its own
`sources.list.d` file, yielding the same request twice so the second is a
guaranteed no-op in the apply phase. The `apt_sources_file.py` script
demonstrates `apt.sources_file`: it writes the Docker repository as a
modern deb822 `docker.sources` file — unprivileged spool transfer plus
elevated install in one request — yielding the same request twice so the
second is a guaranteed no-op in the apply phase. The `apt_update.py`
script demonstrates `apt.update`: it refreshes the package lists under
`sudo` — a single request, since the operation is non-idempotent and has
no state for a second request to converge on. The `apt_upgrade.py` script
demonstrates `apt.upgrade`: it upgrades all packages gated by the dry-run
simulation, yielding the same request twice to show simulation-gated
idempotence. All eleven are rendered on the
[apt examples page](../examples/modules/apt.md).