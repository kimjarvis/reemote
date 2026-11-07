# reemote5

**reemote5** is a python library for configuration management.
It provides cross-host lockstep ordering and dry-run reporting. 

<!---

**reemote5** is a proof-of-concept execution model for configuration
management. It explores an alternative to [pyinfra]'s two-phase
*prepare → execute* design: **deploy scripts are Python generators** that
yield operation descriptors instead of calling operations directly.

That single change lets the driver pause a deploy script at every operation
boundary, which in turn makes sequential *"act, then read"* logic produce the
correct answer in both phases — while keeping the properties that the
two-phase split exists to provide: cross-host lockstep ordering and dry-run
reporting.



!!! warning "POC scope"

    reemote5 is a **library**, not a deployment tool. It is deliberately
    restricted to *generating commands*:

    - no SSH or any other connection transport,
    - no CLI, inventory files or plugin system — hosts are specified as a
      simple *inventory* (a list of dictionaries) passed to
      [`run`][reemote5.drivers.run],
    - without an injected transport, hosts are in-memory `SimulatedHost`
      objects — part of the test framework (`tests/simulated_host.py`),
      never of the library.

    Nothing here connects to a real machine. The point of the project is the
    execution model, demonstrated end to end and covered by tests.

    The command-execution path *is* asynchronous:
    [`send_command`][reemote5.drivers.send_command] — and therefore
    [`run_apply`][reemote5.drivers.run_apply] and
    [`run`][reemote5.drivers.run] — are coroutines, preparing for a real
    [asyncssh]-based transport (pyinfra uses Paramiko + gevent instead).
    Fact gathering is async for the same reason: a fact's `process`,
    [`FactCache.get`][reemote5.core.FactCache.get], the operations that await
    it, and [`run_prepare`][reemote5.drivers.run_prepare] are all coroutines,
    so the whole prepare/apply path is awaited end to end.

## Why generators?

A deploy is naturally written as a sequence of steps where later steps depend
on the effect of earlier ones. Consider "install nginx, then remove its
default site": the site link only exists *after* the package is installed.

In a two-phase model the prepare phase runs before anything is executed, so a
naive prepare phase cannot see that link:

```text
prepare: install nginx → changed        (nginx not installed yet)
         remove link   → no change      (link does not exist yet — wrong!)
apply:   install nginx → changed        (link now created as a side effect)
         remove link   → ???            (script already decided "no change")
```

pyinfra solves this with its own machinery. reemote5 solves it with the
generator itself:

1. The deploy body is a generator, so the driver controls *when* the next
   step is computed.
2. In apply, the driver recreates the
   [`FactCache`][reemote5.core.FactCache] **before** pulling each request, and
   uses that cache to materialize it.
3. Fact reads made by an operation therefore observe everything the previous
   operations did — sequential semantics, no phase flag required.

The prepare phase still yields a full plan, and the apply phase still runs
every host in lockstep. Neither facts nor operations know which phase they are
in; only the drivers do.

--->

## Installation

The project is managed with [uv] and targets Python 3.13+:

```bash
uv sync                    # library + dev (pytest)
uv sync --group docs       # + mkdocs-material and mkdocstrings
```

## Quick start

A deploy script is a generator with a fixed signature, yielding requests built
with the [`op`][reemote5.core.op] helper:

```python
from collections.abc import Generator

from reemote5 import Host, OperationRequest, dnf, files, op, run

SITE = "/etc/nginx/sites-enabled/default"
TARGET = "/etc/nginx/sites-available/default"


def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(dnf.packages, name="Install nginx", packages=("nginx",))
    yield op(files.link, name="Remove default site", src=SITE, present=False)
```

Hosts are specified as an *inventory* — a list of dictionaries, each carrying
at least a host's name (`host`) and SSH `connection` information — and passed
to [`run`][reemote5.drivers.run], which validates
it (every entry must have `host` and `connection`; a malformed inventory raises
[`InventoryError`][reemote5.hosts.InventoryError]) and then prepares and applies
in one call. `run` is a coroutine, so it must be awaited (here from an
`asyncio.run` entry point):

```python
import asyncio

from reemote5 import format_log, format_plan, run

# Test-framework stand-in, lives in tests/simulated_host.py — not part of
# the library API.
from tests.simulated_host import SimulatedHost

inventory = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]


def simulated_factory(entry: dict) -> SimulatedHost:
    # Test-framework stand-in, injected via host_factory so this runs
    # without SSH: web1 is fresh (installing nginx creates the
    # default-site link); web2 already has nginx and the link.
    host = SimulatedHost.from_inventory(entry)
    if host.name == "web1":
        host.installs_links = {"nginx": {SITE: TARGET}}
    else:
        host.packages = {"nginx"}
        host.links = {SITE: TARGET}
    return host


async def main() -> None:
    plans, report, hosts = await run(
        inventory, deploy, host_factory=simulated_factory
    )
    print(format_plan(plans))
    print(format_log(report))


asyncio.run(main())
```

