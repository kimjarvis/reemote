# dnf module

`reemote5.modules.dnf.dnf` combines the dnf facts and the dnf operations in a
single source, so the read side (`DnfPackages` and friends) sits beside the
`dnf.packages`, `dnf.repo`, `dnf.rpm` and `dnf.module` operations that consume
it, plus the non-idempotent `dnf.key` and `dnf.update`, which read no fact:
`dnf.key` imports a GPG key stream and `dnf.update` runs `dnf update -y` over
every package.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step.

::: reemote5.modules.dnf.dnf
    options:
      members:
        - DnfPackages
        - DnfRepositories
        - DnfRepoFile
        - DnfRepoList
        - DnfEnabledModules
        - DnfDisabledModules
        - DnfInstalledModules
        - DnfGroups
        - DnfAvailableGroups
        - RpmPackage

## Operations

Operations live in namespace modules so a deploy reads `dnf.packages`,
mirroring pyinfra's API. Every operation is an async generator with the same
fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where it
decides a change is needed, and yields one descriptor per command that would
mutate the host.

::: reemote5.modules.dnf.dnf
    options:
      members:
        - packages
        - update
        - repo
        - rpm
        - key
        - module
