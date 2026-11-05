# Module examples: files

Scripts under `examples/modules/files/` — one per fact or operation of the
[`files`](../../modules/files.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`files_file.py`, reading `File` for
`/etc/hosts` over `gather_fact`, `files_directory.py`, reading `Directory`
for `/etc` the same way, and `files_link.py`, reading `Link` for
`/etc/localtime` — the timezone symlink, whose `link_target` names the
zoneinfo file — the same way) live alongside the operation examples
in `examples/modules/files/`. They are not rendered here — each reads a
single fact, and the facts themselves are documented on the
[`files` module page](../../modules/files.md).

## Creating a symbolic link

`files.link` reads the link's current target through the `FileLink` fact
(`readlink`) and yields a command only when it differs — `ln -sf` creates or
retargets the link, `present=False` removes it with `rm -f`. The example
ensures the same link twice under the login user's home (no elevation
needed) — the second request is a guaranteed no-op because the first
request's `ln -sf` is visible to it through the fresh per-step fact cache.
The readlink-based check only sees symlinks; removing a regular file needs
`files.file` below.

::: files_op_link
    options:
      members: []

## Ensuring a regular file

`files.file` reads existence through the boolean `FileExists` fact
(`test -e`) and yields a command only on a difference — `touch` creates a
missing file, `present=False` removes a present one with `rm -f`. Unlike
`files.link`'s readlink-based check this probe also sees regular files,
which makes it the removal primitive for them. The example touches the same
marker file twice under the login user's home — the second request is a
guaranteed no-op through the fresh per-step fact cache; a root-owned path
would carry `sudo=True`.

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
