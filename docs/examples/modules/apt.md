# Module examples: apt

Scripts under `examples/modules/apt/` — one per fact or operation of the
[`apt`](../../modules/apt.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`apt_apt_autoremovable.py` reading
`AptAutoremovable`, `apt_apt_held_packages.py` reading `AptHeldPackages`,
`apt_apt_keys.py` reading `AptKeys`,
`apt_apt_packages.py` reading `AptPackages`, `apt_apt_policy.py` reading
`AptPolicy` for `nginx`, `apt_apt_sources.py` reading
`AptSources`, `apt_apt_update_stamp.py` reading `AptUpdateStamp`,
`apt_apt_upgradable.py` reading `AptUpgradable` and
`apt_simulate_operation_will_change.py` reading
`SimulateOperationWillChange` for `dist-upgrade`,
all over `gather_fact`, loading the shared `inventory.json` from the project
root and filtering the host list with the `is_ubuntu` guard before the
fan-out — `gather_fact` has no `when` parameter, so a skipped host is never
connected) are not rendered here —
each reads a single fact, and the facts themselves are documented on the
[`apt` module page](../../modules/apt.md).

Every example below loads that same shared `inventory.json` (validated up
front with `validate_inventory`, so a malformed file fails before any host
is materialized) and guards each request with `when=is_ubuntu` — a stable
module-level predicate reading only the static `host.group` inventory data,
so both deploy phases reach the same skip decision. The driver skips a
rejected request without materializing it: neither its commands nor the
facts behind them run on a non-Ubuntu host, and plan and log render
`[skipped: when guard is_ubuntu]`. The shared file holds one host in the
`ubuntu` group alongside hosts in other groups, so each run targets the
Ubuntu host and visibly skips every other one.

## Installing a `.deb` file with version idempotence

