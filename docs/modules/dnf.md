# dnf module

## dnf Facts

::: reemote5.modules.dnf.dnf
    options:
      show_root_heading: false
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

## dnf Returns

::: reemote5.modules.dnf.dnf
    options:
      show_root_heading: false
      members:
        - DnfPackageInfo
        - DnfRepository
        - DnfRepoListEntry
        - DnfModule
        - DnfGroup
        - RpmPackageInfo

## dnf Operations

::: reemote5.modules.dnf.dnf
    options:
      show_root_heading: false
      members:
        - packages
        - update
        - repo
        - rpm
        - key
        - module


<!---

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
so results are awaited and cached within a phase step. Every fact returns a
TypedDict or a list of TypedDicts: the enumerating facts return sorted,
de-duplicated entry lists (`DnfPackages` → `DnfPackageInfo`, the three module
facts → the shared `DnfModule`, the two group facts → the shared `DnfGroup`,
`DnfRepositories` → `DnfRepository`, `DnfRepoList` → `DnfRepoListEntry`) — the
apt-side convention rather than pyinfra's runtime sets — so reads stay
deterministic and JSON-printable, while the target-specified `RpmPackage`
returns a single `RpmPackageInfo` (or None when the file is absent).
`DnfRepoListEntry` omits `status` when the listing has no status column.

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

--->