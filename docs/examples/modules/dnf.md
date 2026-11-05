# Module examples: dnf

Scripts under `examples/modules/dnf/` — one per fact or operation of the
[`dnf`](../../modules/dnf.md) module. See the [Examples overview](../../examples.md)
for the shared "connects for real" warning and the error-reporting contract
every script follows.

The read-only fact-gathering examples (`DnfRepositories`, `DnfRepoList`,
`DnfEnabledModules`, `DnfDisabledModules`, `DnfInstalledModules`, `DnfGroups`
and `DnfAvailableGroups` over `gather_fact`) live alongside the operation
examples in `examples/modules/dnf/`. They are not rendered here — each reads a
single fact, and the facts themselves are documented on the
[`dnf` module page](../../modules/dnf.md).

## The base example

The starting point for everything below: a single flat deploy generator
installing httpd and removing the welcome configuration the package drops,
run end to end over SSH in both phases.

::: dnf_packages
    options:
      members: []

## Updating all packages

The whole-system update: `dnf.update` runs `dnf update -y` over every installed
package. Like pyinfra's `dnf.update` it is **non-idempotent** — it reads no fact
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
what the [`DnfRepoFile`][reemote5.modules.dnf.dnf.DnfRepoFile] fact reads back
— so re-running is a no-op. Where pyinfra's `dnf.repo` can also *download* a
remote `.repo` file, reemote5 has no download operation, so a URL `src` raises
`ValueError` and the file is always constructed locally from the arguments.
The script yields the same repository request twice — the second is a
guaranteed no-op in the apply phase, because the first write is visible to it
through the fresh per-step fact cache (in the prepare phase, where nothing is
sent, both plan as changes). The write rides under `sudo` (`/etc/yum.repos.d`
is root-owned) as a two-command pair: an unprivileged scp transfer spools the
generated text to `/tmp`, then one elevated `sh -c` creates the directory,
copies the file, chmods it `0644` and removes the spool — the `apt.sources_file`
pattern.

::: dnf_repo
    options:
      members: []

## Installing a `.rpm` file with version idempotence

The package-file operation: `dnf.rpm` installs or removes a `.rpm` that already
exists on the *target host*. It reads the
[`RpmPackage`][reemote5.modules.dnf.dnf.RpmPackage] fact twice — once for the
file's `name`/`version`, once for the installed package of that name — and
counts the package as present only when the installed version matches the
file's, so `present=True` installs with `rpm -i` only when it does not and is
otherwise a no-op, while `present=False` removes it with `dnf remove -y <name>`.
Where pyinfra's `dnf.rpm` can also *download* a remote `.rpm` mid-deploy,
reemote5 has no download operation, so a URL `src` raises `ValueError`; upload
the file from the controller with `files.put` first. The install rides under
`sudo` (installing a package needs root), and the script yields the same request
twice — the second is a guaranteed no-op in the apply phase, because the first
install is visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes).

::: dnf_rpm
    options:
      members: []

## Importing a GPG key from a stream

The non-idempotent key operation: `dnf.key` imports a GPG key into the target
host's rpm keyring. Like pyinfra's `dnf.key` it reads no fact and always
reports a change (rpm itself deduplicates an already-imported key). Where
pyinfra's `src` is a *filename or URL* the target host reads directly
(`rpm --import <src>`), reemote5 has no controller-side file locations, so the
source is a *stream only* — the example uses a module-level `io.BytesIO`,
defined once so both phases pass the identical object (request equality
compares it by identity), and a `str` `src` raises `ValueError`. The one
request yields *two* commands at different privilege levels: a transfer that
spools the stream to a `/tmp` path over scp as the login user (transfers are
never elevated), then an elevated `rpm --import` of that spool file — wrapped
in `sh -c` so the import *and* its cleanup `rm` both run as root — hence the
request is flagged `sudo=True`.

::: dnf_key
    options:
      members: []

## Enabling a module stream with idempotence

The application-stream operation: `dnf.module` enables a module stream
(`dnf module enable -y <module>:<stream>`) or, with `enabled=False`, disables a
module outright (`dnf module disable -y <module>`). Enabling compares against the
[`DnfEnabledModules`][reemote5.modules.dnf.dnf.DnfEnabledModules] fact — a
list of name-and-stream entries — so it is a no-op when the module is already
enabled on exactly the requested stream, and re-enables onto the new stream when
it currently sits on a different one; disabling compares against
[`DnfDisabledModules`][reemote5.modules.dnf.dnf.DnfDisabledModules]. `stream` is
required when `enabled=True` (a missing one raises `ValueError`, mirroring
pyinfra's `OperationValueError`). Enabling or disabling writes dnf's module
state, so the request is flagged `sudo=True`. The script yields the same enable
request twice — the second is a guaranteed no-op in the apply phase, because the
first enable is visible to it through the fresh per-step fact cache (in the
prepare phase, where nothing is sent, both plan as changes). pyinfra's
stream-switching caveat is inherited unchanged: `dnf module enable` refuses to
switch streams while packages from the active stream are installed, so remove
them (or `dnf module reset`) first.

::: dnf_module
    options:
      members: []
