# dnf module

::: reemote.modules.dnf.dnf
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
        - RpmProvide
        - RpmProvides
        - clean
        - key
        - makecache
        - module
        - packages
        - repo
        - rpm
        - update


<!---

`reemote.modules.dnf.dnf` combines the dnf facts and the dnf operations in a
single source, so the read side (`DnfPackages` and friends) sits beside the
`dnf.packages`, `dnf.repo`, `dnf.rpm` and `dnf.module` operations that consume
it, plus the non-idempotent `dnf.update`, which reads no fact and runs
`dnf update -y` over every package. `dnf.packages` reads the `DnfPackages`
fact's per-name version lists, so a pinned `<pkg>=<version>` is present only
when that *exact* version is installed (the `apt.packages` precedent, not a
name-only match), and `allow_downgrade=True` renders `dnf downgrade
--allowerasing` for a pinned package whose name is already installed at a
different version — dnf's dedicated verb for crossing that boundary, since
`dnf install` will not do it and has no equivalent flag. Presence is a
names-only test against `DnfPackages`, while dnf itself resolves a spec against
`Provides:` — so a requested name that survives on the host only as a virtual
provide used to be invisible to the gate (issue #76): EPEL9's `clamav-update`,
carried by `clamav-freshclam`, and `clamav-scanner-systemd`, carried by `clamd`,
kept a `present=True` request from ever converging (and re-fired its handler on
every run) while `present=False` silently no-opped. The operation now settles
those residual misses against the `RpmProvides` read — issued only for the names
`DnfPackages` misses, so a host that already carries every requested name costs
no extra query — which means a requested name may be satisfied by an installed
package that *provides* it, and a removal hands dnf the provider's real name. A
capability nothing provides stays unresolved, leaving the spec verbatim in the
request for dnf itself to report. Beyond `repos=`
(enable-only-named scoping, `--disablerepo='*' --enablerepo=<id>`),
`dnf.packages` also takes a bare `disable_repos=` (one `--disablerepo=<id>`
per name, no `--disablerepo='*'`) so Ansible's `disablerepo: appstream` —
"every repo except this one" — is first-class rather than smuggled through
`extra_install_args`, its additive mirror `enable_repos=` (one bare
`--enablerepo=<id>` per name, no `--disablerepo='*'`) so Ansible's
`enablerepo: <id>` — "this configured-but-disabled repo too, alongside every
enabled one" (the postgresql role's `postgresql_enablerepo`) — is equally
first-class, an `exclude=` filter (one `--exclude=<pkg>` per name on
the install/downgrade/update transactions, Ansible's `yum: exclude=` — the
postgresql role's `exclude: python-unversioned-command` — but not the remove
or the makecache), and an `update_cache=True` flag that yields a scoped
`dnf -y makecache` ahead of the transaction so `update_cache: true` is
first-class rather than a separate `dnf.makecache` request. `dnf.makecache` and
`dnf.clean` are
reemote originals (the baseline has neither): equally stateless metadata
refresh and cache removal, optionally scoped to named repos with
`--disablerepo='*' --enablerepo=<id>` — the ansible-datadog role's scoped
`makecache`/`clean metadata` steps, which `dnf.packages(update=True)` (a
full system upgrade) and `clean=True` (`dnf clean all`) cannot express.
`dnf.repo` extends the baseline's option set with the same role's
`yum_repository` fields (`includepkgs`, `repo_gpgcheck`, `proxy`,
`proxy_username`, `proxy_password`); each is omitted from the constructed
file when unset, and a `proxy_password` installs the file `0600` instead
of `0644`. `dnf.key` is bidirectional: the import
direction (`present=True`, the default) uploads a
GPG key stream and is idempotent when `keyid` names the key — it reads
`RpmGpgKeys` and skips the upload/import pair when a matching `gpg-pubkey`
entry is already imported (the `gpg.import_key` precedent, Ansible
`rpm_key state=present` parity); without `keyid` it stays stateless. The
removal direction (`present=False`, Ansible
`rpm_key state=absent` parity) reads `RpmGpgKeys` and idempotently erases the
matching `gpg-pubkey` pseudo-packages with an elevated `rpm -e`.
`dnf.rpm` installs a host-staged `.rpm` (a URL `src` raises `ValueError`; stage
one with `files.download`, which installs a binary body intact since #23) and
takes the same optional `package`/`version` *identity hint* as `apt.deb` (issue
#55): when the staged file answers nothing, presence falls back to the
installed rpm database, so a request still converges after the artifact is
cleaned up and `present=False` still removes the package. `version` is rpm's
`VERSION-RELEASE` compared verbatim (`8.1-1.el9`); a bare `package` accepts any
installed version, and `version` without `package` raises `ValueError`.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get], which is itself a coroutine,
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
`RpmProvides` is the provides-side companion to the names-only `DnfPackages`
read (→ `RpmProvide`): it takes the capabilities to resolve, asks
`rpm -q --whatprovides` once per capability in a single shell, and reports each
installed provider's real name and `VERSION-RELEASE` beside the capability it
was queried for, so a virtual name can be attributed even when the same package
carries two of them.

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
