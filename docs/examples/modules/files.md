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
zoneinfo file — the same way, and `files_same_file.py`, reading `SameFile`
between `/etc/localtime` and that target (the resolved-inode comparison
behind `files.link`'s hard-link idempotence) plus an unrelated pair) live
alongside the operation examples
in `examples/modules/files/`. They are not rendered here — each reads a
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
first — pyinfra's timestamped `force_backup` is deliberately dropped (a
`datetime.now()` in the command would differ between the phases). The
example ensures the same link twice under the login user's home (no
elevation needed) — the second request is a guaranteed no-op because the
first request's `ln -s` is visible to it through the fresh per-step fact
cache. The link checks only see links; removing a regular file needs
`files.file` below.

::: files_op_link
    options:
      members: []

## Ensuring a regular file

`files.file` (pyinfra parity, in the `files.directory` shape): it stats the
path through the `File` fact and yields only the commands that close a gap
— `touch` creates a missing file, plus `chmod`/`chown` when
`mode`/`user`/`group` are requested and a `mkdir -p` of a *missing* parent
first (`create_remote_dir`, on by default; an existing parent is left
alone); an existing file re-runs only the `chmod`/`chown` whose values
differ from the fact's metadata; and `present=False` removes it with
`rm -f`. Unlike `files.link`, which manages links only, this also sees
regular files, which makes it the removal primitive for them. The example
ensures the same root-owned file twice — the second request is a guaranteed no-op
through the fresh per-step fact cache — so those requests carry
`sudo=True`, then removes a marker in the login user's home unprivileged.
pyinfra's timestamped `force_backup` is deliberately dropped (a
`datetime.now()` in the command would differ between the phases), and the
`touch` option is shown unused because it is deliberately non-idempotent.

::: files_op_file
    options:
      members: []

## Uploading a stream with sha1 idempotence

The first non-shell operation: `files.put` compares the sha1 of an IO-like
source on the control machine against the remote file's and transfers over
the asyncssh connection's scp channel only when they differ. Unlike pyinfra's
`str | IO[Any]`, the source is a *stream only* — the example uses a
module-level `io.BytesIO`, defined once so both phases pass the identical
object (request equality compares it by identity). The script puts the same
stream twice — the second put is a guaranteed no-op because the first put's
upload is visible to it through the fresh per-step fact cache. The transfer
rides the connection as the login user, so the destination is a path under
that user's home (no `sudo`/`su` applies to transfers).

::: files_put
    options:
      members: []

## Downloading into a stream with sha1 idempotence

The mirror operation: `files.get` compares the remote file's sha1 against the
sha1 of an IO-like destination's current content and downloads over the
asyncssh connection's scp channel only when they differ. Where pyinfra writes
to a local path (with `add_deploy_dir`/`create_local_dir`/`force`), reemote5's
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

The generic content-editing primitive, and the reason reemote5 ships no bespoke
`server.etc_hosts`: `files.edit` reads a file's current text with the
`FileContents` fact, applies a caller-supplied *pure* transform, and writes the
result back over the asyncssh connection's scp channel only when it differs. Any
"edit a file in place" task is then just a transform. The example's
`ensure_marker` appends a managed line only when it is not already present, so
it is a fixpoint and the second edit is a guaranteed no-op — visible through the
fresh per-step fact cache. The transform must be a module-level function (a
lambda built inside the deploy generator would diverge across phases and raise
`DeterminismError`), and because the write is an unprivileged transfer the
target is a path under the login user's home, not a root-owned file like
`/etc/hosts` — for that, see the spool-and-install recipe below.

::: files_edit
    options:
      members: []

## Managing a marked block (pyinfra files.block parity)

reemote5's answer to pyinfra's `files.block`: no bespoke block editor, just a
transform. The example's `block_transform` factory builds a pure, idempotent
`files.edit` transform from pyinfra's option set — `content` (a string or a
list of lines) surrounded by the `marker` lines (`# BEGIN/END PYINFRA BLOCK`
by default, kept verbatim so existing pyinfra-managed blocks are recognized),
`line`/`before`/`after` regex positioning with `escape_regex_characters`, and
`present=False` to remove the marked region. An existing block is replaced in
place or returned byte-identical (the fixpoint that makes the second edit a
no-op); the factory must be called once outside the deploy generator, or the
fresh closure per phase would raise `DeterminismError`. pyinfra's `backup`
(a non-deterministic timestamped `cp`), `preserve_meta` (moot under an scp
overwrite) and the deprecated `try_prevent_shell_expansion` are deliberately
dropped; removal from an absent file passes `None` through so no empty file
is created. The script runs the full lifecycle — ensure twice, then remove
twice — each run as a `--dry` plan followed by the real apply log, against a
login-user-writable path (a root-owned target needs the spool-and-install
recipe below).

::: files_block
    options:
      members: []

## Moving a remote file into a remote directory

`files.move` (pyinfra parity): `src` is a remote path, `dest` the remote
*directory* to move it into, and `overwrite=True` clears an existing target
with `rm -rf` before the `mv`. The operation is stateless — a valid move
always plans a command — and raises `ValueError` on an absent source or
absent/non-directory destination in *both* phases, so a "create then move"
flow cannot share one deploy: the example stages the payload with
`files.put` into `/tmp` in run 1, then moves it into the login user's home
in run 2, each run shown as a `--dry` plan followed by the real apply log.
Nothing is elevated here; a root-owned destination would carry `sudo=True`.

