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
`AptSources`, `apt_apt_upgradable.py` reading `AptUpgradable` and
`apt_simulate_operation_will_change.py` reading
`SimulateOperationWillChange` for `dist-upgrade`,
all over `gather_fact`, with the connection information of
the Ubuntu server from `inventory_ubuntu.json`) are not rendered here —
each reads a single fact, and the facts themselves are documented on the
[`apt` module page](../../modules/apt.md).

## Installing a `.deb` file with version idempotence

The package-file operation: `apt.deb` installs or removes a `.deb` that already
exists on the *target host*. It reads the
[`DebPackage`][reemote5.modules.apt.apt.DebPackage] fact for the file's
`name`/`version` and the [`AptPackages`][reemote5.modules.apt.apt.AptPackages]
fact for the installed versions of that name, and counts the package as present
only when the installed version matches the file's — so `present=True` installs
only when it does not and is otherwise a no-op, while `present=False` removes it
with `apt-get remove <name>`. The install runs pyinfra's three-pass sequence (a
tolerant `dpkg -i`, an `apt-get install -f` to resolve dependencies, then a final
`dpkg -i` that fails fast if they are still missing). Where pyinfra's `apt.deb`
can also *download* a remote `.deb` mid-deploy, reemote5 has no download
operation, so a URL `src` raises `ValueError`; upload the file from the
controller with `files.put` first. The install rides under `sudo` (installing a
package needs root), and the script yields the same request twice — the second
is a guaranteed no-op in the apply phase, because the first install is visible
to it through the fresh per-step fact cache (in the prepare phase, where nothing
is sent, both plan as changes).

::: apt_deb
    options:
      members: []

## Installing packages by name

The package operation: `apt.packages` mirrors pyinfra's `apt.packages`,
installing, upgrading or removing packages by name. It reads the
[`AptPackages`][reemote5.modules.apt.apt.AptPackages] fact for the installed
versions, so a `<pkg>=<version>` pin counts as present only when that exact
version is installed (pyinfra's `ensure_packages` version match) —
`present=True` installs only what is missing, `latest=True` additionally
upgrades installed unpinned names (pyinfra's legacy blind upgrade), and
`present=False` removes only what is installed, `purge=True` switching
`remove` to `purge`. `update=True` runs a stateless `apt-get update` first;
`upgrade=True` runs `apt-get upgrade`, gated by the
`SimulateOperationWillChange`
fact exactly like `apt.dist_upgrade`. Where pyinfra's `cache_time` skips the
update while its stamp file is fresh, reemote5 deliberately drops the option
(update is always stateless); `force` (`--force-yes`) is dropped as
deprecated, the same decision as `apt.deb`. The install rides under `sudo`
(managing packages needs root), and the script yields the same request
twice — the second is a guaranteed no-op in the apply phase, because the
first install is visible to it through the fresh per-step fact cache (in
the prepare phase, where nothing is sent, both plan as changes).

::: apt_packages
    options:
      members: []

## Refreshing the package lists

The standalone form of `apt.packages`' stateless `update=True` prefix:
`apt.update` mirrors pyinfra's `apt.update`, refreshing the package lists
with the bare `apt-get update` — no non-interactive wrapper, since
`update` asks no questions. It is **non-idempotent**: it reads no fact and
always reports a change. Where pyinfra's `cache_time` skips the update
while its stamp file is fresh (and `touch`es it afterwards), reemote5
deliberately drops the option — the same decision `apt.packages` made —
so the update is always stateless. The refresh rides under `sudo`
(writing the root-owned `/var/lib/apt` state needs root), and the script
yields the request only once — there is no state for a second request to
converge on, so both the prepare plan and the apply log show it as a
change.

::: apt_update
    options:
      members: []

## Upgrading all packages

The standalone form of `apt.packages`' `upgrade=True` prefix:
`apt.upgrade` mirrors pyinfra's `apt.upgrade`, gated by the
`SimulateOperationWillChange`
fact — an `upgrade --dry-run` whose `N upgraded, N newly installed, N to
remove and N not upgraded.` summary line decides whether the real upgrade
would do anything. All-zero counts are a no-op; a failed simulation (no
summary line, `None`) runs the real command anyway, pyinfra's fallback.
Unlike `dist-upgrade`, a plain `upgrade` never installs new dependencies
or removes conflicting packages. Where pyinfra's operation also takes an
`auto_remove` parameter, reemote5 drops it in favour of the standalone
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

The whole-machine upgrade: `apt.dist_upgrade` mirrors pyinfra's
`apt.dist_upgrade`, gated by the
`SimulateOperationWillChange`
fact — a `dist-upgrade --dry-run` whose `N upgraded, N newly installed,
N to remove and N not upgraded.` summary line decides whether the real
upgrade would do anything. All-zero counts are a no-op; a failed simulation
(no summary line, `None`) runs the real command anyway, pyinfra's fallback.
Unlike a plain `upgrade`, the `dist-upgrade` may also install new
dependencies and remove conflicting packages — the simulation counts those
too. Where pyinfra's operation also takes an `auto_remove` parameter,
reemote5 splits that into the standalone `apt.auto_remove` below, so this
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

The cleanup half of the split: `apt.auto_remove` is a reemote5 original —
pyinfra only offers autoremove as the `auto_remove` parameter of
`apt.dist_upgrade`/`apt.upgrade`, never as a standalone operation, so
orphaned dependencies cannot be cleaned up without also performing an
upgrade. It reads the
[`AptAutoremovable`][reemote5.modules.apt.apt.AptAutoremovable] fact, whose
`apt-get --just-print autoremove` simulation names exactly the
no-longer-needed packages: a non-empty list runs the real
`apt-get autoremove`, an empty list is a no-op. The removal rides under
`sudo`, and the script yields the same request twice — the second is a
no-op in the apply phase once the first cleanup left nothing autoremovable.
Composing `apt.dist_upgrade` then `apt.auto_remove` in one deploy gives the
upgrade-then-cleanup flow pyinfra expresses with a single `auto_remove=True`
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
the stream to a fixed `/tmp` path over scp as the login user, then an
`AptKeyInstallCommand` running one elevated `sh -c` that creates the
keyrings directory, dearmors ASCII-armored content with `gpg --dearmor`
(binary content is copied verbatim), chmods the keyring `0644` and removes
the spool file — so the log shows a plain `scp` line followed by
`sudo sh -c`. Unlike pyinfra's existence-checked `apt.key`, this one is
deliberately non-idempotent (like `dnf.key`): it always installs and always
reports a change. The stream is compared by identity across the prepare
and apply phases, so the script defines it once at module level — building
a fresh stream inside the deploy would raise `DeterminismError`.

::: apt_key
    options:
      members: []

## Holding packages back from upgrades

The hold-state operation: `apt.mark` is a reemote5 original — pyinfra ships
no operation managing package hold states, so pinning a critical package at
its current version (without uninstalling anything) had no first-class
form. A held package is skipped by `apt upgrade`/`apt.dist_upgrade` until
released. It reads the
[`AptHeldPackages`][reemote5.modules.apt.apt.AptHeldPackages] fact
(`apt-mark showhold`), so `held=True` runs `apt-mark hold` for only the
requested names not already held and `held=False` runs `apt-mark unhold`
for only the names currently held — a request whose names are all in the
wanted state is a no-op. The hold rides under `sudo` (marking writes to the
root-owned dpkg database), and the script yields the same request twice —
the second is a guaranteed no-op in the apply phase, because the first
hold is visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes). Composing `apt.mark`
then `apt.upgrade` in one deploy gives the hold-then-patch flow:
everything except the held packages upgrades.

::: apt_mark
    options:
      members: []

## Adding an Ubuntu PPA repository

The repository operation: `apt.ppa` mirrors pyinfra's `apt.ppa`, adding (or
with `present=False` removing) an Ubuntu PPA via `apt-add-repository`. `src`
is the full `ppa:user/repo` name — here the Bitcoin PPA, pyinfra's documented
example — and the operation runs `apt-add-repository -y <src>` (`--remove`
for `present=False`), which requires `software-properties-common` on the
target host. Unlike the idempotent operations above, this one is
deliberately **non-idempotent**, exactly as pyinfra marks it: it reads no
fact and always reports a change, yielding one command whether or not the
PPA is already configured (`apt-add-repository` is itself idempotent on the
host, so a repeat add is harmless). The add rides under `sudo` (writing the
apt sources needs root), and the script yields the request only once — there
is no state for a second request to converge on, so both the prepare plan and
the apply log show it as a change.

