# dnf module

::: reemote5.modules.dnf.dnf
    options:
      show_root_heading: false
      members:
        - DnfAvailableGroups
        - DnfDisabledModules
        - DnfEnabledModules
        - DnfGroup
        - DnfGroups
        - DnfInstalledModules
        - DnfModule
        - DnfPackageInfo
        - DnfPackages
        - DnfRepoFile
        - DnfRepoList
        - DnfRepoListEntry
        - DnfRepositories
        - DnfRepository
        - RpmGpgKey
        - RpmGpgKeys
        - RpmPackage
        - RpmPackageInfo
        - clean
        - key
        - makecache
        - module
        - packages
        - repo
        - rpm
        - update


<!---

`reemote5.modules.dnf.dnf` combines the dnf facts and the dnf operations in a
single source, so the read side (`DnfPackages` and friends) sits beside the
`dnf.packages`, `dnf.repo`, `dnf.rpm` and `dnf.module` operations that consume
it, plus the non-idempotent `dnf.update`, which reads no fact and runs
`dnf update -y` over every package. `dnf.makecache` and `dnf.clean` are
reemote5 originals (the baseline has neither): equally stateless metadata
refresh and cache removal, optionally scoped to named repos with
`--disablerepo='*' --enablerepo=<id>` — the ansible-datadog role's scoped
`makecache`/`clean metadata` steps, which `dnf.packages(update=True)` (a
full system upgrade) and `clean=True` (`dnf clean all`) cannot express.
`dnf.repo` extends the baseline's option set with the same role's
`yum_repository` fields (`includepkgs`, `repo_gpgcheck`, `proxy`,
`proxy_username`, `proxy_password`); each is omitted from the constructed
file when unset, and a `proxy_password` installs the file `0600` instead
of `0644`. `dnf.key` is bidirectional: the import
direction (`present=True`, the default) stays non-idempotent and uploads a
GPG key stream, while the removal direction (`present=False`, Ansible
`rpm_key state=absent` parity) reads `RpmGpgKeys` and idempotently erases the
matching `gpg-pubkey` pseudo-packages with an elevated `rpm -e`.

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
apt-side convention rather than the baseline's runtime sets — so reads stay
deterministic and JSON-printable, while the target-specified `RpmPackage`
returns a single `RpmPackageInfo` (or None when the file is absent).
`DnfRepoListEntry` omits `status` when the listing has no status column.
`RpmGpgKeys` enumerates the `gpg-pubkey-<keyid>-<release>` pseudo-packages
`rpm --import` records in the rpm database (→ `RpmGpgKey`) — the keys are
invisible to `DnfPackages` and to the `gpg` namespace's keyring facts.

## Operations

Operations live in namespace modules so a deploy reads `dnf.packages`,
mirroring the baseline's API. Every operation is an async generator with the same
fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where it
decides a change is needed, and yields one descriptor per command that would
mutate the host.

--->
