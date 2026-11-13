# Module examples: dnf

Scripts under `examples/modules/dnf/` — one per fact or operation of the
[`dnf`](../../modules/dnf.md) module. See the [Examples overview](../../examples.md)
for the shared "connects for real" warning and the error-reporting contract
every script follows.

The read-only fact-gathering examples (`DnfRepositories`, `DnfRepoList`,
`DnfEnabledModules`, `DnfDisabledModules`, `DnfInstalledModules`, `DnfGroups`,
`DnfAvailableGroups` and `RpmGpgKeys` over `gather_fact`) live alongside the
operation examples in `examples/modules/dnf/`. They are not rendered here —
each reads a single fact, and the facts themselves are documented on the
[`dnf` module page](../../modules/dnf.md).

Every script reads the shared project-root `inventory.json` (validated up
front with `validate_inventory`) and guards on the `"centos"` group with a
stable module-level `is_centos` predicate (`host.group == "centos"`), the
same convention as the
[`composition` demonstrations](../demonstrations/composition.md). The
operation examples carry `when=is_centos` on every request: the driver skips
a rejected request without materializing it, so on the Alpine and Ubuntu
hosts neither the `dnf` commands nor the facts behind them run, and plan and
log render `[skipped: when guard is_centos]` in the request's lockstep slot.
The read-only fact examples guard differently by necessity — `gather_fact`
takes no `when` parameter, so their predicate filters the host list before
the fan-out and the run prints a `== Skipped (when guard is_centos): ... ==`
line. The shared inventory holds two CentOS hosts (`web1`, `web2`) alongside
hosts in other groups, so the skip is visible on every run.

## The base example

