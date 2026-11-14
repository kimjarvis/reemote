# Module examples: files

Scripts under `examples/modules/files/` — one per fact or operation of the
[`files`](../../modules/files.md) module, plus the cross-module
`dnf_put_get` health-check scenario. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`files_file.py`, reading `File` for
`/etc/hosts` over `gather_fact`, `files_directory.py`, reading `Directory`
for `/etc` the same way, `files_link.py`, reading `Link` for
`/etc/localtime` — the timezone symlink, whose `link_target` names the
zoneinfo file — the same way, `files_socket.py`, reading `Socket` for
`/var/run/docker.sock` — the daemon-socket ownership/mode audit — the same
way, and `files_same_file.py`, reading `SameFile`
between `/etc/localtime` and that target (the resolved-inode comparison
behind `files.link`'s hard-link idempotence) plus an unrelated pair) live
alongside the operation examples
in `examples/modules/files/`. The hash family is read the same way:
`files_sha256.py`, `files_sha384.py` and `files_md5.py` each digest
`/etc/hostname` with their fact (`Sha256File`, `Sha384File`, `Md5File`).
The enumeration facts close the directory-listing gap: `files_find_files.py`
reads `FindFiles` for the `conf.yaml` files under the agent's `conf.d`
(the DataDog-role cleanup question), `files_find_directories.py` reads
`FindDirectories` for its immediate check subdirectories, `files_find_links.py`
reads `FindLinks` for the symlinks under `/etc`, `files_find_in_file.py`
reads `FindInFile` for the lines of `/etc/yum.conf` matching `gpgcheck`,
`files_find_in_files.py` reads the original `FindInFiles` for the paths of
every `.repo` file under `/etc/yum.repos.d` whose content still names the
retired `mirrorlist.centos.org` endpoint (the "delete unmanaged files
matching content" cleanup read),
`files_find_files_info.py` reads the original `FindFilesInfo` for
per-hit size/mode/owner/mtime metadata under `/var/log`, and
`files_directory_entries.py` reads the original `DirectoryEntries` for the
shallow name/type listing of `/etc/yum.repos.d` (the CentOS mirror of the
legacy apt `.list` cleanup question). They are not rendered here — each reads a
single fact, and the facts themselves are documented on the
[`files` module page](../../modules/files.md).

## Creating a symbolic link

`files.link` stats the path through the `Link` fact and yields a command
only when it differs — `ln -s` (plain `ln` for a hard link,
`symbolic=False`) creates a missing link, an existing link to the wrong
target is removed and recreated (`rm -f` then `ln`, never `ln -sf`),
`user`/`group` are edited with `chown -h` (the link itself, never its
target), and `present=False` removes it with `rm -f`. A missing parent
directory is created first unless `create_remote_dir=False`, and a path
held by a regular file or directory raises unless `force=True` removes it
first — the baseline's timestamped `force_backup` is deliberately dropped (a
`datetime.now()` in the command would differ between the phases). The
example ensures the same link twice in `/tmp` — writable by any login user,
so no elevation is needed — and the second request is a guaranteed no-op
because the first request's `ln -s` is visible to it through the fresh
per-step fact cache. The link checks only see links; removing a regular file
needs `files.file` below.

::: files_op_link
    options:
      members: []

## Ensuring a regular file

`files.file` (baseline parity, in the `files.directory` shape): it stats the
path through the `File` fact and yields only the commands that close a gap
— `touch` creates a missing file, plus `chmod`/`chown` when
`mode`/`user`/`group` are requested and a `mkdir -p` of a *missing* parent
first (`create_remote_dir`, on by default; an existing parent is left
alone); an existing file re-runs only the `chmod`/`chown` whose values
differ from the fact's metadata; and `present=False` removes it with
`rm -f`. Unlike `files.link`, which manages links only, this also sees
regular files, which makes it the removal primitive for them. The example
first provisions the `www` group and system user with `server.group` /
`server.user` (no-ops once the account exists) and keeps the `/srv/web` /
`/srv/web/conf.d` chain `755` `www:www` with `files.directory` — the
`File` fact reads unprivileged, so a non-traversable parent would hide
the file — then ensures the same
root-owned file twice — the second file request is a guaranteed no-op
through the fresh per-step fact cache — so those requests carry
`sudo=True`, then removes a `/tmp` marker unprivileged.
the baseline's timestamped `force_backup` is deliberately dropped (a
`datetime.now()` in the command would differ between the phases), and the
`touch` option is shown unused because it is deliberately non-idempotent.

::: files_op_file
    options:
      members: []

## Uploading a stream with sha1 idempotence