::: apt_ppa
    options:
      members: []

## Adding an apt repository line

The source-line operation: `apt.repo` mirrors pyinfra's `apt.repo`, adding
(or with `present=False` removing) one apt source line. `src` is the full
source string — here the Canonical partner repository for Ubuntu noble —
and `filename` scopes it to its own
`/etc/apt/sources.list.d/canonical-partner.list` instead of the main
`/etc/apt/sources.list`. It parses `src` and compares against the
[`AptSources`][reemote5.modules.apt.apt.AptSources] fact, so a repository
already configured in any sources file is a no-op. Where pyinfra delegates
the edit to `files.line`, reemote5 has no line operation, so the append
rides a self-contained apt descriptor — `sh -c 'echo <src> >> <file>'`,
the wrapper keeping the `>>` redirect inside the shell the driver elevates
— and the removal uses a bare `sed -i` delete without pyinfra's timestamped
backup dance. The append rides under `sudo` (writing the apt sources needs
root), and the script yields the same request twice — the second is a
guaranteed no-op in the apply phase, because the first append is visible to
it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

::: apt_repo
    options:
      members: []

## Managing a deb822 sources file

The modern-format sibling of `apt.repo`: `apt.sources_file` mirrors
pyinfra's `apt.sources_file`, writing (or with `present=False` removing) a
complete deb822 `.sources` file under `/etc/apt/sources.list.d/` — here the
Docker repository as `docker.sources`. The repository is described
structurally (`types`, `uris`, `suites`, `components`, `architectures`,
`signed_by`) rather than as a source line, and each list option expands into
one entry per combination. The write direction compares every desired entry
against the [`AptSources`][reemote5.modules.apt.apt.AptSources] fact, so a
file whose repositories are all already configured is a no-op. Where pyinfra
delegates the write to `files.put`, reemote5 keeps the namespaces separate:
the generated deb822 text is spooled to `/tmp` by an unprivileged scp upload
(a transfer never elevates), then an elevated `sh -c` installs it into
`/etc/apt/sources.list.d/` with mode `0644` and removes the spool file — the
same privilege split as `apt.key`. Removal is idempotent too: the
[`AptSourcesFile`][reemote5.modules.apt.apt.AptSourcesFile] fact gates the
`rm -f`, so an already-absent file is a no-op. The script yields the same
request twice: the prepare phase plans both as changes, while the apply
phase writes on the first and reports the second as a no-op through the
fresh per-step fact cache.

::: apt_sources_file
    options:
      members: []