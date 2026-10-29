# files module

`reemote5.modules.files.files` combines the file facts and the file operations
in a single source, so the read side (`FileLink`, `FileExists`,
`LocalSha1`, `RemoteFileSha1`) sits beside the `files.link`, `files.file`,
`files.put` and `files.get` operations that consume it.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `LocalSha1` is the
one fact whose input is not the host: it hashes an IO-like stream on the
*control* machine — the source `files.put` uploads or the destination
`files.get` downloads into — which both compare against the remote file's hash.

::: reemote5.modules.files.files
    options:
      members:
        - FileLink
        - FileExists
        - LocalSha1
        - RemoteFileSha1

## Operations

Operations live in namespace modules so a deploy reads `files.link`,
`files.file`, `files.put` and `files.get`, mirroring pyinfra's API. Every
operation is an async generator taking an [`OpContext`][reemote5.core.OpContext],
awaiting facts via `ctx.facts`, setting `ctx.changed`/`ctx.reason` where a
change is needed, and yielding one [`Command`][reemote5.commands.Command]
descriptor per mutation.

`files.put` and `files.get` yield the *non-shell* descriptors:
[`PutCommand`][reemote5.modules.files.files.PutCommand] and
[`GetCommand`][reemote5.modules.files.files.GetCommand] have no `shell()` —
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

::: reemote5.modules.files.files
    options:
      members:
        - link
        - LinkCommand
        - file
        - FileCommand
        - put
        - PutCommand
        - get
        - GetCommand