The first non-shell operation: `files.put` compares the sha1 of an IO-like
source on the control machine against the remote file's and transfers over
the asyncssh connection's scp channel only when they differ. Unlike the baseline's
`str | IO[Any]`, the source is a *stream only* — the example uses a
module-level `io.BytesIO`, defined once so both phases pass the identical
object (request equality compares it by identity). The script puts the same
stream twice — the second put is a guaranteed no-op because the first put's
upload is visible to it through the fresh per-step fact cache. The transfer
rides the connection as the login user, so the destination is a path in
`/tmp`, writable by any login user (no `sudo`/`su` applies to transfers).

::: files_put
    options:
      members: []

## Downloading into a stream with sha1 idempotence

The mirror operation: `files.get` compares the remote file's sha1 against the
sha1 of an IO-like destination's current content and downloads over the
asyncssh connection's scp channel only when they differ. Where the baseline writes
to a local path (with `add_deploy_dir`/`create_local_dir`/`force`), reemote's
destination is a *stream only* — the example reads `/etc/hostname` into a
module-level `io.BytesIO` and prints the bytes back, so no local file is
involved. Because a get *writes* its destination, each host keeps its own sink
(a per-host `io.BytesIO` in a module-level dict, reused across phases for
identity) so two hosts never clobber each other in lockstep. The script gets
the same file twice — the second get is a guaranteed no-op because the first
get's download is visible to it through the fresh per-step fact cache.

::: files_get
    options:
      members: []

## Editing a file in place with a pure transform

