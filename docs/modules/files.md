# files module

## file Facts

::: reemote5.modules.files.files
    options:
      show_root_heading: false
      members:
        - File
        - Directory
        - Link
        - FileLink
        - FileExists
        - LocalSha1
        - RemoteFileSha1
        - FileContents

## file Operations

::: reemote5.modules.files.files
    options:
      show_root_heading: false
      members:
        - link
        - file
        - put
        - get
        - edit
        - move



<!---

`reemote5.modules.files.files` combines the file facts and the file operations
in a single source, so the read side (`FileLink`, `FileExists`,
`LocalSha1`, `RemoteFileSha1`, `FileContents`, `File`, `Directory`, `Link`)
sits beside the `files.link`, `files.file`, `files.put`, `files.get`,
`files.edit` and `files.move` operations that consume it.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `LocalSha1` is the
one fact whose input is not the host: it hashes an IO-like stream on the
*control* machine — the source `files.put` uploads or the destination
`files.get` downloads into — which both compare against the remote file's hash.

`File` mirrors pyinfra's `files.File`: it guards the path with
`test -e`/`test -L`, then describes it with GNU `stat -c`, falling back to BSD
`stat -f` and finally `ls -ld`, and returns the metadata dict — `user`,
`group`, `mode` (the octal-looking int pyinfra returns, e.g. `644` or `4755`,
setuid/setgid/sticky bits included), `size`, and `atime`/`mtime`/`ctime` as
*naive UTC* `datetime`s (pyinfra's convention; the `ls -ld` fallback provides
only an mtime, so its atime/ctime read as `None`). A path that does not exist
reads as `None`; one that exists but is not a regular file (a directory,
symlink, socket, ...) reads as `False` — `Directory` and `Link` are the
directory- and link-accepting siblings, and only `Link`'s dict carries the
`link_target` key. Like
pyinfra, a leading `~/` stays unquoted so the remote shell expands it. Where
`FileExists` is the cheap existence probe `files.file` consumes, `File` is
the richer stat-level read for auditing ownership, permissions or timestamps.

`Directory` mirrors pyinfra's `files.Directory` — its `File` subclass with
`type = "directory"`: the very same guard, stat/ls chain and metadata dict,
but the path's type flag must be `d`, so a regular file, symlink, socket, ...
reads as `False` instead. Use it to audit a directory's ownership, mode
(setuid/setgid/sticky bits included — `/tmp`'s `1777`, a setgid share's
`2775`) or timestamps before placing files inside it.

`Link` mirrors pyinfra's `files.Link` — the same `File` subclass with
`type = "link"`: the very same guard, stat/ls chain and metadata dict, but
the path's type flag must be `l`, and the dict additionally carries
`link_target` — the target path with the stat/ls quoting stripped (GNU
`'name'`, older GNU/RHEL 6 `` `name' ``, uutils `"name"`), exactly as the
link records it, so a relative target stays relative. The `test -L` guard
lets a *dangling* symlink through to the `ls -ld` fallback, so a broken
link is still reported with its target. Where `FileLink` is the cheap
`readlink` probe `files.link` consumes, `Link` is the richer stat-level
read for auditing the link itself — ownership, mode, timestamps —
alongside where it points.


## Operations

Operations live in namespace modules so a deploy reads `files.link`,
`files.file`, `files.put`, `files.get`, `files.edit` and `files.move`,
mirroring pyinfra's
API. Every
operation is an async generator taking an [`OpContext`][reemote5.core.OpContext],
awaiting facts via `ctx.facts`, setting `ctx.changed`/`ctx.reason` where a
change is needed, and yielding one [`Command`][reemote5.commands.Command]
descriptor per mutation.

`files.put` and `files.get` yield the *non-shell* descriptors:
`PutCommand` and
`GetCommand` have no `shell()` —
[`send_command`][reemote5.drivers.send_command] dispatches them over the host's
asyncssh connection with scp instead of running shell text, so the `sudo`/`su`
flags do not apply and the remote path must be writable (put) or readable (get)
by the login user. Each takes an **IO-like object only** on the control-machine
side (`src: IO[Any]` for put, `dest: IO[Any]` for get — where pyinfra accepts a
local filename too, a path string is deliberately not supported): put rewinds
its source before every read, get rewinds and truncates its destination before
writing and rewinds it again after, and the same object must be passed in both
phases (request equality compares it by identity). Idempotence is content-based
in both directions: `files.put` compares the source's `LocalSha1` against the
remote file's `RemoteFileSha1` and uploads only on a mismatch, while
`files.get` compares the remote file's `RemoteFileSha1` against the
destination's current `LocalSha1` and downloads only on a mismatch. pyinfra's
`files.get` options `add_deploy_dir`, `create_local_dir` and `force` are
dropped — there is no local path to place, create or force.

`files.edit` is the generic content-editing primitive, and the reason reemote5
ships no bespoke `server.etc_hosts`: it reads a file's current text with
`FileContents`, applies a caller-supplied *pure* `transform`
(`str | None -> str`), and yields `files.put`'s `PutCommand` only when the
result differs from the current content. Any "edit a file in place" task — an
`/etc/hosts` entry, a `sysctl` key, a managed block — is then just a transform,
with no new operation. pyinfra's `files.block` is covered exactly that way: the
`block_transform` factory in the
[block example](../examples/modules/files.md#managing-a-marked-block-pyinfra-filesblock-parity)
builds a marked-block transform from pyinfra's option set (content, markers,
regex positioning, removal), so no bespoke block editor is ported. The
transform must be idempotent (so a second edit is a
fixpoint) and a stable, hashable module-level callable (it is compared by value
across phases, so a lambda built inside the deploy generator would trip
`DeterminismError`). Because the write rides the same unprivileged scp transfer
as `files.put`, the target must be writable by the login user; a root-owned file
such as `/etc/hosts` must be transformed into a writable path and moved into
place with `files.move` flagged `sudo=True`.

`files.move` mirrors pyinfra's `files.move`: `src` is a remote path, `dest`
is the remote *directory* to move it into (the moved path becomes
`dest/basename(src)`), and `overwrite=True` removes an existing target first
(`rm -rf`) instead of raising. It is **stateless** like pyinfra — a valid
move always yields a command, there is no idempotent no-op branch — and it
reads `File(src)`, `Directory(dest)` and `File(dest/basename(src))` to
validate. An absent source, an absent or non-directory destination, and an
existing target without `overwrite` raise `ValueError`. pyinfra's
phase-aware softness for the two absence cases (a log while planning, an
error only while executing) is deliberately dropped — reemote5 operations
are phase-unaware, so they raise in *both* phases. A consequence: a
"create a file then move it" flow cannot share one deploy (prepare would
raise on the not-yet-created source) and spans two runs instead. Both
commands are shell text, so `sudo=True` elevates the whole move — the
vehicle for the `/etc/hosts` recipe: `files.edit` spools the transformed
content into a login-user-writable path in one run, then `files.move`
installs it into `/etc` elevated in a second run (see the
[example](../examples/modules/files.md)).

--->