Pass `dry=True` to stop after the prepare phase — nothing is sent to the hosts
and `report` is `None`.

A complete, runnable version of this example — including the final host state
— lives in
[`examples/dnf_install_nginx.py`](https://github.com/kimjarvis/reemote5/blob/main/examples/dnf_install_nginx.py):

```bash
uv run python examples/dnf_install_nginx.py
```

```text
== Plan (prepare phase, --dry) ==
web1: Install nginx            [changed: 1 package(s) to install]
web1: Remove default site      [no change: link already absent]
web2: Install nginx            [no change: all packages present]
web2: Remove default site      [changed: link present, will remove]

== Run log (apply phase) ==
web1: Install nginx            [changed: 1 package(s) to install]
    $ dnf install -y nginx
web2: Install nginx            [no change: all packages present]
web1: Remove default site      [changed: link present, will remove]
    $ rm -f /etc/nginx/sites-enabled/default
web2: Remove default site      [changed: link present, will remove]
    $ rm -f /etc/nginx/sites-enabled/default

== Final host state ==
web1: packages=['nginx'] links={}
web2: packages=['nginx'] links={}
```

The interesting line is `web1: Remove default site`. The plan says *no change*
because nginx is not installed yet during prepare; the apply log says *changed*
because installing nginx created the link and the fresh fact cache saw it. Same
deploy script, same request sequence, correct outcome in both phases.


<!---

## API reference

Everything documented in the reference is re-exported from the top-level
package, so a deploy script only ever needs one import line:

```python
from reemote5 import Host, OperationRequest, dnf, files, op, run
```

The API has six layers, each documented on a page named after the source
module that defines it:

| Layer | Objects | Reference page |
| --- | --- | --- |
| **Drivers** | [`run`][reemote5.drivers.run], [`run_prepare`][reemote5.drivers.run_prepare], [`run_apply`][reemote5.drivers.run_apply], [`send_command`][reemote5.drivers.send_command], [`format_plan`][reemote5.drivers.format_plan], [`format_log`][reemote5.drivers.format_log] | [drivers](drivers.md) |
| **Core objects** | [`OperationRequest`][reemote5.core.OperationRequest], [`op`][reemote5.core.op], [`OpContext`][reemote5.core.OpContext], [`FactCache`][reemote5.core.FactCache], [`HostRuntime`][reemote5.core.HostRuntime], [`OperationError`][reemote5.core.OperationError] | [core](core.md) |
| **Commands** | [`Command`][reemote5.commands.Command], [`StringCommand`][reemote5.commands.StringCommand], [`TransferCommand`][reemote5.commands.TransferCommand], [`WaitCommand`][reemote5.commands.WaitCommand], [`WaitTimeoutError`][reemote5.commands.WaitTimeoutError] | [commands](commands.md) |
| **Hosts, facts, operations** | [`Host`][reemote5.hosts.Host], [`validate_inventory`][reemote5.hosts.validate_inventory], [`InventoryError`][reemote5.hosts.InventoryError], [`ApkAutoremovablePackages`][reemote5.modules.apk.apk.ApkAutoremovablePackages], [`ApkExplicitPackages`][reemote5.modules.apk.apk.ApkExplicitPackages], [`ApkHeldPackages`][reemote5.modules.apk.apk.ApkHeldPackages], [`ApkInstalledPackages`][reemote5.modules.apk.apk.ApkInstalledPackages], [`ApkRepositories`][reemote5.modules.apk.apk.ApkRepositories], [`AptKeys`][reemote5.modules.apt.apt.AptKeys], [`DnfPackages`][reemote5.modules.dnf.dnf.DnfPackages], [`FileLink`][reemote5.modules.files.files.FileLink], [`RemoteFileSha1`][reemote5.modules.files.files.RemoteFileSha1], [`FileContents`][reemote5.modules.files.files.FileContents], [`FirewalldStatus`][reemote5.modules.firewalld.firewalld.FirewalldStatus], [`FirewalldDefaultZone`][reemote5.modules.firewalld.firewalld.FirewalldDefaultZone], [`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones], [`FirewalldServices`][reemote5.modules.firewalld.firewalld.FirewalldServices], [`FirewalldIpSets`][reemote5.modules.firewalld.firewalld.FirewalldIpSets], [`FirewalldPolicies`][reemote5.modules.firewalld.firewalld.FirewalldPolicies], [`BlockDevices`][reemote5.modules.hardware.hardware.BlockDevices], [`GitBranch`][reemote5.modules.git.git.GitBranch], [`GpgKey`][reemote5.modules.gpg.gpg.GpgKey], [`GpgKeyDetails`][reemote5.modules.gpg.gpg.GpgKeyDetails], [`GpgKeyrings`][reemote5.modules.gpg.gpg.GpgKeyrings], [`GpgKeys`][reemote5.modules.gpg.gpg.GpgKeys], [`GpgSecretKeys`][reemote5.modules.gpg.gpg.GpgSecretKeys], [`GpgTrust`][reemote5.modules.gpg.gpg.GpgTrust], [`GpgVerify`][reemote5.modules.gpg.gpg.GpgVerify], [`OpenrcEnabled`][reemote5.modules.openrc.openrc.OpenrcEnabled], [`OpenrcRunlevels`][reemote5.modules.openrc.openrc.OpenrcRunlevels], [`OpenrcServices`][reemote5.modules.openrc.openrc.OpenrcServices], [`OpenrcStatus`][reemote5.modules.openrc.openrc.OpenrcStatus], [`OpenrcVersion`][reemote5.modules.openrc.openrc.OpenrcVersion], [`MysqlDatabases`][reemote5.modules.mysql.mysql.MysqlDatabases], [`MysqlUsers`][reemote5.modules.mysql.mysql.MysqlUsers], [`MysqlUserGrants`][reemote5.modules.mysql.mysql.MysqlUserGrants], [`MysqlBinaryLogs`][reemote5.modules.mysql.mysql.MysqlBinaryLogs], [`MysqlColumns`][reemote5.modules.mysql.mysql.MysqlColumns], [`MysqlConnections`][reemote5.modules.mysql.mysql.MysqlConnections], [`MysqlEngines`][reemote5.modules.mysql.mysql.MysqlEngines], [`MysqlIndexes`][reemote5.modules.mysql.mysql.MysqlIndexes], [`MysqlPlugins`][reemote5.modules.mysql.mysql.MysqlPlugins], [`MysqlReplication`][reemote5.modules.mysql.mysql.MysqlReplication], [`MysqlStatus`][reemote5.modules.mysql.mysql.MysqlStatus], [`MysqlTables`][reemote5.modules.mysql.mysql.MysqlTables], [`MysqlVariables`][reemote5.modules.mysql.mysql.MysqlVariables], [`PostgresqlRoles`][reemote5.modules.postgresql.postgresql.PostgresqlRoles], [`PostgresqlDatabases`][reemote5.modules.postgresql.postgresql.PostgresqlDatabases], [`PostgresqlRoleMemberships`][reemote5.modules.postgresql.postgresql.PostgresqlRoleMemberships], [`PostgresqlPrivileges`][reemote5.modules.postgresql.postgresql.PostgresqlPrivileges], [`PostgresqlSchemas`][reemote5.modules.postgresql.postgresql.PostgresqlSchemas], [`PostgresqlTables`][reemote5.modules.postgresql.postgresql.PostgresqlTables], [`PostgresqlIndexes`][reemote5.modules.postgresql.postgresql.PostgresqlIndexes], [`PostgresqlExtensions`][reemote5.modules.postgresql.postgresql.PostgresqlExtensions], [`PostgresqlSettings`][reemote5.modules.postgresql.postgresql.PostgresqlSettings], [`PostgresqlTablespaces`][reemote5.modules.postgresql.postgresql.PostgresqlTablespaces], [`PostgresqlReplication`][reemote5.modules.postgresql.postgresql.PostgresqlReplication], [`PostgresqlConnections`][reemote5.modules.postgresql.postgresql.PostgresqlConnections], [`PostgresqlPublications`][reemote5.modules.postgresql.postgresql.PostgresqlPublications], [`PostgresqlSubscriptions`][reemote5.modules.postgresql.postgresql.PostgresqlSubscriptions], [`SEBoolean`][reemote5.modules.selinux.selinux.SEBoolean], [`Booleans`][reemote5.modules.selinux.selinux.Booleans], [`FileContext`][reemote5.modules.selinux.selinux.FileContext], [`FileContextMapping`][reemote5.modules.selinux.selinux.FileContextMapping], [`SEPorts`][reemote5.modules.selinux.selinux.SEPorts], [`SEPort`][reemote5.modules.selinux.selinux.SEPort], [`Mode`][reemote5.modules.selinux.selinux.Mode], [`SELogin`][reemote5.modules.selinux.selinux.SELogin], [`SEUser`][reemote5.modules.selinux.selinux.SEUser], [`Modules`][reemote5.modules.selinux.selinux.Modules], [`PermissiveDomains`][reemote5.modules.selinux.selinux.PermissiveDomains], [`IptablesChains`][reemote5.modules.iptables.iptables.IptablesChains], [`IptablesRules`][reemote5.modules.iptables.iptables.IptablesRules], [`Ip6tablesChains`][reemote5.modules.iptables.iptables.Ip6tablesChains], [`Ip6tablesRules`][reemote5.modules.iptables.iptables.Ip6tablesRules], [`IptablesRulesOrdered`][reemote5.modules.iptables.iptables.IptablesRulesOrdered], [`Ip6tablesRulesOrdered`][reemote5.modules.iptables.iptables.Ip6tablesRulesOrdered], [`IptablesTables`][reemote5.modules.iptables.iptables.IptablesTables], [`Ip6tablesTables`][reemote5.modules.iptables.iptables.Ip6tablesTables], [`IptablesVersion`][reemote5.modules.iptables.iptables.IptablesVersion], [`Ip6tablesVersion`][reemote5.modules.iptables.iptables.Ip6tablesVersion], [`IptablesModules`][reemote5.modules.iptables.iptables.IptablesModules], [`IpSets`][reemote5.modules.iptables.iptables.IpSets], [`IptablesPersistence`][reemote5.modules.iptables.iptables.IptablesPersistence], [`Ip6tablesPersistence`][reemote5.modules.iptables.iptables.Ip6tablesPersistence], [`LxdContainers`][reemote5.modules.lxd.lxd.LxdContainers], [`LxdImageAliases`][reemote5.modules.lxd.lxd.LxdImageAliases], [`LxdImages`][reemote5.modules.lxd.lxd.LxdImages], [`LxdInstanceConfig`][reemote5.modules.lxd.lxd.LxdInstanceConfig], [`LxdInstanceSnapshots`][reemote5.modules.lxd.lxd.LxdInstanceSnapshots], [`LxdInstanceState`][reemote5.modules.lxd.lxd.LxdInstanceState], [`LxdInstances`][reemote5.modules.lxd.lxd.LxdInstances], [`LxdNetworks`][reemote5.modules.lxd.lxd.LxdNetworks], [`LxdOperations`][reemote5.modules.lxd.lxd.LxdOperations], [`LxdProfiles`][reemote5.modules.lxd.lxd.LxdProfiles], [`LxdProjectConfig`][reemote5.modules.lxd.lxd.LxdProjectConfig], [`LxdProjects`][reemote5.modules.lxd.lxd.LxdProjects], [`LxdServerInfo`][reemote5.modules.lxd.lxd.LxdServerInfo], [`LxdServerResources`][reemote5.modules.lxd.lxd.LxdServerResources], [`LxdStoragePools`][reemote5.modules.lxd.lxd.LxdStoragePools], [`LxdStorageVolumes`][reemote5.modules.lxd.lxd.LxdStorageVolumes], [`Sysctl`][reemote5.modules.sysctl.sysctl.Sysctl], [`SysctlFile`][reemote5.modules.sysctl.sysctl.SysctlFile], [`SysctlFiles`][reemote5.modules.sysctl.sysctl.SysctlFiles], [`SystemdServiceState`][reemote5.modules.systemd.systemd.SystemdServiceState], [`SystemdStatus`][reemote5.modules.systemd.systemd.SystemdStatus], [`SystemdEnabled`][reemote5.modules.systemd.systemd.SystemdEnabled], [`SystemdUnitProperties`][reemote5.modules.systemd.systemd.SystemdUnitProperties], [`SystemdFailedUnits`][reemote5.modules.systemd.systemd.SystemdFailedUnits], [`SystemdTimers`][reemote5.modules.systemd.systemd.SystemdTimers], [`SystemdUnitFiles`][reemote5.modules.systemd.systemd.SystemdUnitFiles], [`SystemdVersion`][reemote5.modules.systemd.systemd.SystemdVersion], [`SystemdSystemState`][reemote5.modules.systemd.systemd.SystemdSystemState], [`SystemdDependencies`][reemote5.modules.systemd.systemd.SystemdDependencies], [`UfwStatus`][reemote5.modules.ufw.ufw.UfwStatus], [`UfwRules`][reemote5.modules.ufw.ufw.UfwRules], [`UfwApplications`][reemote5.modules.ufw.ufw.UfwApplications], [`apk.autoremove`][reemote5.modules.apk.apk.autoremove], [`apk.hold`][reemote5.modules.apk.apk.hold], [`apk.packages`][reemote5.modules.apk.apk.packages], [`apk.repository`][reemote5.modules.apk.apk.repository], [`apk.virtual`][reemote5.modules.apk.apk.virtual], [`apt.deb`][reemote5.modules.apt.apt.deb], [`apt.dist_upgrade`][reemote5.modules.apt.apt.dist_upgrade], [`apt.auto_remove`][reemote5.modules.apt.apt.auto_remove], [`apt.key`][reemote5.modules.apt.apt.key], [`apt.mark`][reemote5.modules.apt.apt.mark], [`apt.packages`][reemote5.modules.apt.apt.packages], [`apt.ppa`][reemote5.modules.apt.apt.ppa], [`apt.repo`][reemote5.modules.apt.apt.repo], [`apt.sources_file`][reemote5.modules.apt.apt.sources_file], [`apt.update`][reemote5.modules.apt.apt.update], [`apt.upgrade`][reemote5.modules.apt.apt.upgrade], [`dnf.packages`][reemote5.modules.dnf.dnf.packages], [`dnf.update`][reemote5.modules.dnf.dnf.update], [`dnf.repo`][reemote5.modules.dnf.dnf.repo], [`dnf.rpm`][reemote5.modules.dnf.dnf.rpm], [`dnf.key`][reemote5.modules.dnf.dnf.key], [`dnf.module`][reemote5.modules.dnf.dnf.module], [`files.link`][reemote5.modules.files.files.link], [`files.put`][reemote5.modules.files.files.put], [`files.get`][reemote5.modules.files.files.get], [`files.edit`][reemote5.modules.files.files.edit], [`files.move`][reemote5.modules.files.files.move], [`files.copy`][reemote5.modules.files.files.copy], [`git.config`][reemote5.modules.git.git.config], [`git.repo`][reemote5.modules.git.git.repo], [`git.worktree`][reemote5.modules.git.git.worktree], [`gpg.dearmor`][reemote5.modules.gpg.gpg.dearmor], [`gpg.delete_key`][reemote5.modules.gpg.gpg.delete_key], [`gpg.export_key`][reemote5.modules.gpg.gpg.export_key], [`gpg.gen_key`][reemote5.modules.gpg.gpg.gen_key], [`gpg.import_key`][reemote5.modules.gpg.gpg.import_key], [`gpg.key`][reemote5.modules.gpg.gpg.key], [`gpg.refresh_keys`][reemote5.modules.gpg.gpg.refresh_keys], [`gpg.trust`][reemote5.modules.gpg.gpg.trust], [`openrc.custom_runlevel`][reemote5.modules.openrc.openrc.custom_runlevel], [`openrc.runlevel`][reemote5.modules.openrc.openrc.runlevel], [`openrc.service`][reemote5.modules.openrc.openrc.service], [`mysql.database`][reemote5.modules.mysql.mysql.database], [`mysql.user`][reemote5.modules.mysql.mysql.user], [`mysql.privileges`][reemote5.modules.mysql.mysql.privileges], [`mysql.sql`][reemote5.modules.mysql.mysql.sql], [`mysql.dump`][reemote5.modules.mysql.mysql.dump], [`mysql.load`][reemote5.modules.mysql.mysql.load], [`mysql.table`][reemote5.modules.mysql.mysql.table], [`mysql.index`][reemote5.modules.mysql.mysql.index], [`mysql.variable`][reemote5.modules.mysql.mysql.variable], [`mysql.plugin`][reemote5.modules.mysql.mysql.plugin], [`mysql.role`][reemote5.modules.mysql.mysql.role], [`mysql.replica`][reemote5.modules.mysql.mysql.replica], [`mysql.kill`][reemote5.modules.mysql.mysql.kill], [`mysql.optimize`][reemote5.modules.mysql.mysql.optimize], [`postgresql.database`][reemote5.modules.postgresql.postgresql.database], [`postgresql.role`][reemote5.modules.postgresql.postgresql.role], [`postgresql.sql`][reemote5.modules.postgresql.postgresql.sql], [`postgresql.dump`][reemote5.modules.postgresql.postgresql.dump], [`postgresql.load`][reemote5.modules.postgresql.postgresql.load], [`postgresql.privilege`][reemote5.modules.postgresql.postgresql.privilege], [`postgresql.membership`][reemote5.modules.postgresql.postgresql.membership], [`postgresql.schema`][reemote5.modules.postgresql.postgresql.schema], [`postgresql.extension`][reemote5.modules.postgresql.postgresql.extension], [`postgresql.index`][reemote5.modules.postgresql.postgresql.index], [`postgresql.setting`][reemote5.modules.postgresql.postgresql.setting], [`postgresql.tablespace`][reemote5.modules.postgresql.postgresql.tablespace], [`postgresql.publication`][reemote5.modules.postgresql.postgresql.publication], [`postgresql.subscription`][reemote5.modules.postgresql.postgresql.subscription], [`selinux.boolean`][reemote5.modules.selinux.selinux.boolean], [`selinux.file_context`][reemote5.modules.selinux.selinux.file_context], [`selinux.file_context_mapping`][reemote5.modules.selinux.selinux.file_context_mapping], [`selinux.port`][reemote5.modules.selinux.selinux.port], [`selinux.mode`][reemote5.modules.selinux.selinux.mode], [`selinux.login`][reemote5.modules.selinux.selinux.login], [`selinux.module`][reemote5.modules.selinux.selinux.module], [`selinux.permissive_domain`][reemote5.modules.selinux.selinux.permissive_domain], [`selinux.restorecon`][reemote5.modules.selinux.selinux.restorecon], [`selinux.autorelabel`][reemote5.modules.selinux.selinux.autorelabel], [`iptables.chain`][reemote5.modules.iptables.iptables.chain], [`iptables.policy`][reemote5.modules.iptables.iptables.policy], [`iptables.rule`][reemote5.modules.iptables.iptables.rule], [`iptables.flush`][reemote5.modules.iptables.iptables.flush], [`iptables.zero`][reemote5.modules.iptables.iptables.zero], [`iptables.save`][reemote5.modules.iptables.iptables.save], [`iptables.restore`][reemote5.modules.iptables.iptables.restore], [`iptables.ipset`][reemote5.modules.iptables.iptables.ipset], [`lxd.container`][reemote5.modules.lxd.lxd.container], [`lxd.image`][reemote5.modules.lxd.lxd.image], [`lxd.image_alias`][reemote5.modules.lxd.lxd.image_alias], [`lxd.instance`][reemote5.modules.lxd.lxd.instance], [`lxd.instance_config`][reemote5.modules.lxd.lxd.instance_config], [`lxd.instance_copy`][reemote5.modules.lxd.lxd.instance_copy], [`lxd.instance_snapshot`][reemote5.modules.lxd.lxd.instance_snapshot], [`lxd.instance_state`][reemote5.modules.lxd.lxd.instance_state], [`lxd.network`][reemote5.modules.lxd.lxd.network], [`lxd.operation`][reemote5.modules.lxd.lxd.operation], [`lxd.profile`][reemote5.modules.lxd.lxd.profile], [`lxd.project`][reemote5.modules.lxd.lxd.project], [`lxd.storage_pool`][reemote5.modules.lxd.lxd.storage_pool], [`lxd.storage_volume`][reemote5.modules.lxd.lxd.storage_volume], [`firewalld.zone`][reemote5.modules.firewalld.firewalld.zone], [`firewalld.zone_service`][reemote5.modules.firewalld.firewalld.zone_service], [`firewalld.port`][reemote5.modules.firewalld.firewalld.port], [`firewalld.interface`][reemote5.modules.firewalld.firewalld.interface], [`firewalld.source`][reemote5.modules.firewalld.firewalld.source], [`firewalld.rich_rule`][reemote5.modules.firewalld.firewalld.rich_rule], [`firewalld.protocol`][reemote5.modules.firewalld.firewalld.protocol], [`firewalld.icmp_block`][reemote5.modules.firewalld.firewalld.icmp_block], [`firewalld.forward_port`][reemote5.modules.firewalld.firewalld.forward_port], [`firewalld.masquerade`][reemote5.modules.firewalld.firewalld.masquerade], [`firewalld.service`][reemote5.modules.firewalld.firewalld.service], [`firewalld.ipset`][reemote5.modules.firewalld.firewalld.ipset], [`firewalld.default_zone`][reemote5.modules.firewalld.firewalld.default_zone], [`firewalld.lockdown`][reemote5.modules.firewalld.firewalld.lockdown], [`firewalld.panic`][reemote5.modules.firewalld.firewalld.panic], [`firewalld.reload`][reemote5.modules.firewalld.firewalld.reload], [`server.group`][reemote5.modules.server.server.group], [`server.hostname`][reemote5.modules.server.server.hostname], [`server.kill`][reemote5.modules.server.server.kill], [`server.modprobe`][reemote5.modules.server.server.modprobe], [`server.mount`][reemote5.modules.server.server.mount], [`sysctl.conf`][reemote5.modules.sysctl.sysctl.conf], [`sysctl.setting`][reemote5.modules.sysctl.sysctl.setting], [`systemd.service`][reemote5.modules.systemd.systemd.service], [`systemd.daemon_reload`][reemote5.modules.systemd.systemd.daemon_reload], [`systemd.mask`][reemote5.modules.systemd.systemd.mask], [`systemd.reset_failed`][reemote5.modules.systemd.systemd.reset_failed], [`systemd.dropin`][reemote5.modules.systemd.systemd.dropin], [`systemd.tmpfiles`][reemote5.modules.systemd.systemd.tmpfiles], [`systemd.sysusers`][reemote5.modules.systemd.systemd.sysusers], [`systemd.preset`][reemote5.modules.systemd.systemd.preset], [`ufw.state`][reemote5.modules.ufw.ufw.state], [`ufw.default_policy`][reemote5.modules.ufw.ufw.default_policy], [`ufw.logging`][reemote5.modules.ufw.ufw.logging], [`ufw.rule`][reemote5.modules.ufw.ufw.rule], [`ufw.reset`][reemote5.modules.ufw.ufw.reset], [`server.timezone`][reemote5.modules.server.server.timezone], [`server.wait_service`][reemote5.modules.server.server.wait_service] | [hosts](hosts.md), [modules/apk](modules/apk.md), [modules/apt](modules/apt.md), [modules/dnf](modules/dnf.md), [modules/files](modules/files.md), [modules/firewalld](modules/firewalld.md), [modules/git](modules/git.md), [modules/gpg](modules/gpg.md), [modules/hardware](modules/hardware.md), [modules/iptables](modules/iptables.md), [modules/lxd](modules/lxd.md), [modules/openrc](modules/openrc.md), [modules/mysql](modules/mysql.md), [modules/postgresql](modules/postgresql.md), [modules/selinux](modules/selinux.md), [modules/server](modules/server.md), [modules/sysctl](modules/sysctl.md), [modules/systemd](modules/systemd.md), [modules/ufw](modules/ufw.md) |
| **Connectors** | [`DockerHost`][reemote5.connectors.docker.DockerHost], [`validate_docker_connection`][reemote5.connectors.docker.validate_docker_connection] | [connectors/docker](connectors/docker.md) |
| **Actions** | [`Shell`][reemote5.actions.Shell], [`WaitForConnection`][reemote5.actions.WaitForConnection], [`Result`][reemote5.actions.Result], [`gather_action`][reemote5.actions.gather_action], [`ActionInDeployError`][reemote5.actions.ActionInDeployError], [`ConnectionTimeoutError`][reemote5.actions.ConnectionTimeoutError] | [actions](actions.md) |

!!! note "Design-doc references"

    Several docstrings cite *design doc section N*. Those are section numbers
    from the original design notes for this POC; they are informational only
    and are not published here.

## Project layout

```text
src/reemote5/
├── core.py            # OperationRequest, OpContext, FactCache, HostRuntime, OperationError, op()
├── commands.py        # Command descriptors (Command, StringCommand, TransferCommand, WaitCommand)
├── drivers.py         # run_prepare, run_apply, send_command, run, formatters
├── actions.py         # Shell + WaitForConnection actions, Result, gather_action (immediate, outside deploys)
├── hosts.py           # Host (materialized from inventory entries)
├── connectors/        # alternative transports selected by connection.connector
│   └── docker.py      #   DockerHost (local Docker/Podman containers via the CLI)
└── modules/           # self-contained namespaces (facts + operations together)
    ├── apk/
    │   └── apk.py     #   ApkAutoremovablePackages (apk add --purge --simulate), ApkInstalledPackages (apk list --installed), ApkExplicitPackages (/etc/apk/world), ApkHeldPackages (/etc/apk/world version pins), ApkRepositories (/etc/apk/repositories), apk.autoremove (orphan purge) + apk.packages + apk.virtual (virtual package groups) + apk.hold (version pins) + apk.repository (/etc/apk/repositories lines) + their command descriptors
    ├── apt/
    │   └── apt.py     #   AptKeys (gpg keyrings), AptPackages (apt list --installed), AptSources (.list + deb822 .sources repositories), AptHeldPackages/AptPolicy, AptUpgradable (apt list --upgradable), AptAutoremovable (autoremove simulation), DebPackage (dpkg -I/-s), SimulateOperationWillChange (apt-get --dry-run counts), apt.deb/apt.dist_upgrade/apt.auto_remove/apt.key/apt.mark/apt.packages/apt.ppa/apt.repo/apt.sources_file/apt.update/apt.upgrade + their command descriptors
    ├── dnf/
    │   └── dnf.py     #   DnfPackages…, dnf.packages + its command descriptors
    ├── files/
    │   └── files.py   #   File/FileLink…, LocalSha1/RemoteFileSha1/FileContents, files.link/file/put/get/edit/move/copy + their command descriptors
    ├── firewalld/
    │   └── firewalld.py  # FirewalldStatus/FirewalldDefaultZone, FirewalldZones (--list-all-zones both scopes), FirewalldServices/FirewalldIpSets/FirewalldPolicies (--info-* marker loops), firewalld.zone/zone_service/port/interface/source/rich_rule/protocol/icmp_block/forward_port/masquerade/service/ipset/default_zone/lockdown/panic/reload + their command descriptors
    ├── git/
    │   └── git.py     #   GitBranch (git describe --all), GitConfig (git config -l), GitLocalCommit (git rev-parse), GitRemoteBranchCommit (git ls-remote), GitTag (git tag), GitTrackingBranch (git status --branch --porcelain), git.config (git config --global/--system/--local) + git.repo (git clone/fetch/checkout/pull/submodule) + git.worktree (git worktree add/remove/pull) + their command descriptors
    ├── gpg/
    │   └── gpg.py     #   GpgKey (gpg --with-colons), GpgKeyrings (find + KEYRING markers), GpgKeys/GpgSecretKeys (gpg --list-keys/--list-secret-keys --with-colons), GpgTrust (gpg --export-ownertrust), GpgKeyDetails (targeted gpg --list-keys metadata), GpgVerify (gpg --status-fd 1 --verify), gpg.key/gpg.dearmor/gpg.import_key/gpg.export_key/gpg.delete_key/gpg.gen_key/gpg.trust/gpg.refresh_keys + their command descriptors
    ├── hardware/
    │   └── hardware.py  # BlockDevices (df), CpuInfo (lscpu), Cpus (getconf), DiskStats (/proc/diskstats), DmiInfo (/sys/class/dmi/id), Ipv4Addrs/Ipv6Addrs/NetworkDevices (ip), LsBlk (lsblk), SmbiosInfo (dmidecode)
    ├── iptables/
    │   └── iptables.py  # IptablesChains/IptablesRules + Ip6 siblings (iptables-save), IptablesRulesOrdered (-L -n -v -x --line-numbers), IptablesTables (/proc/net/ip_tables_names), IptablesVersion (--version backend), IptablesModules (lsmod xt_*), IpSets (ipset list), IptablesPersistence (live-vs-snapshot), iptables.chain/policy/rule/flush/zero/save/restore/ipset + their command descriptors
    ├── lxd/
    │   └── lxd.py     #   LxdContainers (lxc list --fast, pyinfra parity), LxdServerInfo/LxdServerResources (daemon metadata + hardware), LxdProjects/LxdProjectConfig, LxdInstances/LxdInstanceConfig/LxdInstanceState/LxdInstanceSnapshots, LxdImages/LxdImageAliases, LxdNetworks, LxdProfiles, LxdStoragePools/LxdStorageVolumes, LxdOperations, lxd.container + instance/instance_state/instance_config/instance_snapshot/instance_copy + image/image_alias/project/profile/network/storage_pool/storage_volume/operation + their command descriptors
    ├── mysql/
    │   └── mysql.py   #   MysqlDatabases/MysqlUsers/MysqlUserGrants (SCHEMATA/mysql.user/SHOW GRANTS), MysqlBinaryLogs/MysqlColumns/MysqlConnections/MysqlEngines/MysqlIndexes/MysqlPlugins/MysqlReplication/MysqlStatus/MysqlTables/MysqlVariables (information_schema + SHOW), mysql.database/user/privileges/sql/dump/load + table/index/variable/plugin/role/replica/kill/optimize + their command descriptors
    ├── openrc/
    │   └── openrc.py  #   OpenrcStatus/OpenrcServices (rc-status), OpenrcEnabled (rc-update show -v), OpenrcRunlevels (ls -R /etc/runlevels), OpenrcVersion (openrc --version), openrc.service + openrc.runlevel + openrc.custom_runlevel (runlevel directories, stacking) + their command descriptors
    ├── postgresql/
    │   └── postgresql.py  # PostgresqlRoles/PostgresqlDatabases (pg_roles/pg_database), PostgresqlRoleMemberships/PostgresqlPrivileges (ACLs), PostgresqlSchemas/PostgresqlTables/PostgresqlIndexes/PostgresqlExtensions, PostgresqlSettings (pg_settings), PostgresqlTablespaces, PostgresqlReplication/PostgresqlConnections (pg_stat_*), PostgresqlPublications/PostgresqlSubscriptions, postgresql.database/role/sql/dump/load + privilege/membership/schema/extension/index/setting/tablespace/publication/subscription + their command descriptors
    ├── selinux/
    │   └── selinux.py  #   SEBoolean/Booleans (getsebool), FileContext (stat -c %C), FileContextMapping (semanage fcontext), SEPorts/SEPort (semanage port / sepolicy network), Mode (sestatus), SELogin/SEUser (semanage login/user), Modules (semodule -lfull), PermissiveDomains (semanage permissive), selinux.boolean/file_context/file_context_mapping/port + mode/login/module/permissive_domain/restorecon/autorelabel + their command descriptors
    ├── server/
    │   └── server.py  #   server.group/hostname/kill/modprobe/mount/timezone/wait_service + their command descriptors (the systemd surface relocated to systemd/, re-exported here; the Sysctl fact relocated to sysctl/)
    ├── sysctl/
    │   └── sysctl.py  #   Sysctl (sysctl -a runtime dump), SysctlFile/SysctlFiles (persistent config visibility), sysctl.setting (one parameter: runtime apply + keyed persistence edit) + sysctl.conf (whole drop-in file) + their command descriptors
    ├── systemd/
    │   └── systemd.py #   SystemdServiceState/Enabled, SystemdStatus/Enabled, SystemdUnitProperties/FailedUnits/Timers/UnitFiles/Version/SystemState/Dependencies, systemd.service/daemon_reload/mask/reset_failed/dropin/tmpfiles/sysusers/preset + their command descriptors
    └── ufw/
        └── ufw.py     #   UfwStatus (ufw status verbose: active/logging/default policies), UfwRules (ufw status numbered, v4 + v6 rows), UfwApplications (ufw app list), ufw.state/default_policy/logging/rule/reset + their command descriptors

examples/                     # runnable usage examples (not part of the library)
tests/simulated_host.py     # SimulatedHost (test framework, not part of the library)
tests/test_core.py          # model tests, incl. the httpd trace and determinism
docs/                       # this documentation
```

## Development

```bash
uv run pytest                        # run the tests
uv run --group docs mkdocs serve     # live-reloading docs on localhost:8000
uv run --group docs mkdocs build     # build the static site into site/
```

--->

[pyinfra]: https://pyinfra.com/
[uv]: https://docs.astral.sh/uv/
[asyncssh]: https://asyncssh.readthedocs.io/