The generic content-editing primitive: `files.edit` reads a file's current
text with the
`FileContents` fact, applies a caller-supplied *pure* transform, and writes the
result back over the asyncssh connection's scp channel only when it differs. Any
"edit a file in place" task is then just a transform — though a few common
formats now have dedicated operations built on the same idea
(`server.etc_hosts` for the hosts file, `sysctl.setting` for sysctl keys). The example's
`ensure_marker` appends a managed line only when it is not already present, so
it is a fixpoint and the second edit is a guaranteed no-op — visible through the
fresh per-step fact cache. The transform must be a module-level function (a
lambda built inside the deploy generator would diverge across phases and raise
`DeterminismError`), and because the write is an unprivileged transfer the
target is a login-user-writable path in `/tmp`, not a root-owned file like
`/etc/hosts` — for that, see `files.transform` or `files.install` below (or the
dedicated
[`server.etc_hosts`](server.md#managing-etchosts-entries) operation).

::: files_edit
    options:
      members: []

## Managing a marked block (baseline files.block parity)

reemote's answer to the baseline's `files.block`: no bespoke block editor, just a
transform. The example's `block_transform` factory builds a pure, idempotent
`files.edit` transform from the baseline's option set — `content` (a string or a
list of lines) surrounded by the `marker` lines (`# BEGIN/END REEMOTE BLOCK`
by default, kept verbatim so existing baseline-managed blocks are recognized),
`line`/`before`/`after` regex positioning with `escape_regex_characters`, and
`present=False` to remove the marked region. An existing block is replaced in
place or returned byte-identical (the fixpoint that makes the second edit a
no-op); the factory must be called once outside the deploy generator, or the
fresh closure per phase would raise `DeterminismError`. The baseline's `backup`
(a non-deterministic timestamped `cp`), `preserve_meta` (moot under an scp
overwrite) and the deprecated `try_prevent_shell_expansion` are deliberately
dropped; removal from an absent file passes `None` through so no empty file
is created. The script runs the full lifecycle — ensure twice, then remove
twice — each run as a `--dry` plan followed by the real apply log, against a
login-user-writable `/tmp` path (a root-owned target needs `files.install`
below).

::: files_block
    options:
      members: []

## Moving a remote file into a remote directory

`files.move` (baseline parity): `src` is a remote path, `dest` the remote
*directory* to move it into, and `overwrite=True` clears an existing target
with `rm -rf` before the `mv`. The operation is stateless — a valid move
always plans a command — and raises `ValueError` on an absent source or
absent/non-directory destination in *both* phases, so a "create then move"
flow cannot share one deploy: the example stages the payload with
`files.put` into `/tmp` in run 1, then moves it into `/var/tmp` in run 2,
each run shown as a `--dry` plan followed by the real apply log. Both
directories exist on every host and are writable by any login user, so no
home directory is assumed. Nothing is elevated here; a root-owned
destination would carry `sudo=True`.

::: files_move
    options:
      members: []

## Copying a remote file into a remote directory

`files.copy` (baseline parity): `src` is a remote path, `dest` the remote
*directory* to copy it into, and the source stays in place — the difference
from `files.move`. `overwrite=True` forces the copy (`cp -r -f`, no `rm -rf`
prelude); without it an existing target with equal sha1 content is a no-op
and a different one raises `ValueError`. Absent source / absent or
non-directory destination raise in *both* phases (the same phase-unaware
strictness as `files.move`), so the example stages the payload with
`files.put` into `/tmp` in run 1, then copies it into `/var/tmp` in run 2,
each run shown as a `--dry` plan followed by the real apply log. Both
directories exist on every host and are writable by any login user, so no
home directory is assumed. Nothing is elevated here; a root-owned
destination would carry `sudo=True`.

::: files_copy
    options:
      members: []

## Creating and managing a directory

`files.directory` (baseline parity): an absent path is created with
`mkdir -p`, then `chmod`/`chown` when `mode`/`user`/`group` are requested;
an existing directory re-runs only the `chmod`/`chown` whose values differ
from the `Directory` fact's metadata, so the second request in the example
is a guaranteed no-op; `present=False` removes it with `rm -rf`, and a path
held by a regular file raises unless `force=True` removes it first.
the baseline's timestamped `force_backup` is deliberately dropped — a
`datetime.now()` in the command would differ between the phases. The
web-root path (`/srv/web`) and the `www` ownership are root-owned, so the
request carries `sudo=True`. The `www` account is not assumed to exist:
the deploy's first two requests ensure the group and system user with
`server.group` / `server.user`, so the `chown` never fails on an unknown
user. The mode is `755`, not `750`: facts read unprivileged, so the web
root must stay traversable by the login user (the `files.file` sibling
example's `File` fact reads a config file inside it).

::: files_op_directory
    options:
      members: []

## Setting and clearing ext2 attribute flags

`files.flags` (baseline parity, in the developer-chosen Linux adaptation):
the baseline's BSD `chflags`/`stat -f %Sf` verb pair does not exist on Linux, so
the same set/clear capability runs over `lsattr -d` (read through the
`FileFlags` fact) and `chattr +flag`/`-flag`. The idempotence is verbatim
the baseline's — a set yields only the requested letters missing from the current
set, a clear (`present=False`) only those actually present, and anything
else is a no-op. `flags` takes one letter or a tuple of letters (a tuple at
`op()` call sites, since request arguments must stay hashable); a letter
outside the ext2 vocabulary raises `ValueError` before anything reaches the
host. `chattr` is root's tool (`CAP_LINUX_IMMUTABLE`), so the requests carry
`sudo=True` even though the marker itself is a login-user-writable `/tmp`
path: the script makes a file immutable, then the second run's clear is
planned against the freshly set state through the per-step fact cache.

::: files_flags
    options:
      members: []

## Converging to a fixed file state (literal no-op on rerun)

The counterpart to the self-reversing script above: a plain *converge-to-state*
deploy that declares a desired end state and never undoes it, so a second run
of the same script reports no change on every step — the literal idempotence
guarantee. It exercises two converge dimensions at once: `files.file` with a
fixed `mode` (`600`, deliberately different from a bare `touch`'s default `644`
so the first run really issues a `chmod`, and the rerun's equality check
closes), and `files.flags` with `present=True` and **no** restore step (a
rerun reads `i` already set and sends nothing). Both close against the fresh
per-step fact cache in the apply phase. Unlike the self-reversing sibling, a
real run leaves the marker present, mode `600` and immutable — that residue is
the point — so unwind it by clearing the flag first (`chattr -i`) before any
removal. The `chattr` carries `sudo=True` (`CAP_LINUX_IMMUTABLE`); the create
and `chmod` are unprivileged in `/tmp`.

::: files_flags_converge
    options:
      members: []

## Installing content at a root-owned path

`files.install` (a reemote original) is the one-deploy answer to a
root-owned content write that a `files.edit` + `files.move` recipe would
need two runs for: it reads the
destination's text with `FileContents` and, only when it differs from the
desired `content`, yields an unprivileged `InstallSpoolCommand` scp transfer
to a dest-derived `/tmp` spool path followed by an elevated `InstallCommand`
(`mkdir -p`/`cp`/optional `chown`/`chmod`/`rm -rf` wrapped in `sh -c`) — both
from one request, so prepare records them and apply runs them in order.
`content` is a plain `str`, hashable and equal across phases, so no
identity-stable stream is needed; the spool name is the destination's sha1
prefix, so two installs never clobber one spool file. The optional
`user`/`group` parameters add a `chown` step inside the elevated chain (before
the `chmod`), so a service config lands with its service-account owner in the
same request that writes its content — no companion `files.file` needed. The
optional `validate` parameter injects an `InstallValidateCommand` *between* the
spool transfer and the install: a hashable command template (`{spool}`/`{dest}`
placeholders substituted with shell-quoted paths) run against the just-spooled
`/tmp` file, so a non-zero exit raises before the elevated install runs and a
bad config never reaches the protected path — the atomic validate-then-install
hook Ansible's `template`/`copy` `validate:` provides. The example validates
the staged bytes with `grep -q '^managed = true' {spool}` (the real-world shape
is `nginx -t -c {spool}`, which inherits the request's `sudo` so the validator
can read every file the config references). The
example first provisions the `www` group and system user (the account the
sibling `files_op_file.py` uses), then installs a plain demo config and an
owned `www:www` variant under `/etc` with `sudo=True` (the transfer stays
unprivileged regardless), and runs the same deploy again to show every
request reporting no change. The owned variant uses the login-readable
`0644` mode; a restrictive `0640` service-account mode stays idempotent too —
`FileContents` re-reads a file the login user cannot read elevated with `sudo`.

::: files_install
    options:
      members: []

## Editing a root-owned file with a pure transform

`files.transform` (a reemote original) is `files.edit`'s pure-transform contract
with the write mechanism `files.edit` lacks: it reads the target's text with
`FileContents`, applies a caller-supplied *pure* transform, and — only when the
result differs — delegates the write to `files.install`, spooling the
transformed text over unprivileged scp to a dest-derived `/tmp` path and
overlaying the target with an elevated `sh -c`. Because the put/edit transfers
never elevate, `files.edit` cannot converge a root-owned config such as
`/etc/httpd/conf/httpd.conf` in one deploy; a single `sudo=True`
`files.transform` request both preserves the rest of the protected file and
lands the edit — the generic form of what `server.etc_hosts` hard-wires to
`/etc/hosts`. The example's `ensure_listen_directive` rewrites any `Listen`
line to the wanted port (appending one when the file has none), preserving
every other line, comment and blank line, so it is a fixpoint and the second
transform is a guaranteed no-op — visible through the fresh per-step fact
cache. As for `files.edit`, the transform must be a module-level function (a
lambda built inside the deploy generator would diverge across phases and raise
`DeterminismError`); `mode`/`user`/`group`/`validate` are forwarded to the install, and
idempotence stays content-only. See
[Migrating a protected-file line edit](../../migrating.md#migrating-a-protected-file-line-edit-lineinfile)
for how to write a transform. The script transforms a demo config under `/etc`
with `sudo=True` (the spool transfer stays unprivileged regardless), then runs
the same deploy again to show every request reporting no change.

::: files_transform
    options:
      members: []

## Downloading a URL to a root-owned path

`files.download` (a reemote original) is the general download-to-path operation
every package-repository role needs: it takes an `http(s)` `url` and lands its
body at a root-owned `dest` in one `sudo=True` request, retiring the hand-rolled
`urllib.request` + `io.BytesIO` workaround that `dnf.key`/`apt.key`/`gpg.key`
force (each *rejects* a URL `src`). The fetch is *control-side* — the control
machine opens the connection (`urllib` in a worker thread), not the target host,
so the download works when only the control side has internet egress and the host
never needs `curl`/`wget`. The body decodes the fetched bytes to text and
delegates the write to `files.install`, so a download inherits install's
content-only idempotence, its dest-derived `/tmp` spool, its owner/mode handling,
its optional `validate` hook and its two-privilege-level signature (unprivileged
scp transfer + elevated `sh -c` install). The optional `checksum` is Ansible's
`get_url` spelling (`"sha256:<hexdigest>"`), verified before anything is written
and what makes the two-phase fetch deterministic — the body runs once per phase,
so the URL is fetched twice per host, and both phases install byte-identical
content. Text only: a binary body (a `.rpm`, a tarball) raises `ValueError` and
stays `files.put`'s job. The example pulls the two Docker CE artifacts the role
hits the gap twice for — the `docker-ce.repo` stanza to
`/etc/yum.repos.d/docker-ce.repo` and the ASCII-armored GPG key to
`/etc/pki/rpm-gpg/RPM-GPG-KEY-DOCKER` (the file the downloaded `.repo`'s
`gpgkey=` line points dnf at) —
with `sudo=True`, and runs the same deploy again to show every request reporting
no change. It omits a hardcoded checksum so it stays runnable against the live
Docker URLs; add `checksum="sha256:<hex>"` in production from the vendor's
published value. See
[Migrating a URL download](../../migrating.md#migrating-a-url-download-get_url)
for the `get_url`/`rpm_key` mapping.

::: files_download
    options:
      members: []

## A health-check deploy: dnf install, put and get

The reemote take on a relatable the baseline scenario — a simple web-server
health-check / log-rotation deploy — in one flat generator: `dnf.packages`
installs `htop` (elevated with `sudo=True`), `files.put` pushes an
in-memory motd stream to `/tmp/remote_motd` (unprivileged scp), and
`files.get` pulls `/etc/os-release` back into a per-host `io.BytesIO` sink
whose content the script prints. Where the baseline original uses local paths
(`files/motd`, `local_os_release.txt`, `add_deploy_dir=True`), reemote's
transfers take IO-like objects only — no local file is involved in either
direction.

::: dnf_put_get
    options:
      members: []
