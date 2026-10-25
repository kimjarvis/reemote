# apt facts

`reemote5.facts.apt` holds the apt fact classes, mirroring the
[`apt` operation namespace](../operations/apt.md). A fact is an
asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over
asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.

## Reference

::: reemote5.facts.apt
    options:
      members:
        - AptPackages
