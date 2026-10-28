# files module

`reemote5.modules.files.files` combines the file facts and the file operations
in a single source, so the read side (`FileLink`, `FileExists`) sits beside the
`files.link` and `files.file` operations that consume it.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step.

::: reemote5.modules.files.files
    options:
      members:
        - FileLink
        - FileExists

## Operations

Operations live in namespace modules so a deploy reads `files.link` and
`files.file`, mirroring pyinfra's API. Every operation is an async generator
taking an [`OpContext`][reemote5.core.OpContext], awaiting facts via
`ctx.facts`, setting `ctx.changed`/`ctx.reason` where a change is needed, and
yielding one [`Command`][reemote5.commands.Command] descriptor per mutation.

::: reemote5.modules.files.files
    options:
      members:
        - link
        - LinkCommand
        - file
        - FileCommand