The package-file operation: `apt.deb` installs or removes a `.deb` that already
exists on the *target host*. It reads the
[`DebPackage`][reemote.modules.apt.apt.DebPackage] fact for the file's
`name`/`version` and the [`AptPackages`][reemote.modules.apt.apt.AptPackages]
fact for the installed versions of that name, and counts the package as present
only when the installed version matches the file's — so `present=True` installs
only when it does not and is otherwise a no-op, while `present=False` removes it
with `apt-get remove <name>`. The install runs the baseline's three-pass sequence (a
tolerant `dpkg -i`, an `apt-get install -f` to resolve dependencies, then a final
`dpkg -i` that fails fast if they are still missing). Where the baseline's `apt.deb`
can also *download* a remote `.deb` mid-deploy, reemote's `apt.deb` installs a
binary `.deb` that must already be on the host (and `files.download` is
text-only), so a URL `src` raises `ValueError`. The example therefore stages the
file itself first: a demo-side `curl` one-liner (a raw `StringCommand`, the same
pattern as `server_op_sysctl.py`'s `sysctl_set`) downloads the small,
self-contained `bat` `.deb` (~2.4 MB, no library dependencies — chosen to fit the
shared Ubuntu host's modest disk) to `/tmp`, unprivileged, before the install
reads it. The install rides under `sudo` (installing a package needs root), and
the script yields the same install request twice — the second is a guaranteed
no-op in the apply phase, because the first install is visible to it through the
fresh per-step fact cache (in the prepare phase, where nothing is sent, both plan
as changes).

::: apt_deb
    options:
      members: []

## Installing packages by name

The package operation: `apt.packages` mirrors the baseline's `apt.packages`,
installing, upgrading or removing packages by name. It reads the
[`AptPackages`][reemote.modules.apt.apt.AptPackages] fact for the installed
versions, so a `<pkg>=<version>` pin counts as present only when that exact
version is installed (the baseline's `ensure_packages` version match) —
`present=True` installs only what is missing, `latest=True` additionally
upgrades installed unpinned names (the baseline's legacy blind upgrade), and
`present=False` removes only what is installed, `purge=True` switching
`remove` to `purge`. `update=True` runs a stateless `apt-get update` first;
`upgrade=True` runs `apt-get upgrade`, gated by the
`SimulateOperationWillChange`
fact exactly like `apt.dist_upgrade`. `default_release` would add apt's
`-t <release>` to the install — and, unlike a raw `extra_install_args`, to
the `latest` upgrade too — so unpinned names resolve from a target release
(the example leaves it unset, since the demo host's own release is already
apt's default). `cache_valid_time` (Ansible's `apt` module option of the
same name) gates the `update=True` prefix on the
[`AptUpdateStamp`][reemote.modules.apt.apt.AptUpdateStamp] fact — the mtime
of `/var/lib/apt/periodic/update-success-stamp`, which `apt-get update`
itself touches on success — so a stamp at most `cache_valid_time` seconds
old (compared against the controller's UTC clock) skips the update with
reason `apt update skipped, cache is fresh`; `0` (the default) keeps the
always-update behaviour, and a missing or unreadable stamp counts as
stale. The example leaves it unset, since a demo install wants the fresh
package lists. The baseline's `cache_time` keyword is still absorbed by
`**kwargs` with no effect — the first-class spelling is `cache_valid_time`,
matching Ansible; `force` (`--force-yes`) is dropped as
deprecated, the same decision as `apt.deb`. The install rides under `sudo`
(managing packages needs root), and the script yields the same request
twice — the second is a guaranteed no-op in the apply phase, because the
first install is visible to it through the fresh per-step fact cache (in
the prepare phase, where nothing is sent, both plan as changes).

::: apt_packages
    options:
      members: []

## Refreshing the package lists

The standalone form of `apt.packages`' `update=True` prefix:
`apt.update` mirrors the baseline's `apt.update`, refreshing the package lists
with the bare `apt-get update` — no non-interactive wrapper, since
`update` asks no questions. By default it is **non-idempotent**: it reads no
fact and always reports a change. `cache_valid_time` (Ansible's `apt` module
option of the same name) gates that yield on the
[`AptUpdateStamp`][reemote.modules.apt.apt.AptUpdateStamp] fact — the mtime of
`/var/lib/apt/periodic/update-success-stamp`, which `apt-get update` itself
touches on success — so a stamp at most `cache_valid_time` seconds old
(compared against the controller's UTC clock) returns with reason `apt update
skipped, cache is fresh` and no change is reported, making the operation
idempotent within that window; `0` (the default) keeps the always-update
behaviour, and a missing or unreadable stamp counts as stale. The example
leaves it unset so both the prepare plan and the apply log show the refresh
as a change. The baseline's `cache_time` keyword is still absorbed by
`**kwargs` with no effect — the first-class spelling is `cache_valid_time`,
matching Ansible. The refresh rides under `sudo`
(writing the root-owned `/var/lib/apt` state needs root), and the script
yields the request only once — there is no state for a second request to
converge on when the always-update default is in force.

::: apt_update
    options:
      members: []

## Upgrading all packages

The standalone form of `apt.packages`' `upgrade=True` prefix:
`apt.upgrade` mirrors the baseline's `apt.upgrade`, gated by the
`SimulateOperationWillChange`
fact — an `upgrade --dry-run` whose `N upgraded, N newly installed, N to
remove and N not upgraded.` summary line decides whether the real upgrade
would do anything. All-zero counts are a no-op; a failed simulation (no
summary line, `None`) runs the real command anyway, the baseline's fallback.
Unlike `dist-upgrade`, a plain `upgrade` never installs new dependencies
or removes conflicting packages. Where the baseline's operation also takes an
`auto_remove` parameter, reemote drops it in favour of the standalone
`apt.auto_remove` below — the same split `apt.dist_upgrade` made — so this
operation never passes `--autoremove`. The upgrade rides under `sudo`
(upgrading needs root; the dry-run itself does not), and the script yields
the same request twice — the second is a no-op in the apply phase once the
first upgrade left nothing more to do, because the fresh per-step fact
cache re-simulates against the upgraded state (in the prepare phase, where
nothing is sent, both plan as changes). Yielding `apt.update` then
`apt.upgrade` in one deploy composes the classic refresh-then-patch flow.

::: apt_upgrade
    options:
      members: []

## Upgrading all packages with dist-upgrade

The whole-machine upgrade: `apt.dist_upgrade` mirrors the baseline's
`apt.dist_upgrade`, gated by the
`SimulateOperationWillChange`
fact — a `dist-upgrade --dry-run` whose `N upgraded, N newly installed,
N to remove and N not upgraded.` summary line decides whether the real
upgrade would do anything. All-zero counts are a no-op; a failed simulation
(no summary line, `None`) runs the real command anyway, the baseline's fallback.
Unlike a plain `upgrade`, the `dist-upgrade` may also install new
dependencies and remove conflicting packages — the simulation counts those
too. Where the baseline's operation also takes an `auto_remove` parameter,
reemote splits that into the standalone `apt.auto_remove` below, so this
operation never passes `--autoremove`. The upgrade rides under `sudo`
(upgrading needs root; the dry-run itself does not), and the script yields
the same request twice — the second is a no-op in the apply phase once the
first upgrade left nothing more to do, because the fresh per-step fact
cache re-simulates against the upgraded state (in the prepare phase, where
nothing is sent, both plan as changes).

::: apt_dist_upgrade
    options:
      members: []

## Removing orphaned dependencies standalone

The cleanup half of the split: `apt.auto_remove` is a reemote original —
the baseline only offers autoremove as the `auto_remove` parameter of
`apt.dist_upgrade`/`apt.upgrade`, never as a standalone operation, so
orphaned dependencies cannot be cleaned up without also performing an
upgrade. It reads the
[`AptAutoremovable`][reemote.modules.apt.apt.AptAutoremovable] fact, whose
`apt-get --just-print autoremove` simulation names exactly the
no-longer-needed packages: a non-empty list runs the real
`apt-get autoremove`, an empty list is a no-op. The removal rides under
`sudo`, and the script yields the same request twice — the second is a
no-op in the apply phase once the first cleanup left nothing autoremovable.
Composing `apt.dist_upgrade` then `apt.auto_remove` in one deploy gives the
upgrade-then-cleanup flow the baseline expresses with a single `auto_remove=True`
flag, each half an explicit, separately plannable step.

::: apt_auto_remove
    options:
      members: []

## Installing a GPG keyring from a stream

The key-management operation: `apt.key` installs a GPG key as a keyring file
under `/etc/apt/keyrings/`, the modern replacement for the deprecated
`apt-key add` — a sources entry then trusts the key only for its own
repository via `signed-by=`. `src` is the key content as a seekable IO-like
stream on the control machine (fetch a URL there and pass its bytes; a
filename/URL `str` raises `ValueError`), and `dest` names the keyring file. One request yields two commands at
different privilege levels: an `AptKeyUploadCommand` transfer that spools
the stream into a dest-derived `/tmp` directory over scp as the login user, then an
`AptKeyInstallCommand` running one elevated `sh -c` that creates the
keyrings directory, dearmors ASCII-armored content with `gpg --dearmor`
(binary content is copied verbatim), chmods the keyring `0644` and removes
the spool directory — so the log shows a plain `scp` line followed by
`sudo sh -c`. Unlike the baseline's existence-checked `apt.key`, this one is
deliberately non-idempotent (like `dnf.key`): it always installs and always
reports a change. The stream is compared by identity across the prepare
and apply phases, so the script defines it once at module level — building
a fresh stream inside the deploy would raise `DeterminismError`. The embedded
stream is the real Docker Release (CE deb) signing key (the armored body of
`https://download.docker.com/linux/ubuntu/gpg`), so the elevated `gpg --dearmor`
produces a valid keyring rather than failing on placeholder bytes; swap in your
own vendor key's armor to trust a different repository.

::: apt_key
    options:
      members: []

## Holding packages back from upgrades

The hold-state operation: `apt.mark` is a reemote original — the baseline ships
no operation managing package hold states, so pinning a critical package at
its current version (without uninstalling anything) had no first-class
form. A held package is skipped by `apt upgrade`/`apt.dist_upgrade` until
released. It reads the
[`AptHeldPackages`][reemote.modules.apt.apt.AptHeldPackages] fact
(`apt-mark showhold`), so `held=True` runs `apt-mark hold` for only the
requested names not already held and `held=False` runs `apt-mark unhold`
for only the names currently held — a request whose names are all in the
wanted state is a no-op. The hold rides under `sudo` (marking writes to the
root-owned dpkg database), and the script yields the same request twice —
the second is a guaranteed no-op in the apply phase, because the first
hold is visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes). A third request then
releases the holds again (`held=False`), so the example is *self-restoring*: it
leaves the host's marks as found, because a lingering hold on `tree` would break
the later `apt_packages.py` install in the shared-host sweep. Composing `apt.mark`
then `apt.upgrade` in one deploy gives the hold-then-patch flow:
everything except the held packages upgrades.

::: apt_mark
    options:
      members: []

## Adding an Ubuntu PPA repository

The repository operation: `apt.ppa` mirrors the baseline's `apt.ppa`, adding (or
with `present=False` removing) an Ubuntu PPA via `apt-add-repository`. `src`
is the full `ppa:user/repo` name — here the Bitcoin PPA, the baseline's documented
example — and the operation runs `apt-add-repository -y <src>` (`--remove`
for `present=False`), which requires `software-properties-common` on the
target host. The operation is **idempotent** on
[`AptSources`][reemote.modules.apt.apt.AptSources]: it resolves `ppa:user/repo`
to its Launchpad archive and reports a change only when the entry is actually
added or removed — a deviation from the baseline, which reads no fact and always
re-runs the command, and what unblocks the `register` + `when: ... is changed`
pattern an Ansible `apt_repository` migration relies on. The add rides under
`sudo` (writing the apt sources needs root), and the script yields the add twice
— the second a guaranteed no-op in the apply phase, since the first add is
visible to it through the fresh per-step fact cache (both plan as changes in the
prepare phase, where nothing is sent) — then a `present=False` remove, so the
example is *self-restoring*: it leaves the host's sources as found. The Bitcoin
PPA publishes no `noble` Release file, so a lingering entry would make the later
`apt_update.py` step fail (`apt-get update` exits 100 on a repository with no
Release); the add-then-remove cycle keeps the shared-host sweep clean.

::: apt_ppa
    options:
      members: []

## Adding an apt repository line

The source-line operation: `apt.repo` mirrors the baseline's `apt.repo`, adding
(or with `present=False` removing) one apt source line. `src` is the full
source string — here the Canonical partner repository for Ubuntu noble —
and `filename` scopes it to its own
`/etc/apt/sources.list.d/canonical-partner.list` instead of the main
`/etc/apt/sources.list`. It parses `src` and compares against the
[`AptSources`][reemote.modules.apt.apt.AptSources] fact, so a repository
already configured in any sources file is a no-op. Where the baseline delegates
the edit to `files.line` and so patches the file in place with `echo >>`
and `sed -i`, reemote *rewrites* the managed file through the
spool-and-install family `apt.key` belongs to: the file's current text is
read with the
[`AptSourcesFile`][reemote.modules.apt.apt.AptSourcesFile] content fact, a
pure transform adds or deletes the line, the result is spooled to a `/tmp`
path named after the managed file by an unprivileged scp upload (a transfer
never elevates), and an elevated `sh -c` installs it over the protected
file with mode `0644` and removes the spool. Because the transform works on
the real content, the file's other lines and comments survive verbatim and
a line the file already carries is never written twice — and the baseline's
timestamped sed backup, non-deterministic between the two phases, goes with
the in-place edit. The request rides under `sudo` (writing the apt sources
needs root), and the script yields the same request twice — the second is a
guaranteed no-op in the apply phase, because the first install is visible to
it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes). A third request then removes the line
again (`present=False`), so the example is *self-restoring*: the Canonical
partner archive is unreachable from the demo Ubuntu host, so leaving it
configured would stall every later `apt-get update` on connection timeouts —
the add-then-remove cycle keeps the shared-host sweep clean and fast.

::: apt_repo
    options:
      members: []

## Managing a deb822 sources file

The modern-format sibling of `apt.repo`: `apt.sources_file` mirrors
the baseline's `apt.sources_file`, writing (or with `present=False` removing) a
complete deb822 `.sources` file under `/etc/apt/sources.list.d/` — here the
Docker repository for Ubuntu noble (the target host's suite) as `docker.sources`.
The repository is described structurally (`types`, `uris`, `suites`,
`components`, `architectures`,
`signed_by`) rather than as a source line, and each list option expands into
one entry per combination. The write direction compares every desired entry
against the [`AptSources`][reemote.modules.apt.apt.AptSources] fact, so a
file whose repositories are all already configured is a no-op. Where the baseline
delegates the write to `files.put`, reemote keeps the namespaces separate:
the generated deb822 text is spooled to `/tmp` by an unprivileged scp upload
(a transfer never elevates), then an elevated `sh -c` installs it into
`/etc/apt/sources.list.d/` with mode `0644` and removes the spool directory — the
same privilege split as `apt.key`. Removal is idempotent too: the
[`AptSourcesFile`][reemote.modules.apt.apt.AptSourcesFile] fact gates the
`rm -f`, so an already-absent file is a no-op. The script yields the same
write request twice: the prepare phase plans both as changes, while the apply
phase writes on the first and reports the second as a no-op through the
fresh per-step fact cache. A third request then removes the file again
(`present=False`), so the example is *self-restoring*: a lingering `docker.sources`
whose `signed-by` keyring is not installed would make the later `apt_update.py`
step fail (`apt-get update` exits 100 on an unsigned repository), so the
write-then-remove cycle keeps the shared-host sweep clean.

::: apt_sources_file
    options:
      members: []