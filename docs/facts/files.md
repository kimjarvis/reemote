# files facts

`reemote5.facts.files` holds the file fact classes, mirroring the
[`files` operation namespace](../operations/files.md). A fact is an
asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over
asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.

## Reference

::: reemote5.facts.files
    options:
      members:
        - FileLink