The starting point for everything below: a single flat deploy generator
installing httpd and removing the welcome configuration the package drops,
run end to end over SSH in both phases. `dnf.packages` reads the
[`DnfPackages`][reemote.modules.dnf.dnf.DnfPackages] fact for the installed
versions, so a `<pkg>=<version>` pin counts as present only when that exact
version is installed (the [`apt.packages`][reemote.modules.apt.apt.packages]
precedent, not a name-only match) — `present=True` installs only what is
missing or mismatched, `latest=True` additionally upgrades installed unpinned
names, and `present=False` removes only what is installed. `allow_downgrade=True`
renders `dnf downgrade --allowerasing` for a pinned package whose name is
already installed at a *different* version, since `dnf install` alone refuses
to cross from a newer installed version to an older requested one and has no
equivalent flag (unlike apt's `--allow-downgrades`); without it, a mismatch is
treated exactly like a missing name and left for dnf's own default behavior to
reject (the example leaves it unset, since httpd is installed unpinned).
Repo scoping has two first-class spellings: `repos=(<id>, ...)` means
*enable-only-named* (`--disablerepo='*' --enablerepo=<id>...`), while
`disable_repos=(<id>, ...)` means *every repo except these* (a bare
`--disablerepo=<id>` per name) — Ansible's `disablerepo: appstream` shape,
first-class rather than smuggled through `extra_install_args`. And
`update_cache=True` yields a scoped `dnf -y makecache` ahead of the
transaction — Ansible's `update_cache: true` — so a metadata refresh is
first-class rather than a separate `dnf.makecache` request. The example
leaves all three unset: httpd resolves from CentOS's default repos and the
base image's cache is fresh.

::: dnf_packages
    options:
      members: []

## Updating all packages

The whole-system update: `dnf.update` runs `dnf update -y` over every installed
package. Like the baseline's `dnf.update` it is **non-idempotent** — it reads no fact
(there is no cheap way to know whether any package has a pending upgrade), so it
always reports a change and always runs; dnf itself is a no-op when the system is
already up to date. Updating the whole system needs root, so the request is
flagged `sudo=True` and the driver elevates the command. Because there is no
state to compare, both the dry-run plan and the apply log report the same single
change and render `$ sudo dnf update -y`.

::: dnf_update
    options:
      members: []

## Adding a repository file with content idempotence

The repository-file operation: `dnf.repo` builds an
`/etc/yum.repos.d/<name>.repo` file from its arguments (`baseurl`,
`description`, `gpgkey`, …) and writes it only when the content differs from
what the [`DnfRepoFile`][reemote.modules.dnf.dnf.DnfRepoFile] fact reads back
— so re-running is a no-op. Where the baseline's `dnf.repo` can also *download* a
remote `.repo` file, reemote's `dnf.repo` *constructs* the file from its
arguments, so a URL `src` raises `ValueError` (use `files.download` to place a
vendor `.repo` straight from a URL).
The script yields the same repository request twice — the second is a
guaranteed no-op in the apply phase, because the first write is visible to it
through the fresh per-step fact cache (in the prepare phase, where nothing is
sent, both plan as changes). The write rides under `sudo` (`/etc/yum.repos.d`
is root-owned) as a two-command pair: an unprivileged scp transfer spools the
generated text to `/tmp`, then one elevated `sh -c` creates the directory,
copies the file, chmods it `0644` and removes the spool — the `apt.sources_file`
pattern. Beyond the baseline's option set, `dnf.repo` also accepts the
ansible-datadog role's `yum_repository` fields — `includepkgs`,
`repo_gpgcheck`, `proxy`, `proxy_username` and `proxy_password` — each
omitted from the constructed file when unset (so the plain requests here
stay byte-compatible with the older parity content); a `proxy_password`
installs the file `0600` instead of `0644` so the password is not
world-readable.

::: dnf_repo
    options:
      members: []

## Refreshing one repository's metadata

The scoped cache refresh: `dnf.makecache` downloads repository metadata
(`dnf -y makecache`) and, with `repos` set, scopes the fetch to just the
named ids — `dnf -y makecache '--disablerepo=*' --enablerepo=DockerCE`,
the ansible-datadog role's `makecache --disablerepo="*"
--enablerepo=datadog` step. It never upgrades a package (unlike
`dnf.packages(update=True)`, which is a full `dnf update -y`) and never
touches another repo's cache. The operation is **non-idempotent** like
`dnf.update`: it reads no fact and always runs — dnf itself is cheap on a
warm cache. The role gates the refresh on the repo task's `changed` flag;
reemote cannot gate on a same-deploy write determinism-safely, so the
example places the unconditional refresh right after the `dnf.repo`
request that ensures the Docker CE file (with the same arguments as the
repo example above, so the shared hosts converge on one content). Both
requests ride under `sudo` and carry the `is_centos` guard.

::: dnf_makecache
    options:
      members: []

## Dropping one repository's cached metadata

The scoped cache clean: `dnf.clean(what="metadata", repos=("DockerCE",))`
renders `dnf clean metadata '--disablerepo=*' --enablerepo=DockerCE` — the
ansible-datadog role's `clean metadata` step, needed when a repo's
*content* moved (the role's case is an agent major-version downgrade),
where dnf's own metadata expiry keeps serving the stale cache.
`dnf.packages(clean=True)` cannot express this: it is an unscoped
`dnf clean all` over every repo's metadata *and* cached packages.
`what` accepts dnf's own cache kinds (`all`, `metadata`, `packages`,
`dbcache`, `expire-cache`; anything else raises `ValueError`). Like
`dnf.makecache` the operation is stateless and unconditional, placed right
after the `dnf.repo` request in the same deploy; dnf re-fetches the
metadata on next use, or immediately via the `dnf.makecache` example
above.

::: dnf_clean
    options:
      members: []

## Installing a `.rpm` file with version idempotence

The package-file operation: `dnf.rpm` installs or removes a `.rpm` that already
exists on the *target host*. It reads the
[`RpmPackage`][reemote.modules.dnf.dnf.RpmPackage] fact twice — once for the
file's `name`/`version`, once for the installed package of that name — and
counts the package as present only when the installed version matches the
file's, so `present=True` installs with `rpm -i` only when it does not and is
otherwise a no-op, while `present=False` removes it with `dnf remove -y <name>`.
Where the baseline's `dnf.rpm` can also *download* a remote `.rpm` mid-deploy,
reemote's `dnf.rpm` installs a binary `.rpm` that must already be on the host
(and `files.download` is text-only), so a URL `src` raises `ValueError`. The
example therefore stages the file itself first: a demo-side `curl` one-liner (a
raw `StringCommand`, the same pattern as `apt_deb.py`'s `download_deb`)
downloads `sl` — the small steam-locomotive `ls` joke, whose only library
dependency `ncurses-libs` is part of the CentOS base — onto the host before
`dnf.rpm` reads it. The install rides under `sudo` (installing a package needs
root), and the script yields the same install request twice — the second is a
guaranteed no-op in the apply phase, because the first install is visible to it
through the fresh per-step fact cache (in the prepare phase, where nothing is
sent, both plan as changes). A final `present=False` request then removes `sl`
again, so the example is **self-restoring**: it leaves the shared CentOS hosts
as it found them and every run starts from the same not-installed state.

Unlike `dnf.key`'s import below, this example applies cleanly even on the
SELinux `staff_t`-confined CentOS hosts: `dnf.rpm`'s install is a *plain*
`sudo rpm -i <src>` (no `sh -c` wrapper), and a direct `sudo rpm` exec
transitions to the privileged rpm domain that may write the rpm database;
the removal is `sudo dnf remove -y sl`, which works the same way.

::: dnf_rpm
    options:
      members: []

## Importing a GPG key from a stream

The key operation: `dnf.key` manages the target host's rpm keyring in both
directions. The import direction (`present=True`, the default) is
**non-idempotent**: like the baseline's `dnf.key` it reads no fact and always
reports a change (rpm itself deduplicates an already-imported key). Where
the baseline's `src` is a *filename or URL* the target host reads directly
(`rpm --import <src>`), reemote has no controller-side file locations, so the
source is a *stream only* — the example uses a module-level `io.BytesIO`,
defined once so both phases pass the identical object (request equality
compares it by identity), and a `str` `src` raises `ValueError`. The stream
embeds the **real** Docker Release (CE rpm) signing key (the armored body of
`https://download.docker.com/linux/centos/gpg`), so `rpm --import` accepts it
rather than rejecting placeholder bytes. The one
request yields *two* commands at different privilege levels: a transfer that
spools the stream to a `/tmp` path over scp as the login user (transfers are
never elevated), then an elevated `rpm --import` of that spool file — wrapped
in `sh -c` so the import *and* its cleanup `rm` both run as root — hence the
request is flagged `sudo=True`.

The script's second request shows the removal direction (`present=False`) —
a reemote extension beyond the baseline's import-only operation, mirroring
Ansible's `rpm_key state=absent` (the step the ansible-datadog role uses to
drop the legacy `4172A230` signing key). An imported key lives in the rpm
database as a `gpg-pubkey-<keyid>-<release>` pseudo-package, so removal names
a `keyid` (8 hex digits, or the 16-digit long form matched by its last 8;
case-insensitive), never a stream — passing `src` with `present=False`
raises `ValueError`. Removal **is idempotent**: it compares the requested id
against the [`RpmGpgKeys`][reemote.modules.dnf.dnf.RpmGpgKeys] fact and
yields an elevated `rpm -e gpg-pubkey-…` only for keys actually present, so a
host without the legacy key plans a no-op. The erase is a *plain* `sudo rpm
-e` (a single command, no `sh -c` wrapper), so — like `dnf.rpm` above and
unlike the wrapped import — it applies cleanly even on the confined hosts.