::: files_move
    options:
      members: []

## Copying a remote file into a remote directory

`files.copy` (pyinfra parity): `src` is a remote path, `dest` the remote
*directory* to copy it into, and the source stays in place — the difference
from `files.move`. `overwrite=True` forces the copy (`cp -r -f`, no `rm -rf`
prelude); without it an existing target with equal sha1 content is a no-op
and a different one raises `ValueError`. Absent source / absent or
non-directory destination raise in *both* phases (the same phase-unaware
strictness as `files.move`), so the example stages the payload with
`files.put` into `/tmp` in run 1, then copies it into the login user's home
in run 2, each run shown as a `--dry` plan followed by the real apply log.
Nothing is elevated here; a root-owned destination would carry `sudo=True`.

::: files_copy
    options:
      members: []

## Creating and managing a directory

`files.directory` (pyinfra parity): an absent path is created with
`mkdir -p`, then `chmod`/`chown` when `mode`/`user`/`group` are requested;
an existing directory re-runs only the `chmod`/`chown` whose values differ
from the `Directory` fact's metadata, so the second request in the example
is a guaranteed no-op; `present=False` removes it with `rm -rf`, and a path
held by a regular file raises unless `force=True` removes it first.
pyinfra's timestamped `force_backup` is deliberately dropped — a
`datetime.now()` in the command would differ between the phases. The
web-root path (`/srv/web`) and the `www` ownership are root-owned, so the
request carries `sudo=True`.

::: files_op_directory
    options:
      members: []

## Setting and clearing ext2 attribute flags

`files.flags` (pyinfra parity, in the developer-chosen Linux adaptation):
pyinfra's BSD `chflags`/`stat -f %Sf` verb pair does not exist on Linux, so
the same set/clear capability runs over `lsattr -d` (read through the
`FileFlags` fact) and `chattr +flag`/`-flag`. The idempotence is verbatim
pyinfra's — a set yields only the requested letters missing from the current
set, a clear (`present=False`) only those actually present, and anything
else is a no-op. `flags` takes one letter or a tuple of letters (a tuple at
`op()` call sites, since request arguments must stay hashable); a letter
outside the ext2 vocabulary raises `ValueError` before anything reaches the
host. `chattr` is root's tool and the example's protected `/etc` path is
root-owned, so the requests carry `sudo=True`: the script makes a file
immutable, then the second run's clear is planned against the freshly set
state through the per-step fact cache.

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
and `chmod` are unprivileged in the login user's home.

::: files_flags_converge
    options:
      members: []

## Managing /etc/hosts with files.edit + files.move

reemote5's answer to pyinfra's bespoke `server.etc_hosts`: no per-format
editor, just the generic primitives composed. A `gather_fact` snapshot reads
each host's real `/etc/hosts`; the pure `ensure_db_host` transform computes
the desired content on the control machine (the line-keyed-by-IP edit
pyinfra performs), and hosts already in the desired state are skipped — the
script-level idempotence pyinfra gets from its `EtcHosts` fact. Run 1 spools
the content to `/tmp/hosts` with `files.edit` (a per-host transform built
once by a closure factory over the frozen snapshot, determinism-safe across
phases); run 2 installs it with `files.move` flagged `sudo=True` —
`sudo rm -rf /etc/hosts` then `sudo mv /tmp/hosts /etc`. Two runs because
`files.edit`'s write is an unprivileged transfer that cannot touch
root-owned `/etc/hosts`, and `files.move` validates its source in the
prepare phase, which a single deploy's not-yet-created spool file would
fail.

::: files_edit_etc_hosts
    options:
      members: []

## Installing content at a root-owned path

`files.install` (a reemote5 original) is the one-deploy answer to the
root-owned write the recipe above needs two runs for: it reads the
destination's text with `FileContents` and, only when it differs from the
desired `content`, yields an unprivileged `InstallSpoolCommand` scp transfer
to a dest-derived `/tmp` spool path followed by an elevated `InstallCommand`
(`mkdir -p`/`cp`/`chmod`/`rm -f` wrapped in `sh -c`) — both from one request,
so prepare records them and apply runs them in order. `content` is a plain
`str`, hashable and equal across phases, so no identity-stable stream is
needed; the spool name is the destination's sha1 prefix, so two installs
never clobber one spool file. The example installs a demo config under
`/etc` with `sudo=True` (the transfer stays unprivileged regardless), then
runs the same deploy again to show every request reporting no change.

::: files_install
    options:
      members: []

## A health-check deploy: dnf install, put and get

The reemote5 take on a relatable pyinfra scenario — a simple web-server
health-check / log-rotation deploy — in one flat generator: `dnf.packages`
installs `htop` (elevated with `sudo=True`), `files.put` pushes an
in-memory motd stream to `/tmp/remote_motd` (unprivileged scp), and
`files.get` pulls `/etc/os-release` back into a per-host `io.BytesIO` sink
whose content the script prints. Where the pyinfra original uses local paths
(`files/motd`, `local_os_release.txt`, `add_deploy_dir=True`), reemote5's
transfers take IO-like objects only — no local file is involved in either
direction.

::: dnf_put_get
    options:
      members: []
