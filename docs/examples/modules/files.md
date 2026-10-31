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
`/etc/hosts`.

::: files_edit
    options:
      members: []