**Confinement caveat (CentOS hosts, import direction).** The dry-run plan and
the pytest suite always pass, but the live apply is blocked on a CentOS host
whose sudo is confined to SELinux `staff_t`. The scp spool lands as the login
user's `0600` file, and the elevated `sh -c 'rpm --import …'` wrapper runs in
`staff_t` without `CAP_DAC_OVERRIDE`, so it cannot even read that spool — rpm
reports `import read failed`. (The `sh -c` wrapper is also why a *direct*
`sudo rpm --import`, which transitions to the privileged rpm domain, would
succeed where the wrapped form cannot take the rpmdb transaction lock.) This
is the same `staff_t` confinement the `selinux`, `sysctl` and `systemd`
examples document; the import is correct as written and runs for real once
the host's sudo is unconfined (or SELinux is permissive). Contrast `dnf.rpm`
above and the removal request here, whose plain `sudo rpm` commands are not
wrapped in `sh -c` and so apply cleanly even here.

::: dnf_key
    options:
      members: []

## Enabling a module stream with idempotence

The application-stream operation: `dnf.module` enables a module stream
(`dnf module enable -y <module>:<stream>`) or, with `enabled=False`, disables a
module outright (`dnf module disable -y <module>`). Enabling compares against the
[`DnfEnabledModules`][reemote.modules.dnf.dnf.DnfEnabledModules] fact — a
list of name-and-stream entries — so it is a no-op when the module is already
enabled on exactly the requested stream, and re-enables onto the new stream when
it currently sits on a different one; disabling compares against
[`DnfDisabledModules`][reemote.modules.dnf.dnf.DnfDisabledModules]. `stream` is
required when `enabled=True` (a missing one raises `ValueError`, mirroring
the baseline's `OperationValueError`). Enabling or disabling writes dnf's module
state, so the request is flagged `sudo=True`. The script yields the same enable
request twice — the second is a guaranteed no-op in the apply phase, because the
first enable is visible to it through the fresh per-step fact cache (in the
prepare phase, where nothing is sent, both plan as changes). The baseline's
stream-switching caveat is inherited unchanged: `dnf module enable` refuses to
switch streams while packages from the active stream are installed, so remove
them (or `dnf module reset`) first.

::: dnf_module
    options:
      members: []
