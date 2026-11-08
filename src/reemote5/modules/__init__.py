# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Self-contained module namespaces.

Each subpackage combines the facts and operations of one namespace in a
single source, so ``modules/apk/apk.py`` holds the apk fact classes
(``ApkAutoremovablePackages``, ``ApkExplicitPackages``, ``ApkHeldPackages``,
``ApkInstalledPackages``, ``ApkPackage``, ``ApkRepositories``) beside the
``autoremove``/``hold``/``packages``/``virtual`` operations and the typed
command descriptors they yield,
``modules/apt/apt.py`` holds the apt fact classes (``AptKeys``,
``AptPackages``, ``AptUpgradable``, ``AptAutoremovable``),
``modules/dnf/dnf.py`` holds the dnf fact classes
(``DnfPackages``, ``DnfRepositories``, ``DnfEnabledModules``,
``DnfDisabledModules``) beside the ``packages`` operation and the typed
command descriptors it yields, ``modules/files/files.py`` holds
``FileLink``/``FileExists``/``Link``/``SameFile`` beside the
``link``/``file`` operations,
``modules/firewalld/firewalld.py`` holds the ``FirewalldStatus``,
``FirewalldDefaultZone``, ``FirewalldZones``, ``FirewalldServices``,
``FirewalldIpSets`` and ``FirewalldPolicies`` facts beside the ``zone``,
``zone_service``, ``port``, ``interface``, ``source``, ``rich_rule``,
``protocol``, ``icmp_block``, ``forward_port``, ``masquerade``,
``service``, ``ipset``, ``default_zone``, ``lockdown``, ``panic`` and
``reload`` operations and the typed command descriptors they yield,
``modules/git/git.py`` holds the ``GitBranch``, ``GitConfig``,
``GitHeadDetached``, ``GitIsRepo``, ``GitLocalCommit``,
``GitRemoteBranchCommit``, ``GitRemoteUrl``, ``GitSshKeyConfigured``,
``GitStatus``, ``GitSubmodule``, ``GitTag`` and ``GitTrackingBranch``
facts beside the ``config``, ``repo`` and ``worktree`` operations,
``modules/gpg/gpg.py`` holds the ``GpgKey``, ``GpgKeyrings``, ``GpgKeys``
and ``GpgSecretKeys`` facts,
``modules/hardware/hardware.py`` holds the ``BlockDevices``, ``CpuInfo``,
``Cpus``, ``DiskStats``, ``DmiInfo``, ``Ipv4Addrs``, ``Ipv6Addrs``,
``LsBlk``, ``NetworkDevices`` and ``SmbiosInfo`` facts,
``modules/iptables/iptables.py`` holds the ``IptablesChains``,
``IptablesRules``, ``IptablesRulesOrdered``, ``IptablesTables``,
``IptablesVersion``, ``IptablesModules``, ``IpSets`` and
``IptablesPersistence`` facts (each with an ``Ip6tables`` IPv6 sibling
where applicable) beside the ``chain``, ``policy``, ``rule``, ``flush``,
``zero``, ``save``, ``restore`` and ``ipset`` operations and the typed
command descriptors they yield,
``modules/lxd/lxd.py`` holds the ``LxdContainers``, ``LxdImages``,
``LxdImageAliases``, ``LxdInstances``, ``LxdInstanceConfig``,
``LxdInstanceState``, ``LxdInstanceSnapshots``, ``LxdNetworks``,
``LxdStoragePools``, ``LxdStorageVolumes``, ``LxdProfiles``,
``LxdProjects``, ``LxdProjectConfig``, ``LxdServerInfo``,
``LxdServerResources`` and ``LxdOperations`` facts beside the
``container``, ``instance``, ``instance_state``, ``instance_config``,
``instance_snapshot``, ``instance_copy``, ``image``, ``image_alias``,
``project``, ``network``, ``storage_pool``, ``storage_volume``,
``profile`` and ``operation`` operations and the typed command
descriptors they yield,
``modules/mysql/mysql.py`` holds the ``MysqlBinaryLogs``, ``MysqlColumns``,
``MysqlConnections``, ``MysqlDatabases``, ``MysqlEngines``,
``MysqlIndexes``, ``MysqlPlugins``, ``MysqlReplication``, ``MysqlStatus``,
``MysqlTables``, ``MysqlUserGrants``, ``MysqlUsers`` and ``MysqlVariables``
facts beside the ``database``, ``dump``, ``index``, ``kill``, ``load``,
``optimize``, ``plugin``, ``privileges``, ``replica``, ``role``, ``sql``,
``table``, ``user`` and ``variable`` operations and the typed command
descriptors they yield,
``modules/openrc/openrc.py`` holds the ``OpenrcEnabled``,
``OpenrcRunlevels``, ``OpenrcServices``, ``OpenrcStatus`` and
``OpenrcVersion`` facts beside the ``service``, ``runlevel`` and
``custom_runlevel`` operations and the typed command descriptors they
yield,
``modules/postgresql/postgresql.py`` holds the ``PostgresqlConnections``,
``PostgresqlDatabases``, ``PostgresqlExtensions``, ``PostgresqlIndexes``,
``PostgresqlPrivileges``, ``PostgresqlPublications``,
``PostgresqlReplication``, ``PostgresqlRoleMemberships``,
``PostgresqlRoles``, ``PostgresqlSchemas``, ``PostgresqlSettings``,
``PostgresqlSubscriptions``, ``PostgresqlTables`` and
``PostgresqlTablespaces`` facts beside the ``database``, ``role``,
``sql``, ``dump``, ``load``, ``privilege``, ``membership``, ``schema``,
``extension``, ``index``, ``setting``, ``tablespace``, ``publication``
and ``subscription`` operations and the typed command descriptors they
yield,
``modules/selinux/selinux.py`` holds the ``SEBoolean``, ``Booleans``,
``FileContext``, ``FileContextMapping``, ``SEPorts``, ``SEPort``, ``Mode``,
``SELogin``, ``SEUser``, ``Modules`` and ``PermissiveDomains`` facts beside
the ``boolean``, ``file_context``, ``file_context_mapping``, ``port``,
``mode``, ``login``, ``module``, ``permissive_domain``, ``restorecon`` and
``autorelabel`` operations and the typed command descriptors they yield,
``modules/systemd/systemd.py`` holds the ``SystemdServiceState``,
``SystemdServiceEnabled``, ``SystemdStatus``, ``SystemdEnabled``,
``SystemdUnitProperties``, ``SystemdFailedUnits``, ``SystemdTimers``,
``SystemdUnitFiles``, ``SystemdVersion``, ``SystemdSystemState`` and
``SystemdDependencies`` facts beside the ``service``, ``daemon_reload``,
``mask``, ``reset_failed``, ``dropin``, ``tmpfiles``, ``sysusers`` and
``preset`` operations and the typed command descriptors they yield,
``modules/sysctl/sysctl.py`` holds the ``Sysctl``, ``SysctlFile`` and
``SysctlFiles`` facts beside the ``setting`` and ``conf`` operations and
the typed command descriptors they yield,
``modules/ufw/ufw.py`` holds the ``UfwStatus``, ``UfwRules`` and
``UfwApplications`` facts beside the ``state``, ``default_policy``,
``rule``, ``logging`` and ``reset`` operations and the typed command
descriptors they yield, and
``modules/server/server.py`` holds the ``wait_service`` operation (which
polls the relocated ``systemd.SystemdServiceState`` fact). The subpackages
are re-exported here, so a deploy can keep writing
``from reemote5.modules import apk, apt, dnf, files, firewalld, git, gpg, hardware, iptables, lxd, mysql, openrc, postgresql, selinux, sysctl, systemd, ufw, server``
and read ``apk.ApkInstalledPackages`` / ``apt.AptKeys`` / ``dnf.packages`` /
``files.link`` / ``firewalld.FirewalldZones`` / ``git.GitBranch`` / ``gpg.GpgKeys`` / ``hardware.BlockDevices`` /
``iptables.IptablesChains`` / ``lxd.LxdInstances`` / ``mysql.MysqlDatabases`` / ``openrc.OpenrcStatus`` / ``postgresql.PostgresqlRoles`` /
``selinux.Mode`` / ``sysctl.setting`` / ``systemd.service`` / ``ufw.UfwRules`` / ``server.wait_service``.
"""

from . import (
    apk,
    apt,
    dnf,
    files,
    firewalld,
    git,
    gpg,
    hardware,
    iptables,
    lxd,
    mysql,
    openrc,
    postgresql,
    selinux,
    server,
    sysctl,
    systemd,
    ufw,
)

__all__ = [
    "apk",
    "apt",
    "dnf",
    "files",
    "firewalld",
    "git",
    "gpg",
    "hardware",
    "iptables",
    "lxd",
    "mysql",
    "openrc",
    "postgresql",
    "selinux",
    "server",
    "sysctl",
    "systemd",
    "ufw",
]
