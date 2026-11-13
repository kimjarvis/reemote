# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Test-framework stand-in for a real machine.

``SimulatedHost`` is part of the *test framework*, not of the library, so it
lives here in ``tests/`` rather than in ``src/reemote/``. Tests and examples
inject it via ``run``'s ``host_factory`` argument (or hand it to
``run_prepare`` directly); the library never imports or constructs one.

The library relies only on the seams this class provides at the points where
a real asyncssh transport is missing:

- ``execute_command(cmd)``: applies the simulated effect of a command
  descriptor to the in-memory state. ``reemote.drivers.send_command``
  forwards to it when the host provides it. Without the seam the same call
  takes the real-transport path instead, dispatching on the descriptor kind:
  ``transfer`` for a ``TransferCommand``, ``wait`` for a ``WaitCommand``, and
  the rendered shell over asyncssh for anything else.
- ``packages`` / ``links`` / ``existing_files`` / ``file_stats`` /
  ``file_flags`` / ``directory_stats`` / ``directory_entries`` /
  ``link_stats`` / ``socket_stats`` / ``same_files`` /
  ``find_results`` / ``found_files`` / ``grep_lines`` / ``grep_files`` /
  ``remote_files`` /
  ``repositories`` / ``repo_list`` / ``enabled_modules`` / ``disabled_modules``
  / ``installed_modules`` / ``installed_groups`` / ``available_groups``
  / ``rpm_files`` / ``rpm_packages`` / ``rpm_gpg_keys`` / ``services`` / ``enabled_services``
  / ``apk_autoremovable`` / ``apk_explicit_packages`` / ``apk_held_packages`` / ``apk_package_info`` / ``apk_packages`` / ``apk_repositories`` / ``apt_autoremovable`` / ``apt_held_packages`` / ``apt_keys`` / ``apt_packages`` / ``apt_policy`` / ``apt_simulations`` / ``apt_sources`` / ``apt_upgradable`` / ``arch`` / ``authorized_keys`` / ``block_devices`` / ``cpu_info`` / ``cpus`` / ``deb_files``
  / ``date`` / ``disk_stats`` / ``dmi_info`` / ``os_env``
  / ``firewalld_default_zone``
  / ``firewalld_ipsets`` / ``firewalld_lockdown`` / ``firewalld_panic_mode``
  / ``firewalld_permanent_ipsets`` / ``firewalld_permanent_policies``
  / ``firewalld_permanent_zones`` / ``firewalld_policies``
  / ``firewalld_services`` / ``firewalld_status`` / ``firewalld_zones``
  / ``flatpak_package_info`` / ``git_branches``
  / ``git_configs`` / ``git_head_detached`` / ``git_local_commits``
  / ``git_remote_commits`` / ``git_remote_urls`` / ``git_repos``
  / ``git_ssh_authenticated`` / ``git_statuses`` / ``git_submodules``
  / ``git_tags`` / ``git_tracking_branches`` / ``gpg_key``
  / ``gpg_key_details`` / ``gpg_keyrings``
  / ``gpg_keys`` / ``gpg_secret_keys`` / ``gpg_trust`` / ``gpg_verify``
  / ``os_groups`` / ``has_gui``
  / ``home_directories`` / ``os_hostname`` / ``ip6tables_chains``
  / ``ip6tables_ordered_rules`` / ``ip6tables_persistence`` / ``ip6tables_rules``
  / ``ip6tables_tables`` / ``ip6tables_version`` / ``ipsets``
  / ``iptables_chains`` / ``iptables_modules`` / ``iptables_ordered_rules``
  / ``iptables_persistence`` / ``iptables_rules`` / ``iptables_tables``
  / ``iptables_version`` / ``ipv4_addrs`` / ``ipv6_addrs``
  / ``os_kernel``
  / ``kernel_modules`` / ``os_kernel_version`` / ``login_records``
  / ``linux_distribution``
  / ``linux_gui`` / ``listening_ports`` / ``locales`` / ``lsb_release``
  / ``lsblk_devices``
  / ``lxd_containers`` / ``lxd_image_aliases`` / ``lxd_images``
  / ``lxd_instance_configs`` / ``lxd_instance_snapshots``
  / ``lxd_instance_states`` / ``lxd_instances`` / ``lxd_networks``
  / ``lxd_operations`` / ``lxd_profiles`` / ``lxd_project_configs``
  / ``lxd_projects`` / ``lxd_server_info`` / ``lxd_server_resources``
  / ``lxd_storage_pools`` / ``lxd_storage_volumes`` / ``mounts``
  / ``mysql_binary_logs`` / ``mysql_columns`` / ``mysql_connections``
  / ``mysql_databases`` / ``mysql_engines`` / ``mysql_indexes``
  / ``mysql_plugins`` / ``mysql_replication`` / ``mysql_status``
  / ``mysql_tables`` / ``mysql_user_grants`` / ``mysql_users``
  / ``mysql_variables``
  / ``network_devices`` / ``openrc_runlevels`` / ``openrc_service_states``
  / ``openrc_version``
  / ``processes``
  / ``postgres_connections`` / ``postgres_databases`` / ``postgres_extensions``
  / ``postgres_indexes`` / ``postgres_privileges`` / ``postgres_publications``
  / ``postgres_replication`` / ``postgres_role_memberships`` / ``postgres_roles``
  / ``postgres_schemas`` / ``postgres_settings`` / ``postgres_subscriptions``
  / ``postgres_tables`` / ``postgres_tablespaces``
  / ``selinux_booleans`` / ``selinux_file_contexts``
  / ``selinux_fcontext_mappings`` / ``selinux_ports`` / ``selinux_mode``
  / ``selinux_logins`` / ``selinux_users`` / ``selinux_modules``
  / ``selinux_permissive_domains``
  / ``smbios_info`` / ``snap_package_info`` / ``sysctls`` / ``systemd_dependencies`` / ``systemd_failed_units``
  / ``systemd_system_state`` / ``systemd_timers`` / ``systemd_unit_files``
  / ``systemd_unit_properties`` / ``systemd_version`` / ``os_timezone`` / ``os_user`` / ``os_users``
  / ``ufw_applications`` / ``ufw_rules`` / ``ufw_status``
  / ``uptime_seconds`` / ``command_paths``:
  the state the library's facts (``DnfPackages``, ``FileLink``, ``FileExists``,
  ``File``, ``Directory``, ``Link``, ``Socket``, ``SameFile``, ``FileFlags``,
  ``FindFiles``, ``FindDirectories``, ``FindLinks``, ``FindInFile``,
  ``FindInFiles``,
  ``FindFilesInfo``, ``DirectoryEntries``, ``RemoteFileSha1``, ``Sha256File``,
  ``Sha384File``, ``Md5File``, ``FileContents``, ``DnfRepoFile``, ``DnfRepositories``,
  ``DnfRepoList``,
  ``DnfEnabledModules``, ``DnfDisabledModules``, ``DnfInstalledModules``,
  ``DnfGroups``, ``DnfAvailableGroups``, ``RpmPackage``, ``RpmGpgKeys``,
  ``SystemdServiceState``, ``SystemdServiceEnabled``, ``SystemdStatus``, ``SystemdEnabled``, ``SystemdUnitProperties``, ``SystemdFailedUnits``, ``SystemdTimers``, ``SystemdUnitFiles``, ``SystemdVersion``, ``SystemdSystemState``, ``SystemdDependencies``, ``ApkAutoremovablePackages``, ``ApkExplicitPackages``, ``ApkHeldPackages``, ``ApkInstalledPackages``, ``ApkPackage``, ``ApkRepositories``, ``ApkRepositoriesFile``, ``AptAutoremovable``, ``AptHeldPackages``, ``AptKeys``, ``AptPackages``, ``AptPolicy``, ``AptSources``, ``AptSourcesFile``, ``AptUpgradable``, ``Arch``,
  ``AuthorizedKeys``, ``AuthorizedKeysFile``, ``BlockDevices``, ``CpuInfo``, ``Cpus``, ``Date``, ``DebPackage``,
  ``DiskStats``, ``DmiInfo``, ``Env``, ``EtcHosts`` (parses the shared
  ``remote_files`` seam at the hosts-file path), ``FirewalldStatus``, ``FirewalldDefaultZone``,
  ``FirewalldZones``, ``FirewalldServices``, ``FirewalldIpSets``,
  ``FirewalldPolicies`` (the firewalld-local ``_FirewalldLockdown`` and
  ``_FirewalldPanicMode`` probes read the ``firewalld_lockdown`` and
  ``firewalld_panic_mode`` seams), ``FlatpakPackage``, ``FlatpakPackages``,
  ``GitBranch``, ``GitConfig``,
  ``GitHeadDetached``, ``GitIsRepo``, ``GitLocalCommit``,
  ``GitRemoteBranchCommit``, ``GitRemoteUrl``, ``GitSshKeyConfigured``,
  ``GitStatus``, ``GitSubmodule``, ``GitTag``,
  ``GitTrackingBranch``, ``GpgKey``, ``GpgKeyDetails``, ``GpgKeyrings``,
  ``GpgKeys``, ``GpgSecretKeys``, ``GpgTrust``, ``GpgVerify`` (the gpg-local
  ``_KeyringFileExists`` probe reads the shared ``existing_files`` seam),
  ``Groups``,
  ``HasGui``, ``Home``, ``Hostname``, ``Ip6tablesChains``,
  ``Ip6tablesRules``, ``Ip6tablesRulesOrdered``, ``Ip6tablesTables``,
  ``Ip6tablesVersion``, ``IpSets``, ``IptablesChains``, ``IptablesRules``,
  ``IptablesRulesOrdered``, ``IptablesTables``, ``IptablesVersion``,
  ``IptablesModules``, ``IptablesPersistence``, ``Ip6tablesPersistence``,
  ``Ipv4Addrs``, ``Ipv6Addrs``, ``Kernel``,
  ``KernelModules``, ``KernelVersion``, ``Last``,
  ``LinuxDistribution``, ``LinuxGui``, ``LinuxName``, ``Locales``,
  ``LsbRelease``, ``LsBlk``, ``LxdContainers``, ``LxdImageAliases``,
  ``LxdImages``, ``LxdInstanceConfig``, ``LxdInstanceSnapshots``,
  ``LxdInstanceState``, ``LxdInstances``, ``LxdNetworks``,
  ``LxdOperations``, ``LxdProfiles``, ``LxdProjectConfig``,
  ``LxdProjects``, ``LxdServerInfo``, ``LxdServerResources``,
  ``LxdStoragePools``, ``LxdStorageVolumes``, ``Mounts``, ``MysqlBinaryLogs``, ``MysqlColumns``,
  ``MysqlConnections``, ``MysqlDatabases``, ``MysqlEngines``, ``MysqlIndexes``,
  ``MysqlPlugins``, ``MysqlReplication``, ``MysqlStatus``, ``MysqlTables``,
  ``MysqlUserGrants``, ``MysqlUsers``, ``MysqlVariables``, ``NetworkDevices``,
  ``OpenrcConfigFile``, ``OpenrcEnabled``,
  ``OpenrcRunlevels``, ``OpenrcServices``, ``OpenrcStatus``, ``OpenrcVersion``,
  ``Port``, ``Processes``,
  ``PostgresqlConnections``, ``PostgresqlDatabases``,
  ``PostgresqlExtensions``, ``PostgresqlIndexes``, ``PostgresqlPrivileges``,
  ``PostgresqlPublications``, ``PostgresqlReplication``,
  ``PostgresqlRoleMemberships``, ``PostgresqlRoles``, ``PostgresqlSchemas``,
  ``PostgresqlSettings``, ``PostgresqlSubscriptions``, ``PostgresqlTables``,
  ``PostgresqlTablespaces``,
  ``SEBoolean``, ``Booleans``, ``FileContext``, ``FileContextMapping``,
  ``SEPorts``, ``SEPort``, ``Mode``, ``SELogin``, ``SEUser``, ``Modules``,
  ``PermissiveDomains``, ``SelinuxConfigFile`` (the selinux-local ``_Which``
  and ``_AutorelabelPending`` probes read the shared ``command_paths`` and
  ``existing_files`` seams),
  ``SnapPackage``, ``SnapPackages``,
  ``SmbiosInfo``, ``SimulateOperationWillChange``, ``Sysctl``, ``SysctlFile``,
  ``SysctlFiles`` (the two persistence reads project the shared
  ``remote_files`` seam onto the standard sysctl paths), ``Timezone``,
  ``UfwApplications``, ``UfwRules``, ``UfwStatus``,
  ``Uptime``, ``User``, ``Users`` (the server-local ``_HomeDirectory`` and
  ``_HomeIsLink`` home probes read the shared ``directory_stats`` and
  ``link_stats`` seams), ``Which``) read as a
  stand-in for gathering over SSH.

Holding this state is what makes the "fresh fact cache per lockstep step"
property observable in the tests and the demo: ``send_command`` mutates the
state when an operation is changed, and the next operation's fact reads see
the mutation.
"""

import hashlib
import io
import posixpath
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from reemote.commands import Command, StringCommand, WaitTimeoutError
from reemote.hosts import CommandError, Host
from reemote.modules.apk.apk import (
    ApkHoldCommand,
    ApkInstallCommand,
    ApkPackageInfo,
    ApkPackageVersion,
    ApkPurgeCommand,
    ApkRemoveCommand,
    ApkRepoInstallCommand,
    ApkRepositories,
    ApkRepository,
    ApkRepoUploadCommand,
    ApkUpgradeCommand,
    ApkVirtualCommand,
    _split_constraint,
)
from reemote.modules.apt.apt import (
    AptAutoRemoveCommand,
    AptDistUpgradeCommand,
    AptInstallCommand,
    AptInstallFixCommand,
    AptKey,
    AptKeyInstallCommand,
    AptKeyUploadCommand,
    AptMarkHoldCommand,
    AptMarkUnholdCommand,
    AptPackageInfo,
    AptPackagePolicy,
    AptPackageUpgrade,
    AptPpaCommand,
    AptRemoveCommand,
    AptRepoInstallCommand,
    AptRepoUploadCommand,
    AptSimulationDict,
    AptSource,
    AptSources,
    AptSourcesFileInstallCommand,
    AptSourcesFileRemoveCommand,
    AptSourcesFileUploadCommand,
    AptUpdateCommand,
    AptUpgradeCommand,
    DebInstallCommand,
    DebPackageInfo,
)
from reemote.modules.dnf.dnf import (
    DnfCleanCommand,
    DnfDowngradeCommand,
    DnfInstallCommand,
    DnfKeyImportCommand,
    DnfKeyRemoveCommand,
    DnfKeyUploadCommand,
    DnfMakecacheCommand,
    DnfModuleDisableCommand,
    DnfModuleEnableCommand,
    DnfRemoveCommand,
    DnfRepoInstallCommand,
    DnfRepoListEntry,
    DnfRepoRemoveCommand,
    DnfRepository,
    DnfRepoUploadCommand,
    DnfUpdateCommand,
    RpmGpgKey,
    RpmInstallCommand,
    RpmPackageInfo,
)
from reemote.modules.files.files import (
    ChattrCommand,
    ChmodCommand,
    ChownCommand,
    CopyCommand,
    DirectoryEntry,
    FileCommand,
    FoundFile,
    GetCommand,
    InstallCommand,
    InstallSpoolCommand,
    InstallValidateCommand,
    LinkCommand,
    MkdirCommand,
    MoveCommand,
    PutCommand,
    RemoveTreeCommand,
    read_source_bytes,
    write_dest_bytes,
)
from reemote.modules.firewalld.firewalld import (
    _SERVICE_MEMBER_FIELDS,
    _ZONE_MEMBER_FIELDS,
    FirewalldDefaultZoneCommand,
    FirewalldIpSet,
    FirewalldIpsetCommand,
    FirewalldIpsetEntryCommand,
    FirewalldLockdownCommand,
    FirewalldMasqueradeCommand,
    FirewalldPanicCommand,
    FirewalldPolicy,
    FirewalldReloadCommand,
    FirewalldService,
    FirewalldServiceCommand,
    FirewalldServiceEntryCommand,
    FirewalldServiceTextCommand,
    FirewalldStatusInfo,
    FirewalldZone,
    FirewalldZoneCommand,
    FirewalldZoneEntryCommand,
    FirewalldZoneTargetCommand,
    FirewalldZoneTextCommand,
    _copy_record,
)
from reemote.modules.flatpak.flatpak import (
    FlatpakInstallCommand,
    FlatpakPackageInfo,
    FlatpakRemoveCommand,
)
from reemote.modules.git.git import (
    GitCheckoutCommand,
    GitCloneCommand,
    GitConfigCommand,
    GitFetchCommand,
    GitPullCommand,
    GitRemoteSetUrlCommand,
    GitRepoPullCommand,
    GitStatusSummary,
    GitSubmoduleStatus,
    GitSubmoduleUpdateCommand,
    GitTagInfo,
    GitWorktreeAddCommand,
    GitWorktreeRemoveCommand,
)
from reemote.modules.gpg.gpg import (
    GpgDearmorCommand,
    GpgDeleteKeyCommand,
    GpgExportCommand,
    GpgGenKeyCommand,
    GpgImportCommand,
    GpgImportUploadCommand,
    GpgKeyDeleteKeysCommand,
    GpgKeyEmptyCleanupCommand,
    GpgKeyInfo,
    GpgKeyInstallCommand,
    GpgKeyMetadata,
    GpgKeyRemoveCommand,
    GpgKeyring,
    GpgKeyTrust,
    GpgKeyUploadCommand,
    GpgRefreshKeysCommand,
    GpgTrustCommand,
    GpgVerification,
)
from reemote.modules.hardware.hardware import (
    BlockDevice,
    DiskStat,
    LsBlkNode,
    NetworkDevice,
)
from reemote.modules.iptables.iptables import (
    IpSet,
    IpsetCreateCommand,
    IpsetDestroyCommand,
    IpsetEntryCommand,
    IptablesChainCommand,
    IptablesFlushCommand,
    IptablesOrderedRule,
    IptablesPolicyCommand,
    IptablesRestoreCommand,
    IptablesRule,
    IptablesRuleCommand,
    IptablesSaveCommand,
    IptablesZeroCommand,
    _normalize_host,
    _parse_iptables_rule,
)
from reemote.modules.lxd.lxd import (
    LxdContainer,
    LxdCopyCommand,
    LxdDeleteCommand,
    LxdDeviceCommand,
    LxdImage,
    LxdImageAlias,
    LxdImageAliasCommand,
    LxdImageCommand,
    LxdInstance,
    LxdInstanceStateData,
    LxdLaunchCommand,
    LxdNetwork,
    LxdNetworkCommand,
    LxdOperation,
    LxdOperationCancelCommand,
    LxdOperationWaitCommand,
    LxdProfile,
    LxdProfileAssignCommand,
    LxdProfileCommand,
    LxdProject,
    LxdProjectCommand,
    LxdRestoreCommand,
    LxdServerInfoData,
    LxdServerResourcesData,
    LxdSetCommand,
    LxdSnapshot,
    LxdSnapshotCommand,
    LxdStateCommand,
    LxdStoragePool,
    LxdStoragePoolCommand,
    LxdStorageVolume,
    LxdStorageVolumeCommand,
    _config_pairs,
)
from reemote.modules.mysql.mysql import (
    MysqlBinaryLog,
    MysqlColumn,
    MysqlConnection,
    MysqlDatabase,
    MysqlDatabaseCommand,
    MysqlDumpCommand,
    MysqlEngine,
    MysqlIndex,
    MysqlIndexCommand,
    MysqlKillCommand,
    MysqlLoadCommand,
    MysqlOptimizeCommand,
    MysqlPlugin,
    MysqlPluginCommand,
    MysqlPrivilegesCommand,
    MysqlReplicaCommand,
    MysqlReplicaStatus,
    MysqlRoleCommand,
    MysqlSqlCommand,
    MysqlTable,
    MysqlTableCommand,
    MysqlUser,
    MysqlUserCommand,
    MysqlVariableCommand,
)
from reemote.modules.openrc.openrc import (
    OpenrcConfigInstallCommand,
    OpenrcConfigUploadCommand,
    RcServiceCommand,
    RcUpdateCommand,
    RunlevelCommand,
)
from reemote.modules.postgresql.postgresql import (
    _PRIVILEGE_ALIASES,
    PostgresqlConnection,
    PostgresqlDatabase,
    PostgresqlDatabaseCommand,
    PostgresqlDumpCommand,
    PostgresqlExtension,
    PostgresqlExtensionCommand,
    PostgresqlIndex,
    PostgresqlIndexCommand,
    PostgresqlLoadCommand,
    PostgresqlMembershipCommand,
    PostgresqlPrivilege,
    PostgresqlPrivilegeCommand,
    PostgresqlPublication,
    PostgresqlPublicationCommand,
    PostgresqlReplicationClient,
    PostgresqlRole,
    PostgresqlRoleCommand,
    PostgresqlRoleMembership,
    PostgresqlSchema,
    PostgresqlSchemaCommand,
    PostgresqlSetting,
    PostgresqlSettingCommand,
    PostgresqlSqlCommand,
    PostgresqlSubscription,
    PostgresqlSubscriptionCommand,
    PostgresqlTable,
    PostgresqlTablespace,
    PostgresqlTablespaceCommand,
    _normalize_object,
)
from reemote.modules.selinux.selinux import (
    AUTORELABEL_PATH,
    AutorelabelCommand,
    ChconCommand,
    ModeInfo,
    Module,
    RestoreconCommand,
    SeContext,
    SelinuxConfigInstallCommand,
    SelinuxConfigUploadCommand,
    SeLogin,
    SemanageFcontextCommand,
    SemanageLoginCommand,
    SemanagePermissiveCommand,
    SemanagePortCommand,
    SemoduleEnableCommand,
    SemoduleInstallCommand,
    SemoduleRemoveCommand,
    SetenforceCommand,
    SetseboolCommand,
    SeUser,
)
from reemote.modules.server.server import (
    AuthorizedKeys,
    AuthorizedKeysInstallCommand,
    AuthorizedKeysUploadCommand,
    EtcHostsInstallCommand,
    EtcHostsUploadCommand,
    GroupAddCommand,
    GroupDelCommand,
    KillCommand,
    LocaleGenCommand,
    LocaleInstallCommand,
    LocaleUploadCommand,
    ModprobeCommand,
    MountCommand,
    Port,
    PortInfo,
    SecurityLimitInstallCommand,
    SecurityLimitUploadCommand,
    SetHostnameCommand,
    SetTimezoneCommand,
    UserAddCommand,
    UserDelCommand,
    UserModCommand,
    WaitForFactCommand,
    _HomeChownCommand,
    _HomeMkdirCommand,
    _KeysChmodCommand,
    _KeysTouchCommand,
)
from reemote.modules.snap.snap import (
    SnapInstallCommand,
    SnapPackageInfo,
    SnapRefreshCommand,
    SnapRemoveCommand,
)
from reemote.modules.sysctl.sysctl import (
    Sysctl,
    SysctlFileInstallCommand,
    SysctlFileRemoveCommand,
    SysctlFileUploadCommand,
    SysctlReloadCommand,
    SysctlSetCommand,
    _parse_conf,
)
from reemote.modules.systemd.systemd import (
    SystemctlCommand,
    SystemdFailedUnit,
    SystemdFileInstallCommand,
    SystemdFileRemoveCommand,
    SystemdFileUploadCommand,
    SystemdServiceState,
    SystemdSysusersCommand,
    SystemdTimer,
    SystemdTmpfilesCommand,
    SystemdUnitFile,
)
from reemote.modules.ufw.ufw import (
    UfwDefaultPolicyCommand,
    UfwLoggingCommand,
    UfwResetCommand,
    UfwRule,
    UfwRuleCommand,
    UfwStateCommand,
    UfwStatusInfo,
    _command_rule_key,
    _is_v6,
    _rule_key,
)


@dataclass
class SimulatedHost(Host):
    """In-memory stand-in for a real machine.

    Inherits the inventory fields from ``Host`` and adds the state facts
    read and ``execute_command`` mutates. Only ever constructed by
    tests/examples (injected via ``run``'s ``host_factory``), never by the
    library itself.
    """

    # Installed dnf packages -- the state store the fact reads, kept as the
    # installed-name -> version list the mutators edit (e.g. ``nginx`` ->
    # ``[""]`` for an unpinned install, ``["1.24.0-1.el9"]`` for a pinned or
    # ``.rpm`` one), which ``DnfPackages.process`` projects into the sorted
    # ``DnfPackageInfo`` entries it returns (the ``apt_packages`` precedent).
    packages: dict[str, list[str]] = field(default_factory=dict)
    # Symlink path -> target: the store the cheap ``FileLink`` readlink
    # probe answers from, maintained by ``make_link``/``remove_link``.
    links: dict[str, str] = field(default_factory=dict)  # path -> target
    # Pairs of paths naming one and the same inode, backing ``SameFile``
    # (the hard-link half of ``files.link``'s idempotence): ``ln`` without
    # ``-s`` records the ``(link path, target)`` pair and a test can seed
    # pairs directly. The ``test -ef`` operator is symmetric, so the fact
    # looks a pair up in either order.
    same_files: set[tuple[str, str]] = field(default_factory=set)
    existing_files: set[str] = field(default_factory=set)
    # Remote path -> the content bytes the simulated remote filesystem holds
    # there. This single store backs both transfer directions: ``RemoteFileSha1``
    # derives its digest from it, ``FileContents`` decodes its text back out,
    # a simulated ``files.put`` writes the source
    # stream's bytes into it, and a simulated ``files.get`` reads them back out
    # into the destination stream. The ``dnf.key``/``dnf.repo``/``apt.key``/
    # ``apt.sources_file``/``gpg.key``/``gpg.import_key``/``files.install``/
    # ``server.user_authorized_keys``/``server.etc_hosts``/``server.locale``/
    # ``server.security_limit``/
    # ``openrc.config``/``selinux.config``
    # spool uploads land
    # here too:
    # ``import_key``/``install_repo_file``/``install_apt_key``/
    # ``install_apt_sources_file``/``install_gpg_key``/``import_gpg_key``/
    # ``install_file``/``install_authorized_keys_file``/``install_etc_hosts_file``/
    # ``install_locale_gen_file``/``install_security_limits_file``/
    # ``install_openrc_config_file``/
    # ``install_selinux_config_file`` pop
    # the spool entry, and
    # the install mutators store the content bytes back at their dest path
    # (``dearmor_gpg_key`` copies between two remote paths in the same store,
    # ``export_gpg_key`` writes a placeholder export file into it).
    # Storing content (not just a hash) is what
    # lets a put then a get of the same path round-trip, and what makes a repeat
    # transfer a no-op: execute_command mutates it, and the next step's fresh
    # fact cache sees the mutation.
    remote_files: dict[str, bytes] = field(default_factory=dict)
    # Spool paths whose ``files.install`` validation is simulated to fail: the
    # ``InstallValidateCommand`` case raises ``CommandError`` for a spool named
    # here, so a test can drive the abort path and prove a failed ``validate``
    # stops the install and leaves the destination untouched (the real
    # command's non-zero exit does the same through ``send_command``'s
    # fail-fast). Empty by default -- validation passes, as a correct config
    # would, and the simulation models no config parser to run for real.
    fail_validate: set[str] = field(default_factory=set)
    # Configured dnf repositories (the ``DnfRepository`` entries
    # ``DnfRepositories`` returns -- repoid/name, the source filename and the
    # open-ended config options in an ``options`` sub-dict).
    repositories: list[DnfRepository] = field(default_factory=list)
    # Runtime repository list (``dnf repolist --all``), backing ``DnfRepoList``:
    # each entry is a ``DnfRepoListEntry`` dict ({repoid, name, status}).
    repo_list: list[DnfRepoListEntry] = field(default_factory=list)
    # Enabled/installed dnf modules -- name -> stream state stores that
    # ``DnfEnabledModules``/``DnfInstalledModules`` project into sorted
    # ``DnfModule`` entries; ``enable_module`` mutates the enabled store.
    enabled_modules: dict[str, str] = field(default_factory=dict)  # name -> stream
    # Disabled dnf module names -- the state store ``DnfDisabledModules``
    # projects into ``DnfModule`` entries with stream "" (a simulated
    # ``dnf module disable`` names no stream; the real listing's [x] rows
    # always carry one). ``enable_module``/``disable_module`` mutate it.
    disabled_modules: list[str] = field(default_factory=list)  # disabled module names
    installed_modules: dict[str, str] = field(default_factory=dict)  # name -> stream
    # Installed/available package-group names (``dnf group list``), backing
    # ``DnfGroups``/``DnfAvailableGroups`` -- name stores the facts project
    # into sorted ``DnfGroup`` entries. Named distinctly from ``Host.group``
    # (the inventory classifier) so the two are never confused.
    installed_groups: set[str] = field(default_factory=set)
    available_groups: set[str] = field(default_factory=set)
    # ``.rpm`` files present on the host (path -> ``RpmPackageInfo``) and the
    # packages installed from them (name -> version), backing ``RpmPackage``.
    # ``dnf.rpm`` installs the file's package: ``install_rpm`` copies the file's
    # name/version into ``rpm_packages`` (and the name/version into
    # ``packages``), so a repeat is a no-op and a removal (``present=False``)
    # clears both.
    rpm_files: dict[str, RpmPackageInfo] = field(default_factory=dict)
    rpm_packages: dict[str, str] = field(default_factory=dict)
    # GPG key content imported via ``dnf.key`` (the raw bytes of each imported
    # key). The import direction is non-idempotent and reads no fact, so
    # nothing in the library reads this back; it exists so a test can observe
    # that the simulated ``rpm --import`` ran. ``import_key`` fills it and
    # clears the ``/tmp`` spool entry the paired ``DnfKeyUploadCommand`` wrote
    # into ``remote_files``.
    imported_keys: set[bytes] = field(default_factory=set)
    # GPG keys imported into the rpm keyring (the ``RpmGpgKey`` entries --
    # keyid/release/summary of each ``gpg-pubkey`` pseudo-package --
    # ``RpmGpgKeys`` returns), backing ``dnf.key``'s ``present=False``
    # idempotence. Kept separate from ``imported_keys`` (raw key bytes, an
    # observation-only store) because the fact reports rpmdb metadata, not
    # key material. Defaults to [] -- a simulated machine with no imported
    # keys. A test seeds it directly with realistic keyids;
    # ``import_key`` also appends a stub entry derived from the content hash
    # (the ``import_gpg_key`` precedent -- the simulation models no OpenPGP
    # parsing, so the stub keyid is stable across phases but not the real
    # key's id), and ``remove_rpm_gpg_key`` (the simulated ``rpm -e``)
    # drops the named entry.
    rpm_gpg_keys: list[RpmGpgKey] = field(default_factory=list)
    # Simulated systemd: full unit name (``httpd.service``) -> its ActiveState
    # string (``active``/``inactive``/...), backing ``SystemdServiceState``;
    # and the set of unit names enabled on boot, backing
    # ``SystemdServiceEnabled``. A unit absent from ``services`` reads as
    # None (as the fact reports when systemd answers nothing).
    services: dict[str, str] = field(default_factory=dict)
    enabled_services: set[str] = field(default_factory=set)
    # Simulated OpenRC: runlevel -> service -> raw state word
    # (``started``/``stopped``/``crashed``/...), backing ``OpenrcServices``
    # (the raw words) and ``OpenrcStatus`` (projected to the running bool,
    # only ``started`` reads True) -- one lossless store for both facts,
    # keyed by runlevel because ``rc-status <runlevel>`` lists the services
    # assigned to that runlevel. An unknown runlevel reads as {}.
    # ``set_openrc_service_state`` (the simulated ``rc-service``) and the
    # ``rc-update add``/``del`` mutators maintain it alongside
    # ``openrc_runlevels``.
    openrc_service_states: dict[str, dict[str, str]] = field(default_factory=dict)
    # Simulated OpenRC runlevel assignments: runlevel -> assigned names
    # (service names, plus stacked runlevel names -- a stacked runlevel is
    # a symlink inside its target's directory), backing ``OpenrcRunlevels``
    # (the raw mapping) and ``OpenrcEnabled`` (projected to per-runlevel
    # membership bools; a service assigned to no runlevel is absent from
    # the projection, so consumers read ``.get(service, False)``).
    # ``enable_openrc_service``/``disable_openrc_service`` (the simulated
    # ``rc-update add``/``del``), ``stack_openrc_runlevel``/
    # ``unstack_openrc_runlevel`` (``rc-update -s``) and
    # ``create_openrc_runlevel``/``remove_openrc_runlevel`` (the runlevel
    # directory commands) maintain it.
    openrc_runlevels: dict[str, list[str]] = field(default_factory=dict)
    # The installed OpenRC release (``OpenrcVersion``), e.g. ``"0.54.1"``.
    # The empty-string default projects to None -- a simulated host
    # without OpenRC -- because None itself is the seam-absent sentinel
    # the fact's getattr check relies on. A read-only seam: no operation
    # installs or upgrades OpenRC itself, so a test seeds it directly.
    openrc_version: str = ""
    # Orphan apk packages (the sorted, de-duplicated ``ApkPackageVersion``
    # dicts -- name and version-and-release -- ``ApkAutoremovablePackages`` returns,
    # the packages ``apk add --purge`` would drop because nothing in
    # ``/etc/apk/world`` reaches them any more), backing ``ApkAutoremovablePackages``.
    # Kept separate from ``apk_packages`` (the installed-name -> versions state
    # store) because the
    # ``apk list --installed`` rows that seam models cannot tell an orphan from
    # a package still reachable from the world, and from
    # ``apk_explicit_packages`` (the world entries) because a world entry is by
    # definition NOT an orphan. Named with the apk_ prefix (like
    # ``apk_packages``) so the apk-side state never mixes with the apt/dnf-
    # side stores -- in particular with ``apt_autoremovable``, the sibling
    # apt-side seam this one mirrors in role.
    # Defaults
    # to [] -- a simulated machine with nothing a purge would remove, like the
    # fact's answer for a simulation with no ``(N/M) Purging`` rows. A test
    # seeds it directly; ``autoremove_apk_packages`` (the simulated
    # ``apk.autoremove``) clears it and pops the names from ``apk_packages``.
    apk_autoremovable: list[ApkPackageVersion] = field(default_factory=list)
    # Explicitly installed (world) apk packages (the sorted, de-duplicated
    # ``ApkPackageVersion`` dicts -- the bare name plus each line's version
    # constraint as the ``version``, ``""`` for a bare name, with the
    # ``@tag`` selector already dropped -- that
    # ``ApkExplicitPackages`` returns), backing
    # ``ApkExplicitPackages``. Kept separate from ``apk_packages`` (the
    # installed-name -> versions state store) because the ``apk list
    # --installed`` rows that seam models
    # cannot tell a package the operator asked for from a dependency.
    # Named with the apk_ prefix (like ``apk_packages``) so the apk-side
    # state never mixes with the apt/dnf-side stores. Defaults to [] -- a
    # simulated machine whose ``/etc/apk/world`` holds nothing, like the
    # fact's answer for a missing or empty file. A test seeds it directly;
    # ``add_apk_virtual``/``remove_apk_virtual`` (the simulated
    # ``apk.virtual``) insert/drop the virtual group's tag as an
    # unconstrained entry, keeping the list sorted and de-duplicated as the
    # world file's parsed entries read.
    apk_explicit_packages: list[ApkPackageVersion] = field(default_factory=list)
    # Held (version-pinned) apk packages (the list of ``ApkPackageVersion``
    # entries ``ApkHeldPackages`` returns -- each the bare name and its
    # version constraint as the ``version`` (e.g. ``curl`` pinned
    # ``=8.14.0-r0``), the ``@tag`` selector dropped), backing
    # ``ApkHeldPackages``. Kept separate from ``apk_explicit_packages`` (the
    # world entries) because that list reports every world line, held or
    # not, and from ``apk_packages`` (the installed-name -> versions
    # state store)
    # because the ``apk list --installed`` rows that seam models never show
    # the world's version pins. Named with the apk_ prefix (like
    # ``apk_packages``) so the apk-side state never mixes with the apt/dnf-
    # side stores. Defaults to [] -- a simulated machine whose
    # ``/etc/apk/world`` pins nothing, like the fact's answer for a missing
    # or empty file. A test seeds it directly; ``hold_apk_packages``/
    # ``unhold_apk_packages`` (the simulated ``apk.hold``) append/remove the
    # entries as the corresponding apk commands would, the ``@tag`` selector
    # always dropped because the hold command the simulation models never
    # writes one.
    apk_held_packages: list[ApkPackageVersion] = field(default_factory=list)
    # Installed apk packages -- the state store the fact reads, kept as the
    # installed-name -> sorted version-and-release list the mutators edit
    # (e.g. ``musl`` -> ``["1.1.22-r3"]``), which ``ApkInstalledPackages.process``
    # projects into the sorted ``ApkPackageVersion`` entries it returns, one
    # per name-and-version pair. Named
    # distinctly from ``packages`` (the name -> versions store
    # ``DnfPackages`` reads) and ``apt_packages`` (name -> versions) so the
    # dnf-, apt- and apk-side state never mix. Defaults to {} -- a simulated
    # Alpine machine with no installed packages listed, so the fact answers
    # ``[]``. A test seeds it directly;
    # ``install_apk_packages`` and ``remove_apk_packages`` (the simulated
    # ``apk.packages``) mutate it as the corresponding apk commands would.
    apk_packages: dict[str, list[str]] = field(default_factory=dict)
    # Configured apk repositories (the list of ``ApkRepository`` dicts
    # ``ApkRepositories`` returns -- a ``url`` and its ``tag``, the empty
    # string for an untagged line), backing ``ApkRepositories``. Named with
    # the apk_ prefix (like ``apk_packages``) to stay distinct from
    # ``repositories`` (the dnf-side ``DnfRepositories`` store) so the two
    # never mix. Defaults to [] -- a
    # simulated machine with no ``/etc/apk/repositories`` entries, like the
    # fact's answer for a missing or empty file. A test seeds it directly;
    # ``install_apk_repo_file`` (the simulated ``apk.repository`` install)
    # refreshes it from the installed file's content -- dropping the entries the
    # previous text parsed into and appending the new text's -- so a repeat is a
    # no-op, keeping the ``remote_files`` content at the path in sync.
    apk_repositories: list[ApkRepository] = field(default_factory=list)
    # Per-package detailed metadata (package name -> the ``ApkPackageInfo``
    # dict ``ApkPackage`` returns -- name, version, description, url, license,
    # size, depends, provides), backing ``ApkPackage``. A name absent from the
    # mapping reads as None, like an absent package printing no ``apk info``
    # field header. Kept separate from ``apk_packages`` (the installed-name ->
    # versions state store) because the ``apk list --installed`` rows that seam
    # models report only
    # installed versions, never a package's dependencies, size or description.
    # Named with the apk_ prefix (like ``apk_packages``) so the apk-side state
    # never mixes with the apt/dnf-side stores. Defaults to {} -- a simulated
    # machine whose packages' detail has not been seeded. A read-only seam:
    # the namespace has no operations yet, so no ``execute_command`` mutator
    # maintains it and a test seeds it directly.
    apk_package_info: dict[str, ApkPackageInfo] = field(default_factory=dict)
    # No-longer-needed apt packages (the sorted, de-duplicated
    # ``AptPackageInfo`` dicts -- name plus the single bracketed Remv
    # version as a one-element ``versions`` list --
    # ``AptAutoremovable`` returns, architecture-suffixed names like
    # ``libc6:i386`` included), backing ``AptAutoremovable``. Kept separate
    # from ``apt_packages`` (name -> versions) because the installed rows
    # cannot tell a legacy dependency from one still in use. Defaults to
    # [] -- a simulated machine with nothing an autoremove would remove,
    # like the fact's answer for a simulation with no Remv rows. A test
    # seeds it directly; ``auto_remove_apt_packages`` (the simulated
    # ``apt.auto_remove``) clears it and pops the entry names from
    # ``apt_packages``.
    apt_autoremovable: list[AptPackageInfo] = field(default_factory=list)
    # Held apt packages (the sorted, de-duplicated ``AptPackageInfo``
    # entries ``AptHeldPackages`` returns -- name plus an empty
    # ``versions`` list, since ``apt-mark showhold`` prints no version,
    # architecture-suffixed
    # names like ``libc6:i386`` included), backing ``AptHeldPackages``. Kept
    # separate
    # from ``apt_packages`` (name -> versions) because the hold state is
    # invisible in the ``apt list --installed`` rows that seam models.
    # Defaults to [] -- a simulated machine with nothing marked
    # ``apt-mark hold``, like the fact's answer for empty showhold output.
    # A test seeds it directly; ``hold_apt_packages``/``unhold_apt_packages``
    # (the simulated ``apt.mark``) add/drop entries, keeping the list sorted
    # and de-duplicated as ``apt-mark showhold`` output parses.
    apt_held_packages: list[AptPackageInfo] = field(default_factory=list)
    # The machine architecture (``uname -m``), backing ``Arch``. Defaults to
    # ``x86_64`` -- a real machine always answers ``uname -m`` with something.
    arch: str = "x86_64"
    # GPG keys available to APT (the list of ``AptKey`` dicts ``AptKeys``
    # returns -- id, validity, length, subkeys, fingerprint, uid_hash, uid),
    # backing ``AptKeys``. The seam holds the already-flattened answer the
    # fact produces after merging every keyring file, so the fact's
    # ``directories`` argument does not filter it. Defaults to [] -- a
    # simulated machine with no keyring files found, like the fact's
    # answer when the find sweep lists nothing. A read-only seam: no
    # ``execute_command`` mutator maintains it, so a test sets it directly.
    apt_keys: list[AptKey] = field(default_factory=list)
    # Installed apt packages -- the state store the fact reads, kept as the
    # name -> sorted version list the mutators edit (architecture-suffixed
    # names like ``libc6:i386`` included), which ``AptPackages.process``
    # projects into the sorted ``AptPackageInfo`` entries it returns. Named
    # distinctly
    # from ``packages`` (the name-only set ``DnfPackages`` reads) so the
    # dnf- and apt-side state never mix. Defaults to {} -- a simulated
    # machine with no installed packages listed, so the fact answers ``[]``.
    # A test seeds it directly;
    # ``install_deb``, ``install_apt_packages``, ``remove_apt_packages``,
    # ``upgrade_apt_packages``, ``dist_upgrade_apt_packages`` and
    # ``auto_remove_apt_packages`` mutate it as the corresponding
    # simulated apt commands would.
    apt_packages: dict[str, list[str]] = field(default_factory=dict)
    # Per-package ``apt-cache policy`` answers (package name -> the
    # ``AptPackagePolicy`` dict ``AptPolicy`` returns -- installed, candidate
    # and the parsed version table), backing ``AptPolicy``. A name absent
    # from the mapping reads as None, like an unknown package printing
    # nothing on stdout. Kept separate from ``apt_packages`` because the
    # candidate and repository origins are invisible in the ``apt list
    # --installed`` rows that seam models. Defaults to {} -- a simulated
    # machine where no package's policy has been seeded. A read-only seam:
    # no ``execute_command`` mutator maintains it, so a test sets it
    # directly.
    apt_policy: dict[str, AptPackagePolicy] = field(default_factory=dict)
    # Simulated ``apt-get`` change counts (subcommand phrase -> the
    # ``AptSimulationDict`` dict ``SimulateOperationWillChange`` returns --
    # upgraded, newly_installed, removed, not_upgraded), backing
    # ``SimulateOperationWillChange``. A phrase absent from the mapping
    # reads as None, like a simulation printing no summary line (which
    # ``apt.dist_upgrade`` treats as "run the real command anyway").
    # Defaults to {} -- a simulated machine where no simulation has been
    # seeded. A test seeds it directly; ``dist_upgrade_apt_packages``,
    # ``upgrade_apt_packages`` and ``auto_remove_apt_packages`` zero the
    # matching entry as the real commands would leave nothing more to do.
    apt_simulations: dict[str, AptSimulationDict] = field(default_factory=dict)
    # Configured apt repositories (the list of ``AptSource`` dicts
    # ``AptSources`` returns -- type, url, distribution, components and
    # list-valued options, legacy .list and deb822 .sources entries already
    # merged and expanded), backing ``AptSources``. Defaults to [] -- a
    # simulated machine with no sources files, like the baseline's
    # ``default = list``. A test seeds it directly; ``add_ppa``/``remove_ppa``
    # (the simulated ``apt.ppa``) append/drop the PPA's Launchpad source and
    # ``install_apt_repo_file`` (the simulated ``apt.repo``) refreshes the
    # entries the installed file content parses into, so ``AptSources``
    # stays consistent across a simulated add/remove. The
    # ``apt.sources_file`` mutators (``install_apt_sources_file``/
    # ``remove_apt_sources_file``) replace/drop the entries their deb822
    # content parses into, keeping the seam consistent across a simulated
    # sources-file write/remove too.
    apt_sources: list[AptSource] = field(default_factory=list)
    # Pending apt upgrades (the list of ``AptPackageUpgrade`` dicts
    # ``AptUpgradable`` returns -- name, installed and candidate versions,
    # architecture-suffixed names like ``libc6:i386`` included), backing
    # ``AptUpgradable``. Kept separate from ``apt_packages`` (name ->
    # installed versions) and ``apt_policy`` (one package's full policy)
    # because neither the installed rows nor a per-package policy query
    # names the whole set an upgrade would move. Defaults to [] -- a
    # simulated machine fully up to date, like the fact's answer for a
    # listing with no upgradable rows. A test seeds it directly;
    # ``dist_upgrade_apt_packages`` (the simulated ``apt.dist_upgrade``) and
    # ``upgrade_apt_packages`` (the simulated ``apt.upgrade`` /
    # ``apt.packages(upgrade=True)``) apply the candidates into
    # ``apt_packages`` and clear it.
    apt_upgradable: list[AptPackageUpgrade] = field(default_factory=list)
    # ``.deb`` files present on the host (path -> the ``DebPackageInfo``
    # dict -- name and version), backing
    # ``DebPackage``'s file query. ``apt.deb`` installs the file's package:
    # ``install_deb`` copies the file's version into ``apt_packages`` (the
    # name -> sorted version list ``AptPackages`` reads), so a repeat is a
    # no-op and a removal (``present=False``) clears it. Kept separate from
    # ``rpm_files`` (the dnf-side ``.rpm`` store) so apt and dnf never mix.
    deb_files: dict[str, DebPackageInfo] = field(default_factory=dict)
    # SSH public keys, backing ``AuthorizedKeys``: keyed by username for the
    # default ``~user/.ssh/authorized_keys`` path and by the explicit path
    # when the fact is given one; each value is the list of full key strings
    # the file lists (already stripped, no comments -- the parsed form).
    # ``server.user_authorized_keys`` always reads the fact with an explicit
    # path, so its ``install_authorized_keys_file`` mutator writes only the
    # path-keyed entries, recomputed from the installed text (so a repeat
    # install never duplicates a key and a ``delete_keys`` rewrite drops the
    # unrequested ones); a test seeding the username-keyed form exercises
    # the fact directly, not that operation.
    authorized_keys: dict[str, list[str]] = field(default_factory=dict)
    # Mounted block devices (device -> the ``BlockDevice`` entry the fact
    # returns -- {blocks, used, available, used_percent, mount}), backing
    # ``BlockDevices``. Each value is the parsed form the fact returns --
    # all five entries raw strings, as ``df`` prints them. Defaults to {}
    # -- a simulated machine with no devices listed, like the baseline's
    # ``default = dict`` for a failed read.
    block_devices: dict[str, BlockDevice] = field(default_factory=dict)
    # CPU details (the parsed ``LANG=C lscpu`` rows), backing ``CpuInfo``:
    # each key maps to its stripped string value, except a ``flag``-bearing
    # key (e.g. ``Flags``) whose value is the list of whitespace-split tokens.
    # Defaults to {} -- a simulated machine reporting no lscpu rows; the real
    # transport reads None on a failed/empty read (the baseline's ``invalid``
    # fixture), but the seam stays a non-None dict so it never falls through
    # to the shell.
    cpu_info: dict[str, str | list[str]] = field(default_factory=dict)
    # The number of online CPUs (the parsed ``getconf NPROCESSORS_ONLN``
    # answer), backing ``Cpus``. Defaults to the baseline's own fact fixture value
    # (1) -- a real machine always answers ``getconf``, and a fixed default
    # keeps simulated reads deterministic.
    cpus: int = 1
    # The server's clock, backing ``Date``. Defaults to the baseline's own fact
    # fixture timestamp (2019-01-01T00:00:00+00:00) -- a real machine always
    # answers ``date``, so the seam never falls through to the shell, and a
    # fixed default keeps simulated reads deterministic.
    date: datetime = datetime(2019, 1, 1, tzinfo=UTC)
    # Per-device IO counters (device -> the ``DiskStat`` int counter entry
    # the fact returns), backing ``DiskStats``. Each value holds whichever
    # counter groups the simulated kernel prints -- the eleven classic
    # counters, plus the discard/flush groups on a modern kernel. Defaults
    # to {} -- a simulated machine with no /proc/diskstats rows, like the
    # fact's {} fallback for a failed read.
    disk_stats: dict[str, DiskStat] = field(default_factory=dict)
    # DMI identity fields (the parsed /sys/class/dmi/id/ dump: sys_vendor,
    # product_name, bios_version, chassis_type, ...), backing ``DmiInfo``.
    # Each value is the raw string the sysfs file holds, with uevent/
    # modalias and empty values already absent. Defaults to {} -- a
    # simulated machine with no DMI identity (a container), like the
    # fact's {} fallback for a failed read.
    dmi_info: dict[str, str] = field(default_factory=dict)
    # The login shell's environment (variable name -> the raw ``echo $<name>``
    # value), backing ``Env``. Named ``os_env`` with the os_ prefix (like
    # ``os_kernel``) so it stays clear of any inventory field. An unlisted
    # variable reads as "", like the blank line echo prints for an unset one.
    # Defaults to a standard Linux PATH -- a real machine always answers
    # ``echo $PATH``, and a fixed default keeps simulated reads deterministic
    # (the replaced ``Path`` fact's seam default, now keyed by variable name).
    os_env: dict[str, str] = field(
        default_factory=lambda: {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin"
        }
    )
    # One directory's immediate entries (path -> the ``DirectoryEntry``
    # rows ``DirectoryEntries`` returns -- name and type word), backing
    # ``DirectoryEntries``. Keys are the caller-supplied path strings
    # verbatim (the command has no filters, so the path alone identifies
    # the question); an absent key reads as [], like a missing or empty
    # directory the ``|| true`` guard masks. A read-only seam: a test
    # seeds the rows directly (no execute_command arm -- the fact issues
    # no write command); the read copies each entry dict.
    directory_entries: dict[str, list[DirectoryEntry]] = field(default_factory=dict)
    # Directory metadata (path -> the stat dict ``Directory`` returns -- the
    # same shape as ``file_stats`` -- or False for a path that exists but is
    # not a directory), backing ``Directory``. A separate store from
    # ``file_stats`` because a metadata dict carries no type flag, so one
    # store cannot answer both the file- and the directory-typed question
    # for the same path. Keys are the caller-supplied path strings verbatim,
    # ``~/...`` prefixes included; an absent key reads as None, like a
    # missing path. Also backing ``git.worktree``'s and ``git.repo``'s private
    # ``_DirectoryExists`` probe (the same seam, read as a bool):
    # ``add_worktree``/``remove_worktree`` (the simulated ``git.worktree``)
    # add/drop the worktree path so a repeat add or remove is a no-op, and
    # ``clone_repo`` (the simulated ``git.repo``) adds both ``dest`` and
    # ``<dest>/.git`` so a repeat clone short-circuits. The
    # ``files.directory`` seam maintains it too: ``make_directory`` writes a
    # ``755`` row for a simulated ``mkdir -p``, ``chmod_path``/``chown_path``
    # replace the row's mode/owner for a simulated ``chmod``/``chown``, and
    # ``remove_tree`` drops the row for a simulated ``rm -rf``. A test seeds
    # any other directory directly.
    directory_stats: dict[str, dict[str, Any] | bool] = field(default_factory=dict)
    # File metadata (path -> the stat dict ``File`` returns -- user, group,
    # mode, atime, mtime, ctime, size -- or False for a path that exists but
    # is not a regular file), backing ``File``. Keys are the caller-supplied
    # path strings verbatim, ``~/...`` prefixes included: the command leaves
    # the leading tilde for the remote shell to expand, so the effective
    # remote path is only known after expansion (like ``authorized_keys``,
    # this cannot ride the literal-path ``remote_files`` store -- which also
    # holds content bytes, not metadata). An absent key reads as None, like
    # a missing path. A test seeds it directly; the ``files.move`` seam
    # (``move_path``/``remove_tree``) relocates or drops a row, the
    # ``files.directory`` ``chmod_path``/``chown_path`` mutators rewrite a
    # row's mode/owner, and the ``files.file`` ``create_file``/``remove_file``
    # mutators seed (mode 644, root-owned -- what a plain ``touch`` creates)
    # or drop the row, so the ``File`` fact stays consistent with the
    # ``FileExists`` probe's ``existing_files`` marker.
    file_stats: dict[str, dict[str, Any] | bool] = field(default_factory=dict)
    # Ext2-family attribute flags set on a path (the sorted one-letter
    # ``lsattr`` codes, ``i`` for immutable, ``a`` for append-only, ...),
    # backing ``FileFlags`` -- the read behind ``files.flags``. Keys are the
    # caller-supplied path strings verbatim; an absent key reads as [], like
    # a path with no attributes (or an absent one, which the fact's guard
    # short-circuits). A test seeds it directly; ``set_file_flags``/
    # ``clear_file_flags`` (the simulated ``chattr +flags``/``-flags``) add/
    # drop letters so a repeat ``files.flags`` is a no-op.
    file_flags: dict[str, set[str]] = field(default_factory=dict)
    # Find-path answers (rendered command string -> the path list the
    # find printed), backing ``FindFiles``/``FindDirectories``/
    # ``FindLinks``. The key is the *exact* command built by
    # ``<Fact>.command(...)`` -- the answer depends on every filter, so a
    # path-only key could answer a different question than the one asked.
    # An unseeded command reads as [], like the ``|| true`` guard masking
    # a missing start path. A read-only seam: a test seeds it directly;
    # the read copies the list.
    find_results: dict[str, list[str]] = field(default_factory=dict)
    # Find-with-metadata answers (rendered command string -> the
    # ``FoundFile`` entries ``FindFilesInfo`` returns), backing
    # ``FindFilesInfo``. Command-keyed like ``find_results`` (the filters
    # are part of the question); an unseeded command reads as []. A
    # read-only seam: a test seeds it directly; the read copies each
    # entry dict (the datetime values are immutable).
    found_files: dict[str, list[FoundFile]] = field(default_factory=dict)
    # The firewalld daemon record ({running, version}), backing
    # ``FirewalldStatus``. The default projects a stopped daemon with no
    # version; a test seeds it directly (read-only -- no operation starts
    # or stops the daemon; that is systemd.service's job).
    firewalld_status: FirewalldStatusInfo = field(
        default_factory=lambda: FirewalldStatusInfo(running=False, version="")
    )
    # The global default zone name, backing ``FirewalldDefaultZone``. An
    # unseeded host reads "" (a stopped daemon or an unprivileged read).
    # The simulated ``firewalld.default_zone`` arm writes it directly --
    # the setting is inherently dual-scope, so one field serves both.
    firewalld_default_zone: str = ""
    # Simulated runtime zones: name -> the ``FirewalldZone`` record
    # ``FirewalldZones`` returns, backing ``FirewalldZones``. The
    # firewalld mutators maintain it alongside ``firewalld_permanent_zones``;
    # ``reload_firewalld`` copies one scope over the other.
    firewalld_zones: dict[str, FirewalldZone] = field(default_factory=dict)
    # Simulated permanent zones (the on-disk configuration a reload
    # applies), backing ``FirewalldZones(permanent=True)``. Zone
    # creation/deletion is permanent-only, so a fresh zone lands here
    # and reaches the runtime store only via ``reload_firewalld``.
    firewalld_permanent_zones: dict[str, FirewalldZone] = field(default_factory=dict)
    # Service definitions (name -> the ``FirewalldService`` record),
    # backing ``FirewalldServices``. Definitions are permanent-only by
    # firewall-cmd's design, so one store serves the fact.
    firewalld_services: dict[str, FirewalldService] = field(default_factory=dict)
    # Simulated runtime ipsets: name -> the ``FirewalldIpSet`` record,
    # backing ``FirewalldIpSets``. Distinct from ``ipsets`` (the
    # iptables.IpSets kernel-table store): firewalld manages its own
    # prefixed containers.
    firewalld_ipsets: dict[str, FirewalldIpSet] = field(default_factory=dict)
    # Simulated permanent ipsets, backing ``FirewalldIpSets(permanent=True)``.
    firewalld_permanent_ipsets: dict[str, FirewalldIpSet] = field(default_factory=dict)
    # Simulated runtime policies: name -> the ``FirewalldPolicy`` record,
    # backing ``FirewalldPolicies``. Read-only (no policy management
    # operation); ``reload_firewalld`` keeps the scopes in sync.
    firewalld_policies: dict[str, FirewalldPolicy] = field(default_factory=dict)
    # Simulated permanent policies, backing ``FirewalldPolicies(permanent=True)``.
    firewalld_permanent_policies: dict[str, FirewalldPolicy] = field(default_factory=dict)
    # Whether lockdown mode is on, backing the private ``_FirewalldLockdown``
    # probe the ``firewalld.lockdown`` operation compares against.
    firewalld_lockdown: bool = False
    # Whether panic mode is on, backing the private ``_FirewalldPanicMode``
    # probe the ``firewalld.panic`` operation compares against.
    firewalld_panic_mode: bool = False
    # Per-package flatpak identity (package name -> the ``FlatpakPackageInfo``
    # dict ``FlatpakPackage`` returns -- id, ref and version), backing
    # ``FlatpakPackage``. A name absent from the mapping reads as None, like an
    # absent package printing no tracked ``flatpak info`` row. Named with the
    # flatpak_ prefix and kept in its own store so the flatpak-side state never
    # mixes with the apk/apt/dnf/snap package-detail seams. Defaults to {} -- a
    # simulated machine with no flatpaks installed. ``FlatpakPackages`` projects
    # this store's keys (the snap single-store precedent), so
    # ``install_flatpak_packages``/``remove_flatpak_packages`` (the simulated
    # ``flatpak install``/``uninstall``) maintain one store for both facts and
    # they can never disagree.
    flatpak_package_info: dict[str, FlatpakPackageInfo] = field(default_factory=dict)
    # The branch a repository is on (``git describe --all``, the
    # heads/tags prefix stripped), backing ``GitBranch``: repo path ->
    # branch name. An absent repo reads as "", like the short-circuited
    # directory guard. A test seeds it directly for read-only fact checks;
    # ``clone_repo``/``checkout_branch`` (the simulated ``git.repo``) write it
    # so a repeat ``git.repo`` sees the branch already checked out.
    git_branches: dict[str, str] = field(default_factory=dict)
    # Git configuration items (the parsed ``git config -l`` rows), backing
    # ``GitConfig``: scope key -> the key -> value-list dict, which the fact
    # projects into its ``GitConfigItem`` entry list (the apk/apt mutated-
    # dict-store precedent). The scope
    # key is the repo path for a ``--local`` read, or the "global"/
    # "system" sentinels for the two repo-less login-user scopes. An
    # absent scope reads as [] (the baseline's ``default = dict``, projected).
    # A test
    # seeds it directly; ``set_git_config`` (the simulated ``git.config``)
    # sets or adds a key's value list under the targeted scope, so a
    # repeat ``git.config`` reads the value back and is a no-op.
    # ``clone_repo``/``set_git_remote_url`` (the simulated ``git.repo``)
    # additionally maintain the ``remote.origin.url`` row under the repo-
    # path scope, so a repeat ``git.repo`` sees origin already reconciled.
    git_configs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # Whether a repository's ``HEAD`` is detached (the ``attached``/
    # ``detached`` probe answer), backing ``GitHeadDetached``: repo path ->
    # detached bool. An absent repo reads as None (undetermined), like the
    # wrapped probe printing no token for a missing or invalid repository. A
    # read-only seam: no ``execute_command`` mutator maintains it, so a test
    # sets it directly.
    git_head_detached: dict[str, bool] = field(default_factory=dict)
    # Local ref SHAs (the ``git rev-parse <ref>`` answers), backing
    # ``GitLocalCommit``: repo path -> ref -> SHA. An absent repo or ref
    # reads as None, like the swallowed rev-parse failure. ``pull_worktree``
    # (the simulated ``git.worktree`` pull) advances a worktree's ``HEAD`` to
    # the remote tip; ``pull_repo`` (the simulated ``git.repo`` pull)
    # advances the repo's row at the effective-branch key, so a repeat pull
    # short-circuits through ``skip_pull``. A test seeds any other repo or
    # ref directly.
    git_local_commits: dict[str, dict[str, str]] = field(default_factory=dict)
    # Remote branch tip SHAs (the ``git ls-remote`` answers), backing
    # ``GitRemoteBranchCommit``: repo path -> remote name -> ref -> SHA,
    # where the ref is the requested branch or "HEAD" for a falsy one. An
    # absent repo, remote or ref reads as None, like the swallowed
    # ls-remote failure. A read-only seam: a test seeds it directly.
    git_remote_commits: dict[str, dict[str, dict[str, str]]] = field(
        default_factory=dict
    )
    # Remote fetch/push URLs (the ``git remote get-url`` answers), backing
    # ``GitRemoteUrl``: repo path -> remote name -> "fetch"/"push" -> URL. An
    # absent repo, remote or kind reads as None, like the swallowed
    # ``get-url`` failure. A read-only seam: a test seeds it directly.
    git_remote_urls: dict[str, dict[str, dict[str, str]]] = field(
        default_factory=dict
    )
    # The paths that are valid git working-tree repositories (the
    # ``git rev-parse --is-inside-work-tree`` answers), backing ``GitIsRepo``.
    # Membership is the answer; an absent path reads as False, like the guard
    # or rev-parse printing no ``true``. A test seeds it directly for
    # read-only fact checks; ``clone_repo`` (the simulated ``git.repo``)
    # adds the freshly cloned ``dest`` so the shape stays consistent with the
    # ``<dest>/.git`` entry it also writes into ``directory_stats``.
    git_repos: set[str] = field(default_factory=set)
    # The ``user@host`` targets whose ``ssh -T`` authentication succeeds,
    # backing ``GitSshKeyConfigured``. Membership is the answer; an absent
    # target reads as False, like the probe printing ``unauthenticated``. Not
    # repo-scoped -- the one git seam keyed by a remote target, not a repo
    # path. A read-only seam: a test seeds it directly.
    git_ssh_authenticated: set[str] = field(default_factory=set)
    # Working-tree statuses (the parsed ``git status --porcelain`` rows),
    # backing ``GitStatus``: repo path -> ``GitStatusSummary``. An absent repo
    # reads as a clean summary (``dirty=False``, empty lists), like the
    # short-circuited directory guard. A read-only seam: a test seeds it
    # directly.
    git_statuses: dict[str, GitStatusSummary] = field(default_factory=dict)
    # Submodule rows (the parsed ``git submodule status`` output), backing
    # ``GitSubmodule``: repo path -> ``GitSubmoduleStatus`` list. An absent
    # repo reads as [], like the short-circuited directory guard. A read-only
    # seam: a test seeds it directly.
    git_submodules: dict[str, list[GitSubmoduleStatus]] = field(default_factory=dict)
    # The repository's tags (the ``git tag`` lines), backing ``GitTag``:
    # repo path -> ``GitTagInfo`` entry list in listing order. An absent repo
    # reads as [], like the short-circuited directory guard. A read-only
    # seam: a test seeds it directly.
    git_tags: dict[str, list[GitTagInfo]] = field(default_factory=dict)
    # Upstream tracking branches (the ``...`` token of
    # ``git status --branch --porcelain``), backing ``GitTrackingBranch``:
    # repo path -> upstream branch. An absent repo reads as None, like a
    # branch without an upstream. A read-only seam: a test seeds it
    # directly.
    git_tracking_branches: dict[str, str] = field(default_factory=dict)
    # GPG key files' contents (src path -> the ``GpgKeyInfo`` entries
    # ``GpgKey`` returns for that file), backing ``GpgKey``. An unseeded
    # path reads as [], the simulated stand-in for a file gpg cannot read.
    # Kept separate from ``gpg_keys`` (the default keychain's listing)
    # because a key file can be read without importing it. A read-only
    # seam: a test seeds it directly.
    gpg_key: dict[str, list[GpgKeyInfo]] = field(default_factory=dict)
    # One key's full metadata (keyid -> the ``GpgKeyMetadata`` dict
    # ``GpgKeyDetails`` returns for that key), backing ``GpgKeyDetails``.
    # An unseeded keyid reads as None, the simulated stand-in for an
    # absent key. Kept separate from ``gpg_keys`` because the metadata
    # carries algo/created/capabilities/uids the listing entries do not.
    # A read-only seam: a test seeds it directly.
    gpg_key_details: dict[str, GpgKeyMetadata] = field(default_factory=dict)
    # Keyring files found under the searched directories (the
    # ``GpgKeyring`` entries ``GpgKeyrings`` returns -- path, format and
    # the keys each file holds), backing ``GpgKeyrings``. The seam holds
    # the already-gathered answer, so the fact's ``directories`` argument
    # does not filter it. Defaults to [] -- a simulated machine with no
    # keyring files found. The ``gpg.key`` mutators maintain it alongside
    # ``remote_files``/``existing_files``/``file_stats``: an install
    # upserts an entry (with empty keys -- key material is not modeled,
    # a test seeds keys directly), a selective delete drops the matching
    # key entries, and a whole-file removal (or the empty-file cleanup)
    # drops the entry.
    gpg_keyrings: list[GpgKeyring] = field(default_factory=list)
    # Public keys in the default keychain (the ``GpgKeyInfo`` entries
    # ``GpgKeys`` returns -- id, validity, length, subkeys, fingerprint,
    # uid_hash, uid), backing ``GpgKeys``. The seam holds the default
    # keychain's answer, so the fact's ``keyring`` argument does not filter
    # it. Named with the gpg_ prefix (like ``apt_keys``) so the gpg-side
    # state never mixes with the apt projection. Defaults to [] -- a
    # simulated machine with an empty keychain. Tests seed it directly;
    # the ``import_gpg_key``/``gen_gpg_key``/``delete_gpg_key`` mutators
    # keep it consistent with the keychain operations.
    gpg_keys: list[GpgKeyInfo] = field(default_factory=list)
    # Secret keys in the default keychain (the ``GpgKeyInfo`` entries
    # ``GpgSecretKeys`` returns), backing ``GpgSecretKeys``. Kept separate
    # from ``gpg_keys`` because a public listing cannot tell which keys
    # have secret material on the host. Only metadata is modeled -- no
    # secret key material ever enters the simulation. Defaults to [] --
    # a simulated machine with no secret keys. Tests seed it directly;
    # the ``gen_gpg_key``/``delete_gpg_key`` mutators keep it consistent
    # (a simulated import models no secret material).
    gpg_secret_keys: list[GpgKeyInfo] = field(default_factory=list)
    # Ownertrust assignments in the trust database (the ``GpgKeyTrust``
    # entries ``GpgTrust`` returns -- fingerprint, raw numeric ownertrust
    # and level name), backing ``GpgTrust``. Defaults to [] -- a simulated
    # machine with no trust assignments exported. The ``set_gpg_trust``/
    # ``gen_gpg_key``/``delete_gpg_key`` mutators upsert/prune it alongside
    # the key lists.
    gpg_trust: list[GpgKeyTrust] = field(default_factory=list)
    # Signature verification outcomes (signature path -> the
    # ``GpgVerification`` dict ``GpgVerify`` returns for that signature),
    # backing ``GpgVerify``. An unseeded path reads as the error answer
    # {valid: False, status: "error"}, the simulated stand-in for a failed
    # verification; the fact's ``file`` argument does not select between
    # entries. A read-only seam: a test seeds it directly.
    gpg_verify: dict[str, GpgVerification] = field(default_factory=dict)
    # Grep matches ((path, pattern) -> the matching lines ``FindInFile``
    # returns), backing ``FindInFile``. A dedicated store rather than a
    # projection of ``remote_files``: grep's BRE semantics cannot be
    # faithfully re-derived from content bytes with Python's ``re``, so a
    # test seeds the matching lines directly. An empty list means the
    # file exists but nothing matches (the sentinel answer); an absent
    # key reads as None, like a missing file. A read-only seam; the read
    # copies the list.
    grep_lines: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # Recursive grep matches ((path, pattern) -> the matching file paths
    # ``FindInFiles`` returns), backing ``FindInFiles``. The same dedicated-
    # store rationale as ``grep_lines``: grep's ERE semantics cannot be
    # faithfully re-derived from the ``remote_files`` content bytes with
    # Python's ``re``, so a test seeds the matching paths directly. Unlike
    # ``grep_lines`` there is no None answer -- ``FindInFiles`` always returns
    # a list -- so an absent key reads as [] (the ``|| true`` guard's empty
    # answer). A read-only seam; the read copies the list.
    grep_files: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # Group names present on the system (the first ``/etc/group`` field of
    # each row, in file order), backing ``Groups``. Named distinctly from
    # ``Host.group`` (the inventory classifier) so the two are never confused.
    # ``server.group`` mutates it: ``add_group`` appends a new name (file
    # order preserved) and ``remove_group`` drops it, so a repeat operation
    # is a no-op once the fresh per-step fact cache sees the change.
    os_groups: list[str] = field(default_factory=list)
    # Whether a known desktop-session binary is installed, backing
    # ``HasGui``. Defaults to False -- a simulated server is headless, like
    # The baseline's gui_absent fixture (``ls /usr/bin/*session`` lists nothing).
    has_gui: bool = False
    # Home directories, backing ``Home``: username -> home directory path,
    # with "" keying the login user (the bare ``~``). An absent user reads as
    # None, like the shell failing to expand ``~user`` for an unknown user.
    home_directories: dict[str, str] = field(default_factory=dict)
    # The machine's hostname (``uname -n``), backing ``Hostname``. Named
    # ``os_hostname`` to stay distinct from ``Host.name`` (the inventory
    # label) and the inventory's ``connection.hostname`` address alias.
    # Defaults to the baseline's own fact fixture hostname -- a real machine
    # always answers ``uname -n``, and a fixed default keeps simulated
    # reads deterministic. ``set_hostname`` (the simulated ``server.hostname``
    # effect) replaces it, so a repeat hostname set is a no-op.
    os_hostname: str = "my-host.net"
    # Simulated ip6tables chains: table -> {chain: policy}, backing
    # ``Ip6tablesChains``. A user chain created with -N carries policy
    # "-"; an unseeded table reads as {}. The iptables mutators
    # (``manage_iptables_chain``/``set_iptables_policy`` with version=6)
    # maintain it alongside the other ip6tables_* seams.
    ip6tables_chains: dict[str, dict[str, str]] = field(default_factory=dict)
    # Simulated ip6tables numbered rows: table -> the ``IptablesOrderedRule``
    # entries across all chains, backing ``Ip6tablesRulesOrdered``. The
    # rule/chain/flush mutators rebuild a table's rows from its rule
    # definitions (per-chain renumbering, counters reset to 0 -- the
    # simulation models no traffic); ``zero_iptables_counters`` zeroes
    # counters in place. A test seeds rich rows directly for fact checks.
    ip6tables_ordered_rules: dict[str, list[IptablesOrderedRule]] = field(default_factory=dict)
    # Simulated ip6tables persistence: snapshot path -> synced bool,
    # backing ``Ip6tablesPersistence``. An unseeded path reads False (an
    # absent file). ``save_iptables_rules``/``restore_iptables_rules``
    # set the path True; every rule/chain/policy/flush mutation flips the
    # stored paths back to False (zeroing counters does not -- a save
    # without -c omits counters).
    ip6tables_persistence: dict[str, bool] = field(default_factory=dict)
    # Simulated ip6tables rules: table -> the ``IptablesRule`` definitions
    # in listing order, backing ``Ip6tablesRules``. Definitions use the
    # iptables-save shape (bare hosts carry /128, a protocol implies its
    # ``-m`` extras pair); ``apply_iptables_rule`` re-parses the yielded
    # spec through the module's own parser so the stored form compares
    # equal to what the operation builds.
    ip6tables_rules: dict[str, list[IptablesRule]] = field(default_factory=dict)
    # Loaded ip6tables table names, backing ``Ip6tablesTables``. Defaults
    # to [] -- a simulated machine with no IPv6 tables loaded. A read-only
    # seam: no operation loads kernel modules, so a test seeds it directly.
    ip6tables_tables: list[str] = field(default_factory=list)
    # The installed ip6tables release record ({version, backend}), backing
    # ``Ip6tablesVersion``. The empty-dict default projects to None -- a
    # simulated host whose version has not been seeded. A read-only seam.
    ip6tables_version: dict[str, str] = field(default_factory=dict)
    # Simulated ipset containers: name -> the ``IpSet`` record ``IpSets``
    # returns, backing ``IpSets``. ``create_ipset``/``destroy_ipset``/
    # ``manage_ipset_entry`` (the simulated ``iptables.ipset``) maintain
    # it with the real commands' -exist tolerance.
    ipsets: dict[str, IpSet] = field(default_factory=dict)
    # Simulated iptables chains: table -> {chain: policy}, backing
    # ``IptablesChains``. A user chain created with -N carries policy
    # "-"; an unseeded table reads as {}. ``manage_iptables_chain``/
    # ``set_iptables_policy`` maintain it alongside the other iptables_*
    # seams.
    iptables_chains: dict[str, dict[str, str]] = field(default_factory=dict)
    # Loaded ``xt_*`` match-extension module names, backing
    # ``IptablesModules``. Defaults to [] -- a simulated machine with no
    # extension modules listed. A read-only seam: loading a module is
    # ``server.modprobe``'s job (the ``kernel_modules`` store), so a test
    # seeds this list directly.
    iptables_modules: list[str] = field(default_factory=list)
    # Simulated iptables numbered rows: table -> the ``IptablesOrderedRule``
    # entries across all chains, backing ``IptablesRulesOrdered``. The
    # rule/chain/flush mutators rebuild a table's rows from its rule
    # definitions (per-chain renumbering, counters reset to 0 -- the
    # simulation models no traffic); ``zero_iptables_counters`` zeroes
    # counters in place. A test seeds rich rows directly for fact checks.
    iptables_ordered_rules: dict[str, list[IptablesOrderedRule]] = field(default_factory=dict)
    # Simulated iptables persistence: snapshot path -> synced bool,
    # backing ``IptablesPersistence``. An unseeded path reads False (an
    # absent file). ``save_iptables_rules``/``restore_iptables_rules``
    # set the path True; every rule/chain/policy/flush mutation flips the
    # stored paths back to False (zeroing counters does not -- a save
    # without -c omits counters).
    iptables_persistence: dict[str, bool] = field(default_factory=dict)
    # Simulated iptables rules: table -> the ``IptablesRule`` definitions
    # in listing order, backing ``IptablesRules``. Definitions use the
    # iptables-save shape (bare hosts carry /32, a protocol implies its
    # ``-m`` extras pair); ``apply_iptables_rule`` re-parses the yielded
    # spec through the module's own parser so the stored form compares
    # equal to what the operation builds.
    iptables_rules: dict[str, list[IptablesRule]] = field(default_factory=dict)
    # Loaded iptables table names, backing ``IptablesTables``. Defaults
    # to [] -- a simulated machine with no tables loaded. A read-only
    # seam: no operation loads kernel modules, so a test seeds it directly.
    iptables_tables: list[str] = field(default_factory=list)
    # The installed iptables release record ({version, backend}), backing
    # ``IptablesVersion``. The empty-dict default projects to None -- a
    # simulated host whose version has not been seeded. A read-only seam.
    iptables_version: dict[str, str] = field(default_factory=dict)
    # IPv4 addresses per network interface (the parsed `ip addr show` /
    # `ifconfig -a` listing), backing ``Ipv4Addrs``: interface name -> the
    # list of address strings the fact returns, primary first, with any
    # interfaces that carry no IPv4 already absent. Defaults to {} -- a
    # simulated machine with no interfaces listed, like the baseline's
    # ``default = dict`` for a failed read.
    ipv4_addrs: dict[str, list[str]] = field(default_factory=dict)
    # IPv6 addresses per network interface (the parsed `ip addr show` /
    # `ifconfig -a` listing), backing ``Ipv6Addrs``: interface name -> the
    # list of address strings the fact returns, primary first, with any
    # interfaces that carry no IPv6 already absent. Defaults to {} -- a
    # simulated machine with no interfaces listed, like the baseline's
    # ``default = dict`` for a failed read.
    ipv6_addrs: dict[str, list[str]] = field(default_factory=dict)
    # The kernel name (``uname -s``), backing ``Kernel``. Named ``os_kernel``
    # with the os_ prefix (like ``os_hostname``) so it stays clear of any
    # inventory field. Defaults to ``Linux`` -- a real machine always answers
    # ``uname -s``, and the simulated fleet is Linux servers (the baseline's own
    # fixture value is the literal placeholder "os", not a real kernel name).
    os_kernel: str = "Linux"
    # Loaded kernel modules (module name -> info dict), backing
    # ``KernelModules``. Each value is the parsed form the fact returns: on
    # Linux {size, instances, state} plus an optional depends list, on
    # FreeBSD {id, refs, address, size} -- the seam holds whichever shape
    # matches the host's ``os_kernel``. Defaults to {} -- a simulated machine
    # with no modules listed, like the baseline's ``default = dict`` for a failed
    # or absent /proc/modules read. ``server.modprobe`` mutates it:
    # ``load_modules`` adds a name with a minimal Linux-shaped info dict and
    # ``unload_modules`` drops it, so a repeat operation is a no-op once the
    # fresh per-step fact cache sees the change.
    kernel_modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The kernel release (``uname -r``), backing ``KernelVersion``. Named
    # ``os_kernel_version`` with the os_ prefix (like ``os_kernel``) so it
    # stays clear of any inventory field. Defaults to a real Fedora kernel
    # release -- a real machine always answers ``uname -r``, and the
    # simulated fleet is dnf-based Linux servers (the baseline's own fixture
    # value is the literal placeholder "os_version", not a real release).
    os_kernel_version: str = "6.11.4-301.fc41.x86_64"
    # Symlink metadata (path -> the stat dict ``Link`` returns -- the same
    # shape as ``file_stats`` plus a ``link_target`` key -- or False for a
    # path that exists but is not a symlink), backing ``Link``. A separate
    # store from ``file_stats``/``directory_stats`` because a metadata dict
    # carries no type flag, so one store cannot answer the file-, directory-
    # and link-typed questions for the same path. Keys are the caller-supplied
    # path strings verbatim, ``~/...`` prefixes included; an absent key reads
    # as None, like a missing path. ``make_link``/``remove_link`` (the
    # simulated ``ln``/``rm -f`` of ``files.link``) maintain it alongside the
    # bare ``links`` store; a test can also seed rows directly.
    link_stats: dict[str, dict[str, Any] | bool] = field(default_factory=dict)
    # Login records (the parsed ``last`` output), backing ``Last``. Each
    # entry is the dict the fact returns -- {user, tty, host, time} with
    # time the raw trailing string -- in ``last`` order. Defaults to [] --
    # a simulated machine with no recorded logins, like the baseline's
    # ``default = list`` for an empty or failed ``last`` read.
    login_records: list[dict[str, str]] = field(default_factory=list)
    # The Linux distribution info (the parsed /etc/*-release dump), backing
    # ``LinuxDistribution``: {name, family, major, minor, release_meta} in the
    # shape the fact returns. ``LinuxName`` delegates to ``LinuxDistribution``
    # and so reads this same seam -- there is deliberately no separate name
    # field, keeping the two facts consistent by construction. Defaults to
    # The baseline's ``default()`` -- all None with an empty release_meta, the
    # "no release files matched" answer (``family`` is reemote's extension and
    # defaults to None too) -- so a simulated machine makes no distro claim
    # until a test sets one.
    linux_distribution: dict[str, Any] = field(
        default_factory=lambda: {
            "name": None,
            "family": None,
            "major": None,
            "minor": None,
            "release_meta": {},
        }
    )
    # Installed GUI names (the parsed ``ls /usr/bin/*session`` listing),
    # backing ``LinuxGui``: the list of names the fact returns, in listing
    # order. Defaults to [] -- a simulated server is headless, matching the
    # ``has_gui`` flag's False default; a test simulating a desktop should
    # set both seams consistently (``has_gui`` is ``bool(linux_gui)``).
    linux_gui: list[str] = field(default_factory=list)
    # Installed locale names (the parsed ``locale -a`` listing, with ``utf8``
    # already normalized to ``UTF-8``), backing ``Locales``: the list of names
    # the fact returns, in command order. Defaults to [] -- a simulated
    # machine with no locales listed, like the baseline's ``default = list`` for an
    # empty or failed ``locale -a`` read. ``server.locale`` mutates it:
    # ``generate_locales`` (the simulated ``locale-gen``) adds or drops the
    # request's one name, so the next step's fresh fact cache sees the
    # locale as enabled/disabled.
    locales: list[str] = field(default_factory=list)
    # Release information (the parsed ``lsb_release -ca`` rows), backing
    # ``LsbRelease``: the lower-cased key -> stripped value dict the fact
    # returns (``Distributor ID`` already reduced to ``id``). Defaults to
    # {} -- a simulated machine makes no LSB claim until a test sets one,
    # like the fact's answer when the ``lsb_release`` read fails (a missing
    # ``lsb_release`` raises ``CommandNotFoundError`` on the real transport).
    lsb_release: dict[str, str] = field(default_factory=dict)
    # The block device tree (the parsed ``lsblk -J`` node list), backing
    # ``LsBlk``: each node is the ``LsBlkNode`` entry the fact returns --
    # name, type, size (int bytes), mountpoint (None when unmounted) --
    # with stacked devices nested under ``children``. Defaults to [] -- a
    # simulated machine with no block devices listed, like the fact's []
    # fallback for a failed or undecodable read.
    lsblk_devices: list[LsBlkNode] = field(default_factory=list)
    # Simulated LXD: one daemon per host (the remote/project kwargs
    # address that daemon, they do not key the seams). Server-wide seams
    # hold the entry lists / documents their facts return verbatim;
    # instance-scoped seams (``lxd_instance_configs`` /
    # ``lxd_instance_states`` / ``lxd_instance_snapshots``) are keyed by
    # instance name and ``lxd_storage_volumes`` by pool name -- an absent
    # key reads as None (configs/states) or [] (snapshots/volumes). Every
    # fact copies each entry on read, so a test can seed these directly.
    # The ``execute_command`` arms maintain them as the corresponding lxc
    # commands would: a launch fills instances + containers + configs +
    # states (+ an empty snapshot list), a delete cascades all of them
    # plus the instance's storage volumes, a state change updates every
    # status copy, and config/device/profile-assign edits keep the
    # listing entry and the expanded config document in step.
    # The fast container listing (``lxc list --fast``), backing
    # ``LxdContainers`` -- containers only, VMs never appear here.
    lxd_containers: list[LxdContainer] = field(default_factory=list)
    # Image alias -> fingerprint mappings, backing ``LxdImageAliases``.
    lxd_image_aliases: list[LxdImageAlias] = field(default_factory=list)
    # The cached image store, backing ``LxdImages``. A simulated copy
    # derives a deterministic fake fingerprint from sha256(source).
    lxd_images: list[LxdImage] = field(default_factory=list)
    # Per-instance expanded configuration documents, backing
    # ``LxdInstanceConfig`` (own config/devices plus the expanded_*
    # views with profile inheritance applied).
    lxd_instance_configs: dict[str, LxdInstance] = field(default_factory=dict)
    # Per-instance snapshot entries, backing ``LxdInstanceSnapshots``.
    lxd_instance_snapshots: dict[str, list[LxdSnapshot]] = field(default_factory=dict)
    # Per-instance runtime state documents, backing ``LxdInstanceState``.
    lxd_instance_states: dict[str, LxdInstanceStateData] = field(default_factory=dict)
    # The full instance listing (containers AND VMs), backing
    # ``LxdInstances``.
    lxd_instances: list[LxdInstance] = field(default_factory=list)
    # Managed and unmanaged networks, backing ``LxdNetworks``.
    lxd_networks: list[LxdNetwork] = field(default_factory=list)
    # Registered background operations, backing ``LxdOperations``. A
    # cancel or a wait prunes the id (the daemon drops finished
    # operations, and disappearance is the wait's completion signal).
    lxd_operations: list[LxdOperation] = field(default_factory=list)
    # Configuration profiles with their config/devices, backing
    # ``LxdProfiles``.
    lxd_profiles: list[LxdProfile] = field(default_factory=list)
    # Per-project configuration documents, backing ``LxdProjectConfig``.
    lxd_project_configs: dict[str, LxdProject] = field(default_factory=dict)
    # The server's projects, backing ``LxdProjects``.
    lxd_projects: list[LxdProject] = field(default_factory=list)
    # The daemon metadata document, backing ``LxdServerInfo``. Defaults
    # to {} -- a simulated daemon makes no version/capability claim
    # until a test seeds one (the fact's empty-output fallback).
    lxd_server_info: LxdServerInfoData = field(default_factory=dict)
    # The host hardware document, backing ``LxdServerResources``.
    # Defaults to {} for the same reason.
    lxd_server_resources: LxdServerResourcesData = field(default_factory=dict)
    # Storage pools and their drivers, backing ``LxdStoragePools``.
    lxd_storage_pools: list[LxdStoragePool] = field(default_factory=list)
    # Per-pool volume entries (custom AND instance/image volumes),
    # backing ``LxdStorageVolumes``.
    lxd_storage_volumes: dict[str, list[LxdStorageVolume]] = field(default_factory=dict)
    # Mounted filesystems (mount point -> {device, type, options}), backing
    # ``Mounts``. Each value is the parsed form the fact returns, with the
    # options already combined and octal escapes decoded -- the seam holds
    # whichever kernel's listing the host's ``os_kernel`` implies. Defaults
    # to {} -- a simulated machine with no mounts listed, like the baseline's
    # ``default = dict`` for a failed read. ``server.mount`` mutates it:
    # ``mount_filesystem`` records a new mount, ``remount_filesystem`` merges
    # the requested options into the entry and ``unmount_filesystem`` drops
    # the mount point, so the next step's fresh fact cache sees the change.
    mounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Simulated MySQL: one server per host (the connection kwargs address
    # that server, they do not key the seams). Server-wide seams hold the
    # entry lists / name-keyed dicts their facts return verbatim;
    # database-scoped seams (``mysql_indexes`` / ``mysql_tables``) are
    # keyed by database name and ``mysql_columns`` by "database.table" --
    # an absent key reads as []. ``mysql_users`` is keyed "user@host";
    # ``mysql_user_grants`` is keyed "user@host" -> grant target ->
    # privilege list, where targets carry the server's own rendering
    # (backticked "`db`.*", or "*.*" for global grants) and a role
    # membership is the backticked "`role`@`host`" target holding the
    # single "ROLE" privilege. Every fact copies each entry on read, so a
    # test can seed these directly. The ``execute_command`` arms maintain
    # them as the corresponding mysql commands would.
    # Binary-log files (SHOW BINARY LOGS), backing ``MysqlBinaryLogs``.
    mysql_binary_logs: list[MysqlBinaryLog] = field(default_factory=list)
    # Column definitions per "database.table", backing ``MysqlColumns``.
    mysql_columns: dict[str, list[MysqlColumn]] = field(default_factory=dict)
    # Active client threads (SHOW PROCESSLIST without INFO), backing
    # ``MysqlConnections``.
    mysql_connections: list[MysqlConnection] = field(default_factory=list)
    # The server's databases (name -> charset/collation), backing
    # ``MysqlDatabases``.
    mysql_databases: dict[str, MysqlDatabase] = field(default_factory=dict)
    # Storage engines (information_schema.ENGINES), backing ``MysqlEngines``.
    mysql_engines: list[MysqlEngine] = field(default_factory=list)
    # Index definitions per database, backing ``MysqlIndexes``.
    mysql_indexes: dict[str, list[MysqlIndex]] = field(default_factory=dict)
    # Installed plugins (information_schema.PLUGINS), backing ``MysqlPlugins``.
    mysql_plugins: list[MysqlPlugin] = field(default_factory=list)
    # Per-channel replica status (SHOW REPLICA STATUS), backing
    # ``MysqlReplication``.
    mysql_replication: list[MysqlReplicaStatus] = field(default_factory=list)
    # Runtime counters (SHOW GLOBAL STATUS), backing ``MysqlStatus``.
    mysql_status: dict[str, str] = field(default_factory=dict)
    # User tables per database, backing ``MysqlTables``.
    mysql_tables: dict[str, list[MysqlTable]] = field(default_factory=dict)
    # SHOW GRANTS output per "user@host" account (grant target -> the
    # sorted privilege list), backing ``MysqlUserGrants``.
    mysql_user_grants: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # The server's accounts ("user@host" -> mysql.user attributes),
    # backing ``MysqlUsers``.
    mysql_users: dict[str, MysqlUser] = field(default_factory=dict)
    # Global system variables (SHOW GLOBAL VARIABLES), backing
    # ``MysqlVariables``.
    mysql_variables: dict[str, str] = field(default_factory=dict)
    # Network devices (interface -> the ``NetworkDevice`` entry the fact
    # returns -- {state, ether?, mtu?, ipv4?, ipv6?}), backing
    # ``NetworkDevices``. Each value is the parsed form the fact returns
    # -- an ipv4/ipv6 block holds the primary address plus its
    # mask/broadcast details and any ``additional_ips`` aliases. Defaults
    # to {} -- a simulated machine with no interfaces listed, like the baseline's
    # ``default = dict`` for a failed read.
    network_devices: dict[str, NetworkDevice] = field(default_factory=dict)
    # Listening sockets (``(protocol, port)`` -> the ``PortInfo`` record the
    # fact returns), backing ``Port`` and polled by ``server.wait``. An
    # unlisted port reads as "nothing listening", like a command whose port
    # filter matches no row; the read copies the record, so a test can seed
    # these directly. A read-only seam: a wait never mutates state (see
    # ``check_wait``), so a test that wants a port to appear seeds it between
    # steps -- or lets a command arm (``start_service``, a future listener
    # simulator) add it.
    listening_ports: dict[tuple[str, int], PortInfo] = field(default_factory=dict)
    # Running processes (PID -> {user, state, cpu_percent, mem_percent,
    # command, args}), backing ``Processes``. Each value is the parsed form
    # the fact returns -- the seam holds the joined record regardless of
    # which kernel's ps listing ``os_kernel`` implies. Defaults to {} -- a
    # simulated machine with no processes listed, like the baseline's
    # ``default = dict`` for a failed ps read. ``server.kill`` mutates it:
    # ``kill_process`` drops the PID, so the next step's fresh fact cache
    # no longer lists the killed process.
    processes: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Simulated PostgreSQL: one cluster per host. Cluster-wide seams
    # (``postgres_connections`` / ``postgres_databases`` / ``postgres_roles``
    # / ``postgres_role_memberships`` / ``postgres_settings`` /
    # ``postgres_replication`` / ``postgres_tablespaces``) hold the entry
    # lists / name-keyed dicts their facts return verbatim; database-scoped
    # seams (``postgres_extensions`` / ``postgres_indexes`` /
    # ``postgres_privileges`` / ``postgres_publications`` /
    # ``postgres_schemas`` / ``postgres_subscriptions`` / ``postgres_tables``)
    # are keyed by database name, with the fact's default database
    # (``psql_database or "postgres"``) selecting the entry list -- an
    # absent key reads as []. Every fact copies each entry on read, so a
    # test can seed these directly. The ``execute_command`` arms maintain
    # them as the corresponding psql commands would.
    # Active client connections (pg_stat_activity without the query text),
    # backing ``PostgresqlConnections``.
    postgres_connections: list[PostgresqlConnection] = field(default_factory=list)
    # The cluster's databases (name -> pg_database metadata), backing
    # ``PostgresqlDatabases``.
    postgres_databases: dict[str, PostgresqlDatabase] = field(default_factory=dict)
    # Installed extensions per database, backing ``PostgresqlExtensions``.
    postgres_extensions: dict[str, list[PostgresqlExtension]] = field(
        default_factory=dict
    )
    # User indexes per database, backing ``PostgresqlIndexes``.
    postgres_indexes: dict[str, list[PostgresqlIndex]] = field(default_factory=dict)
    # Object-level grants per database, backing ``PostgresqlPrivileges``.
    postgres_privileges: dict[str, list[PostgresqlPrivilege]] = field(
        default_factory=dict
    )
    # Logical-replication publications per database, backing
    # ``PostgresqlPublications``.
    postgres_publications: dict[str, list[PostgresqlPublication]] = field(
        default_factory=dict
    )
    # Streaming-replication standbys (pg_stat_replication), backing
    # ``PostgresqlReplication``.
    postgres_replication: list[PostgresqlReplicationClient] = field(
        default_factory=list
    )
    # Role-to-role grants (pg_auth_members), backing
    # ``PostgresqlRoleMemberships``.
    postgres_role_memberships: list[PostgresqlRoleMembership] = field(
        default_factory=list
    )
    # The cluster's roles (name -> pg_roles attributes), backing
    # ``PostgresqlRoles``.
    postgres_roles: dict[str, PostgresqlRole] = field(default_factory=dict)
    # Non-system schemas per database, backing ``PostgresqlSchemas``.
    postgres_schemas: dict[str, list[PostgresqlSchema]] = field(default_factory=dict)
    # Active GUC configuration (pg_settings), backing ``PostgresqlSettings``.
    postgres_settings: list[PostgresqlSetting] = field(default_factory=list)
    # Logical-replication subscriptions per database (conninfo deliberately
    # absent), backing ``PostgresqlSubscriptions``.
    postgres_subscriptions: dict[str, list[PostgresqlSubscription]] = field(
        default_factory=dict
    )
    # User tables per database, backing ``PostgresqlTables``.
    postgres_tables: dict[str, list[PostgresqlTable]] = field(default_factory=dict)
    # Tablespaces and their locations, backing ``PostgresqlTablespaces``.
    postgres_tablespaces: list[PostgresqlTablespace] = field(default_factory=list)
    # Decoded SMBIOS tables (section title -> the field dict the fact
    # returns), backing ``SmbiosInfo``. Each field value is a raw string,
    # except a multi-item field (e.g. ``Characteristics``) whose value is
    # the list of item strings. Defaults to {} -- a simulated machine
    # whose dmidecode read fails (no binary, no root or passwordless
    # sudo), like the fact's {} fallback for a failed read.
    smbios_info: dict[str, dict[str, str | list[str]]] = field(default_factory=dict)
    # The system timezone (the ``readlink -f /etc/localtime | sed`` answer),
    # backing ``Timezone``. Named ``os_timezone`` with the os_ prefix (like
    # ``os_kernel``) so it stays clear of any inventory field. Defaults to
    # The baseline's own fact fixture timezone -- a real machine always resolves
    # ``/etc/localtime``, and a fixed default keeps simulated reads
    # deterministic. ``set_timezone`` (the simulated ``server.timezone``
    # effect) replaces it, so a repeat timezone set is a no-op.
    os_timezone: str = "Europe/Amsterdam"
    # The current user's name (the ``echo $USER`` answer), backing ``User``.
    # Named ``os_user`` with the os_ prefix (like ``os_env``) so it stays
    # clear of the inventory's ``connection.user`` login name. Defaults to
    # ``root`` -- a real machine always answers ``echo $USER``, and a fixed
    # default keeps simulated reads deterministic (the baseline ships no fixture
    # for this fact).
    os_user: str = "root"
    # System users (username -> the details dict ``Users`` returns -- home,
    # comment, shell, group, groups, uid, gid, lastlog, login_time,
    # password), backing ``Users``. Named ``os_users`` with the os_ prefix
    # (like ``os_user``) so it stays clear of the inventory's
    # ``connection.user`` login name. Defaults to {} -- a simulated machine
    # with no users listed, like the baseline's ``default = dict`` for a failed
    # or empty read. ``add_user``/``modify_user``/``remove_user`` (the
    # simulated ``useradd``/``usermod``/``userdel`` behind ``server.user``)
    # mutate it.
    os_users: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The seconds since boot (the parsed ``cut -d. -f1 /proc/uptime`` answer,
    # or the BSD now-minus-boottime difference), backing ``Uptime``. Defaults
    # to the baseline's own fact fixture uptime (86400 -- one day) -- a real
    # machine always answers with an uptime, and a fixed default keeps
    # simulated reads deterministic.
    uptime_seconds: int = 86400
    # Resolvable commands (command name -> the path ``command -v`` prints),
    # backing ``Which``. An absent command reads as None, like the swallowed
    # non-zero exit of ``command -v nosuchcommand || true``. Defaults to {} --
    # a simulated machine resolves no commands until a test names them.
    command_paths: dict[str, str] = field(default_factory=dict)
    # Simulated SELinux booleans (name -> on/off bool), backing ``Booleans``
    # (the whole map) and ``SEBoolean`` (one name projected to "on"/"off",
    # an absent name ""). ``set_selinux_boolean`` (the simulated
    # ``setsebool``) edits it.
    selinux_booleans: dict[str, bool] = field(default_factory=dict)
    # The runtime context of labelled paths (path -> SeContext), backing
    # ``FileContext``. ``chcon_path`` (the simulated ``chcon``) edits the
    # named components; ``relabel_path`` (the simulated ``restorecon``)
    # reapplies the fcontext database to them.
    selinux_file_contexts: dict[str, SeContext] = field(default_factory=dict)
    # Persistent fcontext database rules (target expression -> SeContext),
    # backing ``FileContextMapping``. ``manage_selinux_fcontext`` (the
    # simulated ``semanage fcontext -a/-m/-d``) edits it; a new rule seeds
    # the stock ``system_u:object_r:<type>:s0`` context.
    selinux_fcontext_mappings: dict[str, SeContext] = field(default_factory=dict)
    # Port type labels (protocol -> port -> type), backing ``SEPorts`` (the
    # whole map, optionally filtered) and ``SEPort`` (one pair projected).
    # ``manage_selinux_port`` (the simulated ``semanage port -a/-m/-d``)
    # edits it, dropping a protocol left empty.
    selinux_ports: dict[str, dict[int, str]] = field(default_factory=dict)
    # The SELinux status record, backing ``Mode``. Defaults to an
    # enforcing/targeted host; ``set_selinux_mode`` (the simulated
    # ``setenforce``) edits the runtime ``mode`` key only, mirroring
    # setenforce's runtime-only effect, while ``install_selinux_config_file``
    # (the ``selinux.config`` install) refreshes ``config_mode`` from the
    # installed file's SELINUX assignment -- sestatus reads the file live.
    selinux_mode: ModeInfo = field(
        default_factory=lambda: ModeInfo(
            mode="enforcing", config_mode="enforcing", policy="targeted"
        )
    )
    # Linux login -> SELinux user mappings, backing ``SELogin``.
    # ``manage_selinux_login`` (the simulated ``semanage login -a/-m/-d``)
    # edits it.
    selinux_logins: dict[str, SeLogin] = field(default_factory=dict)
    # SELinux user definitions (seuser -> SeUser), backing ``SEUser``. A
    # read-only seam: no operation manages user definitions, so a test
    # seeds it directly.
    selinux_users: dict[str, SeUser] = field(default_factory=dict)
    # Installed policy modules, backing ``Modules``. The semodule mutators
    # edit it: install appends an enabled instance, remove drops the
    # priority's instance, enable/disable flips every instance of the name.
    selinux_modules: list[Module] = field(default_factory=list)
    # Permissive domain exemptions, backing ``PermissiveDomains``.
    # ``manage_selinux_permissive`` (the simulated ``semanage permissive
    # -a/-d``) edits it.
    selinux_permissive_domains: list[str] = field(default_factory=list)
    # Per-package snap identity (package name -> the ``SnapPackageInfo`` dict
    # ``SnapPackage`` returns -- name, publisher, snap_id, channel, version),
    # backing ``SnapPackage`` and, through its key set, the ``SnapPackages``
    # name list (``SnapPackages`` projects ``list(store)``, so the two snap
    # facts can never disagree on a simulated host). A name absent from the
    # mapping reads as None, like an absent package printing no tracked
    # ``snap info`` row. Named with
    # the snap_ prefix and kept in its own store so the snap-side state never
    # mixes with the apk/apt/dnf package-detail seams. Defaults to {} -- a
    # simulated machine with no snaps installed. ``install_snap_packages``,
    # ``remove_snap_packages`` and ``refresh_snap_packages`` (the simulated
    # ``snap.package``) maintain it; a test may also seed it directly.
    snap_package_info: dict[str, SnapPackageInfo] = field(default_factory=dict)
    # Socket metadata (path -> the stat dict ``Socket`` returns -- the
    # same shape as ``file_stats`` -- or False for a path that exists but
    # is not a socket), backing ``Socket``. A separate store from
    # ``file_stats``/``directory_stats``/``link_stats`` because a metadata
    # dict carries no type flag, so one store cannot answer the
    # socket-typed question for the same path. Keys are the caller-supplied
    # path strings verbatim, ``~/...`` prefixes included; an absent key
    # reads as None, like a missing path. A read-only seam: a test seeds
    # it directly; the read copies the dict.
    socket_stats: dict[str, dict[str, Any] | bool] = field(default_factory=dict)
    # Sysctl settings (key -> the parsed value ``Sysctl`` returns -- an int,
    # a string, or a list of ints/strings), backing the relocated
    # ``sysctl.Sysctl`` fact. A keys filter selects the named settings; an
    # absent key reads as missing, like the swallowed ``sysctl nosuchkey``
    # error. Defaults to {} -- a simulated machine with no settings listed,
    # like the baseline's ``default = dict`` for an empty or failed read.
    # ``set_sysctl`` (the simulated ``sysctl -w`` behind ``sysctl.setting``)
    # and ``reload_sysctl_file`` (the ``sysctl -p`` arm) mutate it; the
    # demo-side ``sysctl`` recipe step stays a raw ``StringCommand`` with no
    # simulated effect.
    sysctls: dict[str, Any] = field(default_factory=dict)
    # Simulated systemd visibility -- the read-side seams for the seven
    # reemote-original facts (the two the baseline ports project the ``services``/
    # ``enabled_services`` stores above). A test seeds each directly; the
    # facts issue no write command, so no fact-only arm maintains them. The
    # ``mask``/``preset`` operations (which change a unit's boot state) do
    # mutate ``systemd_unit_files`` -- see their execute_command arms. The
    # scope flags (user_mode/machine/user_name) are ignored: one system-scope
    # store per fact, a documented approximation the real transport renders
    # faithfully.
    #
    # A unit's dependency edges (full unit name -> property -> unit-name
    # list), backing ``SystemdDependencies``. An absent unit reads as {}; the
    # read copies each list.
    systemd_dependencies: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # The failed-unit table (the parsed ``SystemdFailedUnit`` entries),
    # backing ``SystemdFailedUnits``. Defaults to [] -- a healthy host with no
    # failed units; the read copies each entry dict.
    systemd_failed_units: list[SystemdFailedUnit] = field(default_factory=list)
    # The ``is-system-running`` health word (``running``/``degraded``/...),
    # backing ``SystemdSystemState``. The empty-string default projects to
    # ``unknown`` -- a simulated host whose systemctl could not be read.
    systemd_system_state: str = ""
    # The ``*.timer`` schedule (timer unit name -> ``SystemdTimer`` entry),
    # backing ``SystemdTimers``; the read copies each entry dict.
    systemd_timers: dict[str, SystemdTimer] = field(default_factory=dict)
    # The installed unit files (unit name -> ``SystemdUnitFile`` state/preset),
    # backing ``SystemdUnitFiles`` and read by the ``preset`` operation; the
    # ``mask``/``unmask``/``preset`` arms mutate the named unit's state.
    systemd_unit_files: dict[str, SystemdUnitFile] = field(default_factory=dict)
    # A unit's curated ``systemctl show`` properties (full unit name -> the
    # raw Key=Value dict), backing ``SystemdUnitProperties``. An absent unit
    # reads as {}; the read copies the property dict. The ``mask``/``unmask``/
    # ``preset`` arms also update the unit's ``UnitFileState`` here (the ``mask``
    # operation reads it for idempotence), keeping it in sync with
    # ``systemd_unit_files``.
    systemd_unit_properties: dict[str, dict[str, str]] = field(default_factory=dict)
    # The ``systemctl --version`` banner (a partial/empty dict normalizes to
    # version None), backing ``SystemdVersion``. Defaults to {} -- a simulated
    # host whose systemctl could not be read.
    systemd_version: dict[str, Any] = field(default_factory=dict)
    # Simulated ufw: the status record backing ``UfwStatus`` (the active
    # flag, the logging level word and the three default policies).
    # Defaults to the inactive/empty record -- an unseeded host reads like
    # an inactive or uninstalled ufw (the fact's unprivileged answer).
    # ``set_ufw_state`` (the enable/disable arm), ``set_ufw_default_policy``
    # and ``set_ufw_logging`` mutate it; ``reset_ufw`` restores the empty
    # record alongside the ruleset.
    ufw_status: UfwStatusInfo = field(
        default_factory=lambda: UfwStatusInfo(
            active=False,
            logging="",
            default_incoming="",
            default_outgoing="",
            default_routed="",
        )
    )
    # The numbered ruleset backing ``UfwRules`` -- ``UfwRule`` entries in
    # listing order (the v4 section then the v6 twins, as ``ufw status
    # numbered`` prints them; ``manage_ufw_rule`` renumbers after every
    # mutation). Defaults to [] -- an inactive firewall reports no rules.
    ufw_rules: list[UfwRule] = field(default_factory=list)
    # Installed application profile names backing ``UfwApplications`` (the
    # ``ufw app list`` answer, the names ``ufw.rule(app=...)`` references).
    # A read-only seam: no operation installs profiles, so a test seeds it
    # directly.
    ufw_applications: list[str] = field(default_factory=list)
    # Side effects of installing a package: package -> {link src: target}
    # and package -> {file path}. This is what lets the demo reproduce the
    # design doc section 9 trace: installing a package creates the default
    # site's link or ships files (e.g. httpd's welcome.conf), which the
    # *next* operation then sees because the fact cache is recreated per
    # step.
    installs_links: dict[str, dict[str, str]] = field(default_factory=dict)
    installs_files: dict[str, set[str]] = field(default_factory=dict)

    # Command seam: reemote.drivers.send_command forwards here. The match
    # on the typed descriptors lives in the test framework, not the library.

    def execute_command(self, cmd: Command) -> None:
        """Apply the simulated effect of a command descriptor.

        A raw ``StringCommand`` is accepted but has no simulated effect; an
        unknown descriptor is a hard error so new command types cannot be
        silently ignored.
        """
        match cmd:
            case ApkHoldCommand(packages=packages, present=False):
                self.unhold_apk_packages(packages)
            case ApkHoldCommand(packages=packages):
                self.hold_apk_packages(packages)
            case ApkInstallCommand(packages=packages):
                self.install_apk_packages(packages)
            case ApkPurgeCommand():
                self.autoremove_apk_packages()
            case ApkRemoveCommand(packages=packages):
                self.remove_apk_packages(packages)
            case ApkRepoInstallCommand(spool=spool, dest=dest):
                self.install_apk_repo_file(spool, dest)
            case ApkRepoUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case ApkUpgradeCommand():
                pass  # the simulation tracks no newer versions to upgrade to
            case ApkVirtualCommand(tag=tag, packages=packages, present=False):
                self.remove_apk_virtual(tag, packages)
            case ApkVirtualCommand(tag=tag, packages=packages):
                self.add_apk_virtual(tag, packages)
            case DnfInstallCommand(packages=packages):
                self.install_packages(packages)
            case DnfDowngradeCommand(packages=packages):
                self.downgrade_packages(packages)
            case DnfRemoveCommand(packages=packages):
                self.remove_packages(packages)
            case DnfUpdateCommand() | DnfCleanCommand() | DnfMakecacheCommand():
                pass  # the simulation tracks no versions/metadata to update
            case DnfRepoUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case DnfRepoInstallCommand(spool=spool, dest=dest, mode=mode):
                self.install_repo_file(spool, dest, int(mode))
            case DnfRepoRemoveCommand(path=path):
                self.remove_repo_file(path)
            case RpmInstallCommand(src=src):
                self.install_rpm(src)
            case DebInstallCommand(src=src):
                self.install_deb(src)
            case AptInstallFixCommand():
                pass  # the simulation models no dependency resolution
            case AptRemoveCommand(packages=packages):
                self.remove_apt_packages(packages)
            case AptInstallCommand(packages=packages):
                self.install_apt_packages(packages)
            case AptUpdateCommand():
                pass  # the simulation tracks no package-list metadata to refresh
            case AptUpgradeCommand():
                self.upgrade_apt_packages()
            case AptDistUpgradeCommand():
                self.dist_upgrade_apt_packages()
            case AptAutoRemoveCommand():
                self.auto_remove_apt_packages()
            case AptMarkHoldCommand(packages=packages):
                self.hold_apt_packages(packages)
            case AptMarkUnholdCommand(packages=packages):
                self.unhold_apt_packages(packages)
            case AptPpaCommand(src=src, present=False):
                self.remove_ppa(src)
            case AptPpaCommand(src=src):
                self.add_ppa(src)
            case AptRepoUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case AptRepoInstallCommand(spool=spool, dest=dest):
                self.install_apt_repo_file(spool, dest)
            case AptSourcesFileUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case AptSourcesFileInstallCommand(spool=spool, dest=dest):
                self.install_apt_sources_file(spool, dest)
            case AptSourcesFileRemoveCommand(path=path):
                self.remove_apt_sources_file(path)
            case DnfKeyUploadCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case DnfKeyImportCommand(path=path):
                self.import_key(path)
            case DnfKeyRemoveCommand(keyid=keyid, release=release):
                self.remove_rpm_gpg_key(keyid, release)
            case AptKeyUploadCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case AptKeyInstallCommand(spool=spool, dest=dest):
                self.install_apt_key(spool, dest)
            case GpgKeyUploadCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case GpgKeyInstallCommand(spool=spool, dest=dest):
                self.install_gpg_key(spool, dest)
            case GpgKeyRemoveCommand(path=path):
                self.remove_gpg_keyring(path)
            case GpgKeyDeleteKeysCommand(keyring=keyring, fingerprints=fingerprints):
                self.delete_gpg_keys(keyring, fingerprints)
            case GpgKeyEmptyCleanupCommand(keyring=keyring):
                self.cleanup_empty_gpg_keyring(keyring)
            case GpgDearmorCommand(src=src, dest=dest):
                self.dearmor_gpg_key(src, dest)
            case GpgImportUploadCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case GpgImportCommand(spool=spool):
                self.import_gpg_key(spool)
            case GpgExportCommand(keyid=keyid, dest=dest, armor=armor, secret=secret):
                self.export_gpg_key(keyid, dest, armor, secret)
            case GpgDeleteKeyCommand(fingerprint=fingerprint, secret=secret):
                self.delete_gpg_key(fingerprint, secret)
            case GpgGenKeyCommand(
                real_name=real_name, email=email, algo=algo, length=length, expire=expire
            ):
                self.gen_gpg_key(real_name, email, algo, length, expire)
            case GpgTrustCommand(fingerprint=fingerprint, ownertrust=ownertrust):
                self.set_gpg_trust(fingerprint, ownertrust)
            case GpgRefreshKeysCommand():
                pass  # refresh contacts keyservers; no key material is modeled
            case DnfModuleEnableCommand(module=module, stream=stream):
                self.enable_module(module, stream)
            case DnfModuleDisableCommand(module=module):
                self.disable_module(module)
            case LinkCommand(path=path, target=None):
                self.remove_link(path)
            case LinkCommand(path=path, target=target, symbolic=symbolic):
                self.make_link(path, target, symbolic)
            case FileCommand(path=path, present=False):
                self.remove_file(path)
            case FileCommand(path=path):
                self.create_file(path)
            case PutCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case GetCommand(src=src, dest=dest):
                self.get_file(src, dest)
            case InstallSpoolCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case InstallValidateCommand(validate=validate, spool=spool):
                # The atomic validate-then-install step. A real validator exits
                # non-zero on a bad config and send_command's fail-fast aborts
                # before the InstallCommand runs; the simulation models no
                # config parser, so validation passes unless a test seeds the
                # spool into fail_validate to drive the abort.
                if spool in self.fail_validate:
                    raise CommandError(
                        self.name, validate, 1, "simulated validation failure"
                    )
            case InstallCommand(
                spool=spool, dest=dest, mode=mode, user=user, group=group
            ):
                self.install_file(spool, dest, mode, user, group)
            case MoveCommand(src=src, dest=dest):
                self.move_path(src, dest)
            case RemoveTreeCommand(src=src):
                self.remove_tree(src)
            case CopyCommand(src=src, dest=dest):
                self.copy_path(src, dest)
            case MkdirCommand(path=path):
                self.make_directory(path)
            case _HomeMkdirCommand(path=path):
                # server.user's inlined home-directory creation rides the
                # same mutation as files.directory's mkdir descriptor.
                self.make_directory(path)
            case _KeysTouchCommand(path=path):
                # server.user_authorized_keys's inlined authorized_keys
                # file creation rides the same touch mutation as files.file's
                # FileCommand descriptor; the paired _KeysChmodCommand then
                # rewrites the seeded 644 row to the operation's 600 mode.
                self.create_file(path)
            case ChattrCommand(path=path, flags=flags, present=False):
                self.clear_file_flags(path, flags)
            case ChattrCommand(path=path, flags=flags):
                self.set_file_flags(path, flags)
            case ChmodCommand(path=path, mode=mode):
                self.chmod_path(path, mode)
            case _KeysChmodCommand(path=path, mode=mode):
                # server.user_authorized_keys's inlined chmod for the .ssh
                # directory and the key file rides the same mutation as
                # files.directory's chmod descriptor.
                self.chmod_path(path, mode)
            case ChownCommand(path=path, user=user, group=group, dereference=dereference):
                self.chown_path(path, user, group, dereference)
            case _HomeChownCommand(path=path, user=user, group=group):
                # server.user's inlined home-ownership change rides the same
                # mutation as files.directory's chown descriptor.
                self.chown_path(path, user, group)
            case AuthorizedKeysUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case AuthorizedKeysInstallCommand(spool=spool, dest=dest):
                # server.user_authorized_keys's install mutation updates the
                # key-file content, its existence/metadata seams and the
                # AuthorizedKeys fact seam in one place, so a repeat run
                # reads the keys as already written.
                self.install_authorized_keys_file(spool, dest)
            case EtcHostsUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case EtcHostsInstallCommand(spool=spool, dest=dest):
                # server.etc_hosts's install mutation stores the hosts-file
                # content plus its existence/metadata seams; the EtcHosts
                # fact parses remote_files directly, so no second store
                # needs refreshing and a repeat run reads the entry as
                # already written.
                self.install_etc_hosts_file(spool, dest)
            case LocaleUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case LocaleInstallCommand(spool=spool, dest=dest):
                # server.locale's install mutation stores the locale.gen
                # content plus its existence/metadata seams; the paired
                # LocaleGenCommand then flips the Locales seam, so a repeat
                # run reads the locale as already enabled/disabled.
                self.install_locale_gen_file(spool, dest)
            case LocaleGenCommand(locale=locale, present=present):
                self.generate_locales(locale, present)
            case SecurityLimitUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case SecurityLimitInstallCommand(spool=spool, dest=dest):
                # server.security_limit's install mutation stores the
                # limits.conf content plus its existence/metadata seams;
                # AuthorizedKeysFile reads remote_files directly, so no
                # second store needs refreshing and a repeat run reads the
                # limit line as already set.
                self.install_security_limits_file(spool, dest)
            case GitConfigCommand(
                key=key,
                value=value,
                multi_value=multi_value,
                repo=repo,
                system=system,
            ):
                self.set_git_config(key, value, multi_value, repo, system)
            case GitCheckoutCommand(dest=dest, branch=branch):
                self.checkout_branch(dest, branch)
            case GitCloneCommand(src=src, dest=dest, branch=branch):
                self.clone_repo(src, dest, branch)
            case GitFetchCommand():
                pass  # fetch only advances remote refs the facts do not read back
            case GitRemoteSetUrlCommand(dest=dest, url=url):
                self.set_git_remote_url(dest, url)
            case GitRepoPullCommand(dest=dest, branch=branch):
                self.pull_repo(dest, branch)
            case GitSubmoduleUpdateCommand():
                pass  # no migrated fact reads submodule sync state back
            case GitWorktreeAddCommand(worktree=worktree):
                self.add_worktree(worktree)
            case GitWorktreeRemoveCommand(worktree=worktree):
                self.remove_worktree(worktree)
            case GitPullCommand(
                worktree=worktree, from_remote_branch=from_remote_branch
            ):
                self.pull_worktree(worktree, from_remote_branch)
            case StringCommand():
                pass  # no simulated effect for raw shell strings
            case GroupAddCommand(group=name):
                self.add_group(name)
            case GroupDelCommand(group=name):
                self.remove_group(name)
            case UserAddCommand():
                self.add_user(cmd)
            case UserModCommand():
                self.modify_user(cmd)
            case UserDelCommand(user=name):
                self.remove_user(name)
            case SetHostnameCommand(hostname=name):
                self.set_hostname(name)
            case SetTimezoneCommand(timezone=timezone):
                self.set_timezone(timezone)
            case KillCommand(pid=pid):
                self.kill_process(pid)
            case ModprobeCommand(modules=modules, present=False):
                self.unload_modules(modules)
            case ModprobeCommand(modules=modules):
                self.load_modules(modules)
            case MountCommand(path=path, mounted=False):
                self.unmount_filesystem(path)
            case MountCommand(path=path, options=options, remount=True):
                self.remount_filesystem(path, options)
            case MountCommand(path=path, device=device, fs_type=fs_type, options=options):
                self.mount_filesystem(path, device, fs_type, options)
            case SystemctlCommand(action="daemon-reload"):
                pass  # the simulation tracks no unit files to reload
            case SystemctlCommand(
                action="start" | "restart" | "reload" | "reload-or-try-restart",
                unit=unit,
            ):
                self.services[unit] = "active"
            case SystemctlCommand(action="stop", unit=unit):
                self.services[unit] = "inactive"
            case SystemctlCommand(action="reset-failed", unit=unit):
                # reset-failed clears a failed unit back to inactive; any
                # other state is left alone (the real command is a no-op then).
                if self.services.get(unit) == "failed":
                    self.services[unit] = "inactive"
            case SystemctlCommand(action="enable", unit=unit, now=now):
                self.enabled_services.add(unit)
                if now:
                    self.services[unit] = "active"
            case SystemctlCommand(action="disable", unit=unit, now=now):
                self.enabled_services.discard(unit)
                if now:
                    self.services[unit] = "inactive"
            case SystemctlCommand(action="mask", unit=unit):
                self.mask_systemd_unit(unit)
            case SystemctlCommand(action="unmask", unit=unit):
                self.unmask_systemd_unit(unit)
            case SystemctlCommand(action="preset", unit=unit):
                self.preset_systemd_unit(unit)
            case SystemctlCommand(action="preset-all"):
                self.preset_all_systemd_units()
            case SystemdFileUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case SystemdFileInstallCommand(spool=spool, dest=dest):
                self.install_systemd_conf_file(spool, dest)
            case SystemdFileRemoveCommand(path=path):
                self.remove_systemd_conf_file(path)
            case SysctlSetCommand(key=key, value=value):
                self.set_sysctl(key, value)
            case SysctlFileUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case SysctlFileInstallCommand(spool=spool, dest=dest):
                self.install_sysctl_conf_file(spool, dest)
            case SysctlFileRemoveCommand(path=path):
                self.remove_sysctl_conf_file(path)
            case SysctlReloadCommand(path=path):
                self.reload_sysctl_file(path)
            case SystemdTmpfilesCommand():
                pass  # the simulation models no runtime files/dirs to create
            case SystemdSysusersCommand():
                pass  # the simulation models no users/groups to create
            case RcServiceCommand(service=service, action=action):
                self.set_openrc_service_state(service, action)
            case RcUpdateCommand(service=name, runlevel=level, present=False, stack=True):
                self.unstack_openrc_runlevel(name, level)
            case RcUpdateCommand(service=name, runlevel=level, stack=True):
                self.stack_openrc_runlevel(name, level)
            case RcUpdateCommand(service=name, runlevel=level, present=False):
                self.disable_openrc_service(name, level)
            case RcUpdateCommand(service=name, runlevel=level):
                self.enable_openrc_service(name, level)
            case RunlevelCommand(runlevel=level, present=False):
                self.remove_openrc_runlevel(level)
            case RunlevelCommand(runlevel=level):
                self.create_openrc_runlevel(level)
            case OpenrcConfigUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case OpenrcConfigInstallCommand(spool=spool, dest=dest):
                self.install_openrc_config_file(spool, dest)
            case MysqlDatabaseCommand():
                self.manage_mysql_database(cmd)
            case MysqlDumpCommand():
                self.dump_mysql_database(cmd)
            case MysqlIndexCommand():
                self.manage_mysql_index(cmd)
            case MysqlKillCommand():
                self.kill_mysql_connection(cmd)
            case MysqlLoadCommand():
                pass  # the simulation executes no SQL, so a load changes no catalog
            case MysqlOptimizeCommand():
                pass  # maintenance refreshes statistics the simulation does not model
            case MysqlPluginCommand():
                self.manage_mysql_plugin(cmd)
            case MysqlPrivilegesCommand():
                self.manage_mysql_privileges(cmd)
            case MysqlReplicaCommand():
                self.manage_mysql_replica(cmd)
            case MysqlRoleCommand():
                self.manage_mysql_role(cmd)
            case MysqlSqlCommand():
                pass  # the simulation interprets no SQL (FLUSH PRIVILEGES included)
            case MysqlTableCommand():
                self.manage_mysql_table(cmd)
            case MysqlUserCommand():
                self.manage_mysql_user(cmd)
            case MysqlVariableCommand():
                self.set_mysql_variable(cmd)
            case PostgresqlDatabaseCommand():
                self.manage_postgresql_database(cmd)
            case PostgresqlDumpCommand():
                self.dump_postgresql_database(cmd)
            case PostgresqlExtensionCommand():
                self.manage_postgresql_extension(cmd)
            case PostgresqlIndexCommand():
                self.manage_postgresql_index(cmd)
            case PostgresqlLoadCommand():
                pass  # the simulation executes no SQL, so a load changes no catalog
            case PostgresqlMembershipCommand():
                self.manage_postgresql_membership(cmd)
            case PostgresqlPrivilegeCommand():
                self.manage_postgresql_privileges(cmd)
            case PostgresqlPublicationCommand():
                self.manage_postgresql_publication(cmd)
            case PostgresqlRoleCommand():
                self.manage_postgresql_role(cmd)
            case PostgresqlSchemaCommand():
                self.manage_postgresql_schema(cmd)
            case PostgresqlSettingCommand():
                self.set_postgresql_setting(cmd)
            case PostgresqlSqlCommand():
                pass  # the simulation interprets no SQL (pg_reload_conf included)
            case PostgresqlSubscriptionCommand():
                self.manage_postgresql_subscription(cmd)
            case PostgresqlTablespaceCommand():
                self.manage_postgresql_tablespace(cmd)
            case SetseboolCommand(boolean=boolean, value=value):
                self.set_selinux_boolean(boolean, value)
            case ChconCommand():
                self.chcon_path(cmd)
            case SemanageFcontextCommand():
                self.manage_selinux_fcontext(cmd)
            case SemanagePortCommand():
                self.manage_selinux_port(cmd)
            case SetenforceCommand(mode=mode):
                self.set_selinux_mode(mode)
            case SemanageLoginCommand():
                self.manage_selinux_login(cmd)
            case SemoduleInstallCommand(source=source, priority=priority):
                self.install_selinux_module(source, priority)
            case SemoduleRemoveCommand(name=name, priority=priority):
                self.remove_selinux_module(name, priority)
            case SemoduleEnableCommand(name=name, present=present):
                self.enable_selinux_module(name, present)
            case SemanagePermissiveCommand():
                self.manage_selinux_permissive(cmd)
            case RestoreconCommand():
                self.relabel_path(cmd)
            case AutorelabelCommand():
                self.create_file(AUTORELABEL_PATH)
            case SelinuxConfigUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case SelinuxConfigInstallCommand(spool=spool, dest=dest):
                self.install_selinux_config_file(spool, dest)
            case IptablesChainCommand():
                self.manage_iptables_chain(cmd)
            case IptablesPolicyCommand():
                self.set_iptables_policy(cmd)
            case IptablesRuleCommand():
                self.apply_iptables_rule(cmd)
            case IptablesFlushCommand():
                self.flush_iptables_rules(cmd)
            case IptablesZeroCommand():
                self.zero_iptables_counters(cmd)
            case IptablesSaveCommand():
                self.save_iptables_rules(cmd)
            case IptablesRestoreCommand():
                self.restore_iptables_rules(cmd)
            case IpsetCreateCommand(set_name=set_name, set_type=set_type):
                self.create_ipset(set_name, set_type)
            case IpsetDestroyCommand(set_name=set_name):
                self.destroy_ipset(set_name)
            case IpsetEntryCommand(set_name=set_name, entry=entry, present=present):
                self.manage_ipset_entry(set_name, entry, present)
            case FirewalldZoneCommand():
                self.manage_firewalld_zone(cmd)
            case FirewalldZoneTargetCommand():
                self.set_firewalld_zone_target(cmd)
            case FirewalldZoneTextCommand():
                self.set_firewalld_zone_text(cmd)
            case FirewalldZoneEntryCommand():
                self.manage_firewalld_zone_entry(cmd)
            case FirewalldMasqueradeCommand():
                self.set_firewalld_masquerade(cmd)
            case FirewalldServiceCommand():
                self.manage_firewalld_service(cmd)
            case FirewalldServiceEntryCommand():
                self.manage_firewalld_service_entry(cmd)
            case FirewalldServiceTextCommand():
                self.set_firewalld_service_text(cmd)
            case FirewalldIpsetCommand():
                self.manage_firewalld_ipset(cmd)
            case FirewalldIpsetEntryCommand():
                self.manage_firewalld_ipset_entry(cmd)
            case FirewalldDefaultZoneCommand(zone=zone):
                self.firewalld_default_zone = zone
            case FirewalldLockdownCommand(enabled=enabled):
                self.firewalld_lockdown = enabled
            case FirewalldPanicCommand(enabled=enabled):
                self.firewalld_panic_mode = enabled
            case FirewalldReloadCommand():
                self.reload_firewalld(cmd)
            case UfwStateCommand(enabled=enabled):
                self.set_ufw_state(enabled)
            case UfwDefaultPolicyCommand(policy=policy, direction=direction):
                self.set_ufw_default_policy(policy, direction)
            case UfwLoggingCommand(level=level):
                self.set_ufw_logging(level)
            case UfwResetCommand():
                self.reset_ufw()
            case UfwRuleCommand():
                self.manage_ufw_rule(cmd)
            case LxdLaunchCommand():
                self.launch_lxd_instance(cmd)
            case LxdStateCommand():
                self.set_lxd_instance_state(cmd)
            case LxdDeleteCommand():
                self.delete_lxd_instance(cmd)
            case LxdImageCommand():
                self.manage_lxd_image(cmd)
            case LxdImageAliasCommand():
                self.manage_lxd_image_alias(cmd)
            case LxdSetCommand():
                self.set_lxd_config(cmd)
            case LxdDeviceCommand():
                self.manage_lxd_device(cmd)
            case LxdProfileAssignCommand():
                self.assign_lxd_profiles(cmd)
            case LxdCopyCommand():
                self.copy_lxd_instance(cmd)
            case LxdSnapshotCommand():
                self.create_lxd_snapshot(cmd)
            case LxdRestoreCommand():
                self.restore_lxd_snapshot(cmd)
            case LxdNetworkCommand():
                self.manage_lxd_network(cmd)
            case LxdOperationCancelCommand():
                self.cancel_lxd_operation(cmd)
            case LxdOperationWaitCommand():
                self.finish_lxd_operation(cmd)
            case LxdProfileCommand():
                self.manage_lxd_profile(cmd)
            case LxdProjectCommand():
                self.manage_lxd_project(cmd)
            case LxdStoragePoolCommand():
                self.manage_lxd_storage_pool(cmd)
            case LxdStorageVolumeCommand():
                self.manage_lxd_storage_volume(cmd)
            case FlatpakInstallCommand(packages=packages):
                self.install_flatpak_packages(packages)
            case FlatpakRemoveCommand(packages=packages):
                self.remove_flatpak_packages(packages)
            case SnapInstallCommand(packages=packages, channel=channel):
                self.install_snap_packages(packages, channel)
            case SnapRefreshCommand(packages=packages, channel=channel):
                self.refresh_snap_packages(packages, channel)
            case SnapRemoveCommand(packages=packages):
                self.remove_snap_packages(packages)
            case WaitForFactCommand():
                self.check_wait(cmd)
            case _:
                raise TypeError(f"unknown command descriptor: {cmd!r}")

    # State mutations, invoked only via execute_command.

    def install_apk_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apk add``: mark the names installed.

        ``apk.packages``' install mutation. A version pin (``pkg=version``)
        records exactly that version in ``apk_packages`` (the name -> sorted
        version list ``ApkInstalledPackages`` reads), so a repeat pinned install is a
        no-op; an unpinned name carries no repository metadata to resolve a
        version from, so it gets the ``[""]`` placeholder -- and an
        already-installed name keeps its versions. Kept separate from
        ``packages``/``apt_packages`` (the dnf-/apt-side state) so apk, apt
        and dnf never mix.
        """
        for package in packages:
            name, sep, version = package.rpartition("=")
            if sep:
                self.apk_packages[name] = [version]
            else:
                self.apk_packages.setdefault(package, [""])

    def remove_apk_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apk del``: drop the versions.

        The ``present=False`` direction of ``apk.packages``. A bare name pops
        the whole entry from ``apk_packages`` so ``ApkInstalledPackages`` no longer
        reports it; a version pin (``pkg=version``, which ``apk.packages``
        passes through verbatim) drops only that version, popping the name
        once no versions remain. An absent name/version is tolerated (the
        real command would exit non-zero, but the operation's fact check
        never yields the removal in that case).
        """
        for package in packages:
            name, sep, version = package.rpartition("=")
            if not sep:
                self.apk_packages.pop(package, None)
                continue
            versions = self.apk_packages.get(name)
            if versions is None:
                continue
            if version in versions:
                versions.remove(version)
            if not versions:
                self.apk_packages.pop(name, None)

    def add_apk_virtual(self, tag: str, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apk add -t <tag> <packages>``.

        ``apk.virtual``'s create mutation. apk records the virtual group's
        ``tag`` in ``/etc/apk/world`` and installs the member packages as its
        dependencies, so this both inserts ``tag`` into ``apk_explicit_packages``
        (the sorted, de-duplicated ``ApkPackageVersion`` entries
        ``ApkExplicitPackages`` reads) -- as an unconstrained entry
        (``version`` ``""``), kept
        sorted and de-duplicated as the parsed world entries
        read -- and delegates the members to ``install_apk_packages`` so
        ``ApkInstalledPackages`` stays consistent for any later ``apk.packages`` read.
        """
        if not any(entry["name"] == tag for entry in self.apk_explicit_packages):
            self.apk_explicit_packages.append({"name": tag, "version": ""})
            self.apk_explicit_packages.sort(
                key=lambda entry: (entry["name"], entry["version"])
            )
        self.install_apk_packages(packages)

    def remove_apk_virtual(self, tag: str, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apk del <tag>``.

        ``apk.virtual``'s remove mutation: drop the virtual group's ``tag`` from
        ``apk_explicit_packages`` (an absent tag is tolerated -- the real
        command would exit non-zero, but the operation's world-name check never
        yields the removal in that case) and remove whichever member packages
        the request named via ``remove_apk_packages``. A tag-only removal
        (``packages`` empty, the common ``present=False`` form) leaves the
        members installed: apk's own ``apk del <tag>`` would drop the ones
        nothing else needs, but the simulation models no dependency graph, so
        member cleanup on removal stays out of scope.
        """
        if tag in [entry["name"] for entry in self.apk_explicit_packages]:
            self.apk_explicit_packages = [
                entry
                for entry in self.apk_explicit_packages
                if entry["name"] != tag
            ]
        self.remove_apk_packages(packages)

    def hold_apk_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apk add <pkg><constraint>``: pin the names.

        ``apk.hold``'s ``present=True`` mutation. Each entry carries its
        version constraint (``curl=8.14.0-r0``), so ``_split_constraint``
        separates the bare name from the bound and the pair lands in
        ``apk_held_packages`` (the list of ``ApkPackageVersion`` entries
        ``ApkHeldPackages`` reads) as the entry's ``version``, exactly as
        the fact parses the world
        line the real command writes -- the ``@tag`` selector dropped, since
        a hold command the simulation models carries none. A repeat
        hold of the same pin is a no-op once the fresh per-step fact cache
        sees it. Holding does not touch ``apk_packages``
        -- the pin keeps a package out of upgrades, it never installs or
        removes anything.
        """
        for package in packages:
            name, constraint = _split_constraint(package)
            entry = {"name": name, "version": constraint}
            held = [e for e in self.apk_held_packages if e["name"] != name]
            held.append(entry)
            self.apk_held_packages = held

    def unhold_apk_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apk add <pkg>``: drop the pins.

        ``apk.hold``'s ``present=False`` mutation. Each entry is a bare name
        (the real ``apk add <pkg>`` rewrites the world line without a bound),
        so the name's entry is removed from ``apk_held_packages`` and
        ``ApkHeldPackages`` no longer reports it. An absent name is tolerated
        -- the operation's fact check only yields names currently held. The
        package stays installed: ``apk_packages`` is untouched.
        """
        self.apk_held_packages = [
            entry
            for entry in self.apk_held_packages
            if entry["name"] not in packages
        ]

    def autoremove_apk_packages(self) -> None:
        """Record a simulated ``apk add --purge``: drop the orphan names.

        ``apk.autoremove``'s mutation. Pops each ``apk_autoremovable`` name
        from ``apk_packages`` so ``ApkInstalledPackages`` no longer reports it,
        clears ``apk_autoremovable`` (nothing left to purge) so a repeat
        ``apk.autoremove`` is a no-op once the fresh per-step fact cache
        sees the empty list. ``apk_explicit_packages`` is deliberately
        untouched: an orphan is by definition NOT a world entry, so the
        purge never rewrites the world. ``apk_held_packages`` is likewise
        untouched -- a held package carries a version constraint in the
        world, so it is reachable and never an orphan. An absent name is
        tolerated (the operation's fact check only yields names the
        simulation reported).
        """
        for entry in self.apk_autoremovable:
            self.apk_packages.pop(entry["name"], None)
        self.apk_autoremovable.clear()

    def install_apk_repo_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``apk.repository`` install of the spooled text.

        ``apk.repository`` first uploads the transformed repositories text to a
        ``/tmp`` spool path (the paired ``ApkRepoUploadCommand`` runs through
        ``put_file``, storing the bytes in ``remote_files``), then installs it as
        the ``dest`` file and removes the spool. This mirrors the
        install-and-remove: the spool bytes are popped out of ``remote_files``
        and stored at ``dest``, and the path is added to ``existing_files`` plus
        a minimal ``file_stats`` row (root-owned, mode 644 -- the real command's
        ``chmod 0644``), so the content-read (``ApkRepositoriesFile``), existence
        and metadata facts all see the installed file consistently. The
        ``apk_repositories`` seam is refreshed too: entries the *previous*
        content at ``dest`` parsed into are dropped and the new content's lines
        appended (both through ``ApkRepositories.parse``), so an add or a removal
        replaces rather than accumulates and a repeat ``apk.repository`` is a
        no-op once the fresh per-step fact cache sees them -- the refresh is
        content-based, never append-only, exactly like the real ``cp`` overlay.
        An absent spool path installs nothing (the paired ``apt.key`` and
        ``apt.sources_file`` mutators tolerate it the same way).
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        previous = self.remote_files.get(dest)
        if previous is not None:
            stale = ApkRepositories.parse(previous.decode("utf-8").splitlines())
            self.apk_repositories[:] = [
                entry for entry in self.apk_repositories if entry not in stale
            ]
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }
        for entry in ApkRepositories.parse(data.decode("utf-8").splitlines()):
            if entry not in self.apk_repositories:
                self.apk_repositories.append(entry)

    def set_openrc_service_state(self, service: str, action: str) -> None:
        """Record a simulated ``rc-service <service> <action>``.

        ``openrc.service``'s runtime mutation. ``start``/``restart`` record
        ``started``, ``stop`` records ``stopped``, ``reload`` leaves the
        state word alone (a reload re-reads configuration without a state
        change). The word is updated in every runlevel listing the service
        appears in -- runtime state is global, while the seam is keyed by
        runlevel because ``rc-status <runlevel>`` lists per-runlevel
        assignments -- and a service no runlevel lists yet is inserted
        into ``default``, so starting an unassigned service stays
        observable through the default-runlevel facts (a documented
        simulation simplification).
        """
        if action == "reload":
            return
        state = "stopped" if action == "stop" else "started"
        found = False
        for services in self.openrc_service_states.values():
            if service in services:
                services[service] = state
                found = True
        if not found:
            self.openrc_service_states.setdefault("default", {})[service] = state

    def enable_openrc_service(self, service: str, runlevel: str) -> None:
        """Record a simulated ``rc-update add <service> <runlevel>``.

        Appends the service to the runlevel's assignment list
        (``openrc_runlevels``, which ``OpenrcRunlevels``/``OpenrcEnabled``
        read) and seeds a ``stopped`` state word in the runlevel's
        ``openrc_service_states`` listing -- enabling at boot never starts
        a service, and ``rc-status <runlevel>`` lists it once assigned. An
        already-assigned service is left alone (the operation's fact check
        only yields the add when absent).
        """
        names = self.openrc_runlevels.setdefault(runlevel, [])
        if service not in names:
            names.append(service)
        self.openrc_service_states.setdefault(runlevel, {}).setdefault(
            service, "stopped"
        )

    def disable_openrc_service(self, service: str, runlevel: str) -> None:
        """Record a simulated ``rc-update del <service> <runlevel>``.

        Drops the service from the runlevel's assignment list and from its
        ``openrc_service_states`` listing -- an unassigned service no
        longer appears in ``rc-status <runlevel>`` rows. The state words
        in *other* runlevels' listings are untouched (the real command
        only removes the one boot assignment). Absent entries are
        tolerated.
        """
        names = self.openrc_runlevels.get(runlevel)
        if names is not None and service in names:
            names.remove(service)
        services = self.openrc_service_states.get(runlevel)
        if services is not None:
            services.pop(service, None)

    def stack_openrc_runlevel(self, runlevel: str, target: str) -> None:
        """Record a simulated ``rc-update -s add <runlevel> <target>``.

        A stacked runlevel is a symlink inside the target's directory, so
        the runlevel name is appended to the target's assignment list
        (``OpenrcRunlevels`` reports it as an entry there). A repeat stack
        is a no-op.
        """
        names = self.openrc_runlevels.setdefault(target, [])
        if runlevel not in names:
            names.append(runlevel)

    def unstack_openrc_runlevel(self, runlevel: str, target: str) -> None:
        """Record a simulated ``rc-update -s del <runlevel> <target>``.

        Removes the runlevel's stack symlink from the target's assignment
        list; an absent entry is tolerated.
        """
        names = self.openrc_runlevels.get(target)
        if names is not None and runlevel in names:
            names.remove(runlevel)

    def create_openrc_runlevel(self, runlevel: str) -> None:
        """Record a simulated ``mkdir -p /etc/runlevels/<runlevel>``.

        Seeds the runlevel as an empty assignment list; ``mkdir -p`` on an
        existing directory is a no-op, and so is this.
        """
        self.openrc_runlevels.setdefault(runlevel, [])

    def remove_openrc_runlevel(self, runlevel: str) -> None:
        """Record a simulated ``rm -rf /etc/runlevels/<runlevel>``.

        Pops the runlevel's assignment list and state listing, and drops
        its name from every other runlevel's list -- the operation
        unstacks before removing, but the mutator stays consistent even
        when a test drives the descriptor directly (no dangling stack
        entry survives).
        """
        self.openrc_runlevels.pop(runlevel, None)
        self.openrc_service_states.pop(runlevel, None)
        for names in self.openrc_runlevels.values():
            if runlevel in names:
                names.remove(runlevel)

    def install_openrc_config_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``openrc.config`` install of the spooled text.

        ``openrc.config`` first uploads the transformed rc.conf / conf.d text
        to a ``/tmp`` spool path (the paired ``OpenrcConfigUploadCommand`` runs
        through ``put_file``, storing the bytes in ``remote_files``), then
        installs it as the ``dest`` file and removes the spool. This mirrors
        the install-and-remove: the spool bytes are popped out of
        ``remote_files`` and stored at ``dest``, and the path is added to
        ``existing_files`` plus a minimal ``file_stats`` row (root-owned,
        mode 644 -- the real command's ``chmod 0644``), so the content-read
        (``OpenrcConfigFile``), existence and metadata facts all see the
        installed file consistently. There is no parsed store to refresh:
        ``OpenrcConfigFile`` reads ``remote_files`` directly, so the next
        step's fresh fact cache sees the new text and a repeat
        ``openrc.config`` is a no-op once the transform is a fixpoint. An
        absent spool path installs nothing (the ``install_apk_repo_file``
        tolerance).
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }

    def install_packages(self, packages: tuple[str, ...]) -> None:
        for package in packages:
            # A version pin (``pkg=version``) records exactly that version in
            # the name -> versions store, overwriting whatever was there
            # before (the ``install_apt_packages`` precedent), so a pinned
            # install converges even when the name was already present at a
            # different version; an unpinned name carries no repository
            # metadata to resolve a version from, so it gets the [""]
            # placeholder and never overwrites an existing entry.
            name, sep, version = package.rpartition("=")
            if sep:
                self.packages[name] = [version]
            else:
                name = package
                self.packages.setdefault(name, [""])
            for src, target in self.installs_links.get(name, {}).items():
                self.links[src] = target
                # The Link-fact seam too: files.link stats the path instead
                # of only probing readlink, so a package-dropped link must
                # answer with a row (root-owned, mode 777, the symlink
                # defaults) carrying its target.
                self.link_stats.setdefault(
                    src,
                    {
                        "mode": 777,
                        "user": "root",
                        "group": "root",
                        "link_target": target,
                    },
                )
            for path in self.installs_files.get(name, set()):
                self.existing_files.add(path)
                # The File-fact seam too: files.file(present=False) now
                # stats the path instead of only probing existence, so a
                # package-dropped file must answer with a metadata row
                # (root-owned mode 644, the rpm default).
                self.file_stats.setdefault(
                    path, {"mode": 644, "user": "root", "group": "root"}
                )

    def downgrade_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``dnf downgrade``: pin the name to the requested
        version.

        ``dnf.packages(allow_downgrade=True)``'s mutation for a pinned
        ``<pkg>=<version>`` whose name is already installed at a different
        version. Always overwrites the name's versions with exactly the
        requested one and re-applies the same package side effects (dropped
        links/files) as ``install_packages``, keyed on the bare name.
        """
        for package in packages:
            name, sep, version = package.rpartition("=")
            if not sep:
                name, version = package, ""
            self.packages[name] = [version]
            for src, target in self.installs_links.get(name, {}).items():
                self.links[src] = target
                self.link_stats.setdefault(
                    src,
                    {
                        "mode": 777,
                        "user": "root",
                        "group": "root",
                        "link_target": target,
                    },
                )
            for path in self.installs_files.get(name, set()):
                self.existing_files.add(path)
                self.file_stats.setdefault(
                    path, {"mode": 644, "user": "root", "group": "root"}
                )

    def remove_packages(self, packages: tuple[str, ...]) -> None:
        for package in packages:
            name = package.rpartition("=")[0] or package
            self.packages.pop(name, None)
            # A package installed from a ``.rpm`` (``dnf.rpm`` ``present=False``
            # reuses DnfRemoveCommand) must also leave ``rpm_packages``, or
            # ``RpmPackage`` would still report it installed.
            self.rpm_packages.pop(name, None)

    def install_rpm(self, src: str) -> None:
        """Record a simulated ``rpm -i``: mark the file's package installed.

        Reads the ``.rpm``'s name/version from ``rpm_files`` (the seam
        ``RpmPackage`` reads for a file path) and records them in
        ``rpm_packages`` plus ``packages``, so a repeat ``dnf.rpm`` is a no-op
        and ``DnfPackages`` also sees the package at that version. An unknown
        ``.rpm`` path has no metadata to install, so nothing changes.
        """
        info = self.rpm_files.get(src)
        if info is None:
            return
        name = info["name"]
        self.rpm_packages[name] = info["version"]
        self.packages[name] = [info["version"]]

    def install_deb(self, src: str) -> None:
        """Record a simulated ``dpkg -i``: mark the ``.deb``'s package installed.

        Reads the ``.deb``'s name/version from ``deb_files`` (the seam
        ``DebPackage`` reads for a file path) and records the version in
        ``apt_packages`` (the name -> sorted version list ``AptPackages``
        reads), so a repeat ``apt.deb`` is a no-op. Both install passes -- the
        tolerant first and the configuring final -- have the same simulated
        effect, and the middle ``apt-get install -f`` models no dependency
        resolution. An unknown ``.deb`` path has no metadata to install, so
        nothing changes. Kept separate from ``packages``/``rpm_packages`` (the
        dnf-side state) so apt and dnf never mix.
        """
        info = self.deb_files.get(src)
        if info is None:
            return
        self.apt_packages[info["name"]] = [info["version"]]

    def install_apt_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apt-get install``: mark the names installed.

        ``apt.packages``' install (and ``latest`` upgrade) mutation. A
        version pin (``pkg=version``) records exactly that version in
        ``apt_packages`` (the name -> sorted version list ``AptPackages``
        reads), so a repeat pinned install is a no-op; an unpinned name
        carries no repository metadata to resolve a version from, so it
        gets the ``[""]`` placeholder the baseline's own operation fixtures use
        for an unknown version -- and an already-installed name keeps its
        versions (the ``latest`` path is non-idempotent by design, so no
        candidate convergence is modeled). Kept separate from
        ``packages``/``rpm_packages`` (the dnf-side state) so apt and dnf
        never mix.
        """
        for package in packages:
            name, sep, version = package.rpartition("=")
            if sep:
                self.apt_packages[name] = [version]
            else:
                self.apt_packages.setdefault(package, [""])

    def remove_apt_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apt-get remove``/``purge``: drop the versions.

        The ``present=False`` direction of ``apt.deb`` and ``apt.packages``.
        A bare name pops the whole entry from ``apt_packages`` so
        ``AptPackages`` (and ``DebPackage``'s installed-name query) no
        longer report it; a version pin (``pkg=version``, which
        ``apt.packages`` passes through verbatim) drops only that version,
        popping the name once no versions remain. An absent name/version is
        tolerated (the real command would exit non-zero, but the
        operation's fact check never yields the removal in that case).
        """
        for package in packages:
            name, sep, version = package.rpartition("=")
            if not sep:
                self.apt_packages.pop(package, None)
                continue
            versions = self.apt_packages.get(name)
            if versions is None:
                continue
            if version in versions:
                versions.remove(version)
            if not versions:
                self.apt_packages.pop(name, None)

    def upgrade_apt_packages(self) -> None:
        """Record a simulated ``apt-get upgrade``: apply pending upgrades.

        ``apt.upgrade``'s and ``apt.packages(upgrade=True)``'s mutation. Like
        ``dist_upgrade_apt_packages`` it moves every ``apt_upgradable``
        candidate version into ``apt_packages`` and clears
        ``apt_upgradable`` (a plain upgrade never installs new dependencies
        or removes conflicting packages, which the simulation does not
        model anyway), but zeroes the ``upgrade`` entry in
        ``apt_simulations`` -- the phrase both operations simulate -- so
        a repeat upgrade is a no-op.
        """
        for upgrade in self.apt_upgradable:
            self.apt_packages[upgrade["name"]] = [upgrade["candidate"]]
        self.apt_upgradable.clear()
        self.apt_simulations["upgrade"] = {
            "upgraded": 0,
            "newly_installed": 0,
            "removed": 0,
            "not_upgraded": 0,
        }

    def dist_upgrade_apt_packages(self) -> None:
        """Record a simulated ``apt-get dist-upgrade``: apply pending upgrades.

        ``apt.dist_upgrade``'s mutation. Moves every ``apt_upgradable``
        candidate version into ``apt_packages`` (the name -> sorted version
        list ``AptPackages`` reads), clears ``apt_upgradable`` (nothing left
        to upgrade) and zeroes the ``dist-upgrade`` entry in
        ``apt_simulations``, so a repeat ``apt.dist_upgrade`` is a no-op.
        Orphaned dependencies are left alone -- a plain dist-upgrade does
        not autoremove; that is ``auto_remove_apt_packages``' job.
        """
        for upgrade in self.apt_upgradable:
            self.apt_packages[upgrade["name"]] = [upgrade["candidate"]]
        self.apt_upgradable.clear()
        self.apt_simulations["dist-upgrade"] = {
            "upgraded": 0,
            "newly_installed": 0,
            "removed": 0,
            "not_upgraded": 0,
        }

    def auto_remove_apt_packages(self) -> None:
        """Record a simulated ``apt-get autoremove``: drop the removable names.

        ``apt.auto_remove``'s mutation. Pops each ``apt_autoremovable``
        entry's name from ``apt_packages`` so ``AptPackages`` no longer
        reports it,
        clears ``apt_autoremovable`` (nothing left to remove) and zeroes the
        ``autoremove`` entry in ``apt_simulations``, so a repeat
        ``apt.auto_remove`` is a no-op. An absent name is tolerated (the
        operation's fact check only yields names the simulation reported).
        """
        for entry in self.apt_autoremovable:
            self.apt_packages.pop(entry["name"], None)
        self.apt_autoremovable.clear()
        self.apt_simulations["autoremove"] = {
            "upgraded": 0,
            "newly_installed": 0,
            "removed": 0,
            "not_upgraded": 0,
        }

    def hold_apt_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apt-mark hold``: mark the names held.

        ``apt.mark``'s ``held=True`` mutation. Adds the names as entries to
        ``apt_held_packages`` (the list ``AptHeldPackages`` reads), keeping
        it sorted and de-duplicated as the fact's ``showhold`` parsing
        would, so a repeat hold is a no-op once the fresh per-step fact
        cache sees the change. Holding does not touch ``apt_packages`` --
        the mark keeps a package out of upgrades, it never installs or
        removes anything.
        """
        held = {entry["name"] for entry in self.apt_held_packages}
        self.apt_held_packages[:] = [
            {"name": name, "versions": []} for name in sorted(held | set(packages))
        ]

    def unhold_apt_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``apt-mark unhold``: drop the held marks.

        ``apt.mark``'s ``held=False`` mutation. Removes the names' entries
        from
        ``apt_held_packages`` so ``AptHeldPackages`` no longer reports them
        (the list stays sorted). An absent name is tolerated -- the real
        ``apt-mark unhold`` reports "was already not on hold" and exits 0,
        and the operation's fact check never yields such a name anyway.
        """
        dropped = set(packages)
        self.apt_held_packages[:] = [
            entry for entry in self.apt_held_packages if entry["name"] not in dropped
        ]

    @staticmethod
    def _ppa_url(src: str) -> str:
        """The Launchpad URL a ``ppa:user/repo`` name expands to."""
        return f"http://ppa.launchpad.net/{src.removeprefix('ppa:')}/ubuntu"

    def add_ppa(self, src: str) -> None:
        """Record a simulated ``apt-add-repository``: add the PPA's source.

        ``apt.ppa``'s ``present=True`` mutation. A ``ppa:user/repo`` name
        expands to the Launchpad URL ``http://ppa.launchpad.net/user/repo/ubuntu``
        and is appended to ``apt_sources`` (the list ``AptSources`` reads) as a
        ``deb`` source with the ``main`` component and the host's codename (the
        ``lsb_release`` seam's ``Codename``, ``""`` when unset -- the simulation
        models no release detection). A repeat add of the same URL is a no-op.
        The signing key ``apt-add-repository`` also imports is not modeled,
        like ``install_apt_key`` leaves ``apt_keys`` to the test.
        """
        url = self._ppa_url(src)
        if any(source["url"] == url for source in self.apt_sources):
            return
        self.apt_sources.append(
            {
                "type": "deb",
                "url": url,
                "distribution": self.lsb_release.get("Codename", ""),
                "components": ["main"],
                "options": {},
            }
        )

    def remove_ppa(self, src: str) -> None:
        """Record a simulated ``apt-add-repository --remove``: drop the PPA.

        ``apt.ppa``'s ``present=False`` mutation. Removes every ``apt_sources``
        entry whose URL matches the PPA's Launchpad URL, so ``AptSources`` no
        longer reports it. An absent PPA is tolerated (the real command would
        exit non-zero, but the operation only yields the removal once it has
        read the source as present, so this seam rarely sees an absent one).
        """
        url = self._ppa_url(src)
        self.apt_sources[:] = [s for s in self.apt_sources if s["url"] != url]

    @staticmethod
    def _legacy_sources_entries(data: bytes) -> list[AptSource]:
        """Parse the ``AptSource`` entries one legacy sources file lists.

        Blank and ``#`` comment lines are skipped and lines the fact's regex
        cannot parse contribute nothing -- exactly how ``AptSources`` reads a
        ``.list`` group -- so the ``apt_sources`` seam stays consistent with
        what the fact would report for the same bytes.
        """
        entries: list[AptSource] = []
        for raw in data.decode("utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parsed = AptSources._parse_list_line(line)
            if parsed is not None:
                entries.append(parsed)
        return entries

    def install_apt_repo_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``apt.repo`` install of the spooled sources text.

        ``apt.repo`` first uploads the transformed file text to a ``/tmp``
        spool path (the paired ``AptRepoUploadCommand`` runs through
        ``put_file``, storing the bytes in ``remote_files``), then installs
        it as the ``dest`` sources file and removes the spool. This mirrors
        the install-and-remove: the spool bytes are popped out of
        ``remote_files`` and stored at ``dest``, and the path is added to
        ``existing_files`` plus a minimal ``file_stats`` row (root-owned,
        mode 644 -- the real command's ``chmod 0644``), so the content-read
        (``AptSourcesFile``), existence and metadata facts all see the
        installed file consistently. The ``apt_sources`` seam is refreshed
        too: entries the *previous* content at ``dest`` parsed into are
        dropped and the new content's legacy lines appended, so an add or a
        removal replaces rather than accumulates and a repeat ``apt.repo``
        is a no-op once the fresh per-step fact cache sees them. Like the
        deb822 mutator below, the simulation tracks no per-entry
        provenance, so an equal entry goes regardless of which file listed
        it. An absent spool path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        previous = self.remote_files.get(dest)
        if previous is not None:
            stale = self._legacy_sources_entries(previous)
            self.apt_sources[:] = [s for s in self.apt_sources if s not in stale]
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }
        for entry in self._legacy_sources_entries(data):
            if entry not in self.apt_sources:
                self.apt_sources.append(entry)

    def install_apt_sources_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``apt.sources_file`` install of the spooled deb822.

        ``apt.sources_file`` first uploads the generated content to a
        ``/tmp`` spool path (the paired ``AptSourcesFileUploadCommand`` runs
        through ``put_file``, storing the bytes in ``remote_files``), then
        installs it as the ``dest`` sources file and removes the spool. This
        mirrors the install-and-remove: the spool bytes are popped out of
        ``remote_files`` and stored at ``dest``, and the path is added to
        ``existing_files`` plus a minimal ``file_stats`` row (root-owned,
        mode 644 -- the real command's ``chmod 0644``), so the file facts
        see the installed file consistently. The ``apt_sources`` seam is
        refreshed too: entries the *previous* content at ``dest`` parsed
        into are dropped and the new content's entries (``AptSources``'
        deb822 parser) appended, so a rewrite replaces rather than
        accumulates and a repeat write is a no-op once the fresh per-step
        fact cache sees them. The simulation does not track which file an
        entry came from, so an equal entry goes regardless of its file
        (mirrored by ``install_apt_repo_file``). An absent spool path
        installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        previous = self.remote_files.get(dest)
        if previous is not None:
            stale = AptSources._parse_deb822_file(previous.decode("utf-8").splitlines())
            self.apt_sources[:] = [s for s in self.apt_sources if s not in stale]
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }
        for entry in AptSources._parse_deb822_file(data.decode("utf-8").splitlines()):
            if entry not in self.apt_sources:
                self.apt_sources.append(entry)

    def remove_apt_sources_file(self, path: str) -> None:
        """Record a simulated ``rm -f`` of a deb822 sources file.

        ``apt.sources_file``'s ``present=False`` mutation. The content
        bytes (``remote_files``), the existence marker (``existing_files``)
        and any ``file_stats`` row all go, and the entries the file's
        content parsed into are dropped from ``apt_sources`` so
        ``AptSources`` no longer reports them (equal entries go regardless
        of their file -- the simulation tracks no per-entry provenance).
        An absent path is tolerated: ``rm -f`` exits 0 on a missing file
        and the operation always yields the removal.
        """
        data = self.remote_files.pop(path, None)
        if data is not None:
            stale = AptSources._parse_deb822_file(data.decode("utf-8").splitlines())
            self.apt_sources[:] = [s for s in self.apt_sources if s not in stale]
        self.existing_files.discard(path)
        self.file_stats.pop(path, None)

    def import_key(self, path: str) -> None:
        """Record a simulated ``rpm --import`` of the spooled key at ``path``.

        ``dnf.key`` first uploads the key stream to a ``/tmp`` spool path (the
        paired ``DnfKeyUploadCommand`` runs through ``put_file``, storing the
        bytes in ``remote_files``), then imports and removes it. This mirrors
        the import-and-remove: the spool bytes are popped out of
        ``remote_files`` and recorded in ``imported_keys``, so a test can assert
        both that the key was imported and that the spool file was cleaned up.
        A stub ``RpmGpgKey`` entry also lands in ``rpm_gpg_keys`` so
        ``RpmGpgKeys`` reports the import and a ``present=False`` removal can
        find it. The simulation models no OpenPGP parsing (the
        ``import_gpg_key`` precedent): the stub's keyid and release are
        derived from the content hash (stable across phases), not from the
        real key material, and its summary is a placeholder -- a test wanting
        a realistic keyid seeds ``rpm_gpg_keys`` directly. An already-present
        keyid/release pair is left alone (rpm's re-import is a no-op); an
        absent spool path imports nothing.
        """
        data = self.remote_files.pop(path, None)
        if data is not None:
            self.imported_keys.add(data)
            digest = hashlib.sha256(data).hexdigest()
            keyid, release = digest[-8:], digest[:8]
            if not any(
                entry["keyid"] == keyid and entry["release"] == release
                for entry in self.rpm_gpg_keys
            ):
                self.rpm_gpg_keys.append(
                    {
                        "keyid": keyid,
                        "release": release,
                        "summary": "gpg(reemote simulated key)",
                    }
                )

    def remove_rpm_gpg_key(self, keyid: str, release: str) -> None:
        """Record a simulated ``rpm -e`` of one ``gpg-pubkey`` pseudo-package.

        ``dnf.key``'s ``present=False`` mutation: the entry naming the keyid
        and release leaves ``rpm_gpg_keys``, so ``RpmGpgKeys`` no longer
        reports it and a repeat removal plans a no-op. The raw bytes stay in
        ``imported_keys`` -- that store observes the import event, not the
        rpmdb state. An absent entry is tolerated (the operation only yields
        the erase for a key the fact reported present).
        """
        self.rpm_gpg_keys[:] = [
            entry
            for entry in self.rpm_gpg_keys
            if not (entry["keyid"] == keyid and entry["release"] == release)
        ]

    def install_apt_key(self, spool: str, dest: str) -> None:
        """Record a simulated ``apt.key`` install of the spooled key at ``spool``.

        ``apt.key`` first uploads the key stream to a ``/tmp`` spool path (the
        paired ``AptKeyUploadCommand`` runs through ``put_file``, storing the
        bytes in ``remote_files``), then installs it as the ``dest`` keyring
        file and removes the spool. This mirrors the install-and-remove: the
        spool bytes are popped out of ``remote_files`` and stored at ``dest``,
        and the keyring path is added to ``existing_files`` plus a minimal
        ``file_stats`` row (root-owned, mode 644 -- the real command's
        ``chmod 0644``), so ``FileExists``, ``File`` and the content-read
        facts all see the installed file consistently. The simulated install
        models no ``gpg --dearmor``: armored content is stored verbatim (the
        real command would write its binary conversion), and the ``apt_keys``
        seam is not derived from it -- that seam holds the already-parsed
        ``AptKeys`` answer a test seeds directly. An absent spool path
        installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }

    def _store_gpg_keyring_file(self, dest: str, data: bytes) -> None:
        """Record a keyring file write consistently across every consulted seam.

        The shared core of ``install_gpg_key`` and ``dearmor_gpg_key``: the
        bytes land in ``remote_files``, the path joins ``existing_files``
        (the ``_KeyringFileExists``/``FileExists`` probe) and a minimal
        ``file_stats`` row (root-owned, mode 644 -- the real commands'
        ``chmod 0644``), and the ``gpg_keyrings`` entry for ``dest`` is
        upserted so ``GpgKeyrings`` sees the file too. The upserted entry
        carries empty keys: the simulation models no key material, so a
        test seeds the keys a keyring holds directly (an existing entry's
        seeded keys are kept -- a re-install over a seeded keyring does not
        erase the test's key fixture).
        """
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }
        for keyring in self.gpg_keyrings:
            if keyring["path"] == dest:
                break
        else:
            self.gpg_keyrings.append(
                {"path": dest, "format": dest.split(".")[-1].lower(), "keys": []}
            )

    def install_gpg_key(self, spool: str, dest: str) -> None:
        """Record a simulated ``gpg.key`` install of the spooled key at ``spool``.

        ``gpg.key`` first uploads the key stream to a ``/tmp`` spool path (the
        paired ``GpgKeyUploadCommand`` runs through ``put_file``, storing the
        bytes in ``remote_files``), then installs it as the ``dest`` keyring
        file and removes the spool. This mirrors the install-and-remove: the
        spool bytes are popped out of ``remote_files`` and stored at ``dest``
        via ``_store_gpg_keyring_file``, so ``FileExists``, ``File``,
        ``GpgKeyrings`` and the content-read facts all see the installed file
        consistently. The simulated install models no ``gpg --dearmor``:
        armored content is stored verbatim (the real command would write its
        binary conversion). An absent spool path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self._store_gpg_keyring_file(dest, data)

    def remove_gpg_keyring(self, path: str) -> None:
        """Record a simulated ``rm -f`` of a whole keyring file.

        The ``present=False`` direction of ``gpg.key`` without ``keyid``
        (and the all-keys-match removal of an armored keyring): the file
        leaves ``remote_files``, ``existing_files`` and ``file_stats``, and
        its ``gpg_keyrings`` entry is dropped, so every fact that consulted
        the file sees it gone. An absent path is left alone (``rm -f`` never
        fails on one).
        """
        self.remote_files.pop(path, None)
        self.existing_files.discard(path)
        self.file_stats.pop(path, None)
        self.gpg_keyrings[:] = [kr for kr in self.gpg_keyrings if kr["path"] != path]

    def delete_gpg_keys(self, keyring: str, fingerprints: tuple[str, ...]) -> None:
        """Record a simulated selective ``gpg --delete-keys`` on one keyring.

        Drops the key entries whose fingerprint (or id, when no fingerprint
        was attached -- the operation's own fallback) is named, keeping the
        rest and the file itself: the emptied-file pruning is the separate
        ``cleanup_empty_gpg_keyring`` step that follows. An unseeded keyring
        path deletes nothing (the simulation cannot list unmodeled keys).
        """
        for kr in self.gpg_keyrings:
            if kr["path"] == keyring:
                kr["keys"] = [
                    key
                    for key in kr["keys"]
                    if key.get("fingerprint", key["id"]) not in fingerprints
                ]
                break

    def cleanup_empty_gpg_keyring(self, keyring: str) -> None:
        """Record the simulated empty-keyring pruning after a selective delete.

        Mirrors the real command's ``--list-keys | grep -q '^pub' || rm -f``:
        when the seeded keyring entry holds no keys left, the whole file is
        removed (``remove_gpg_keyring``); a keyring with surviving keys -- or
        an unseeded path, whose keys the simulation cannot list -- is left
        alone.
        """
        for kr in self.gpg_keyrings:
            if kr["path"] == keyring:
                if not kr["keys"]:
                    self.remove_gpg_keyring(keyring)
                break

    def dearmor_gpg_key(self, src: str, dest: str) -> None:
        """Record a simulated ``gpg.dearmor`` conversion between remote paths.

        Reads the armored bytes from ``remote_files`` at ``src`` (which stays
        -- dearmor does not consume its input) and stores them at ``dest``
        via ``_store_gpg_keyring_file``, so the output file is visible to
        every consulted seam. Like the install mutator it models no actual
        dearmoring: the content is stored verbatim (the real command would
        write its binary conversion). An absent ``src`` writes nothing (the
        real ``gpg`` would fail; the seam stays safe regardless).
        """
        data = self.remote_files.get(src)
        if data is None:
            return
        self._store_gpg_keyring_file(dest, data)

    def import_gpg_key(self, spool: str) -> None:
        """Record a simulated ``gpg.import_key`` of the spooled key stream.

        Pops the spool bytes out of ``remote_files`` (the paired
        ``GpgImportUploadCommand`` stored them via ``put_file``) and appends
        a stub ``GpgKeyInfo`` entry to ``gpg_keys`` so a repeat import with
        ``keyid`` sees the key. The simulation models no OpenPGP parsing:
        the stub's fingerprint is derived from the content hash (stable
        across phases), it carries no uid, and secret material is not
        modeled into ``gpg_secret_keys`` (a test seeds that directly). An
        already-present fingerprint is left alone (gpg's merge is a
        no-op); an absent spool path imports nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        fingerprint = hashlib.sha256(data).hexdigest().upper()[:40]
        if any(key.get("fingerprint") == fingerprint for key in self.gpg_keys):
            return
        self.gpg_keys.append(
            {
                "id": fingerprint[-16:],
                "validity": "u",
                "length": 4096,
                "fingerprint": fingerprint,
                "subkeys": [],
            }
        )

    def export_gpg_key(self, keyid: str, dest: str, armor: bool, secret: bool) -> None:
        """Record a simulated ``gpg.export_key`` write of one key to ``dest``.

        The export file lands in ``remote_files``, ``existing_files`` and a
        minimal ``file_stats`` row, so the existence/content facts see it
        consistently -- but *not* in ``gpg_keyrings``: an export is a key
        dump, not an installed keyring. No real key material is modeled:
        the bytes are a deterministic placeholder naming the exported key
        (armored markers with ``armor``, a PRIVATE block for ``secret``).
        """
        if armor:
            kind = "PRIVATE" if secret else "PUBLIC"
            data = (
                f"-----BEGIN PGP {kind} KEY BLOCK-----\n"
                f"simulated export of {keyid}\n"
                f"-----END PGP {kind} KEY BLOCK-----\n"
            ).encode()
        else:
            data = f"\x99simulated export of {keyid}".encode()
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }

    def delete_gpg_key(self, fingerprint: str, secret: bool) -> None:
        """Record a simulated ``gpg.delete_key`` from the default keychain.

        Drops the named key (matched by fingerprint, or id when none was
        attached -- the operation's own fallback) from ``gpg_keys``, and
        from ``gpg_secret_keys`` too with ``secret`` (the
        ``--delete-secret-and-public-keys`` form removes both halves). The
        key's ownertrust assignment leaves ``gpg_trust`` either way, like
        the real trustdb prune. Keyring *files* are untouched -- this
        mutator models the keychain, not ``gpg.key``'s removal. The real
        gpg refusal to delete a public key that still has secret material
        is not modeled; the seam stays safe regardless.
        """
        self.gpg_keys[:] = [
            key
            for key in self.gpg_keys
            if key.get("fingerprint", key["id"]) != fingerprint
        ]
        if secret:
            self.gpg_secret_keys[:] = [
                key
                for key in self.gpg_secret_keys
                if key.get("fingerprint", key["id"]) != fingerprint
            ]
        self.gpg_trust[:] = [
            entry for entry in self.gpg_trust if entry["fingerprint"] != fingerprint
        ]

    def gen_gpg_key(
        self, real_name: str, email: str, algo: str, length: int, expire: str
    ) -> None:
        """Record a simulated ``gpg.gen_key`` in the default keychain.

        Appends a stub key entry to ``gpg_keys`` *and* ``gpg_secret_keys``
        (a real generation creates both halves) plus the ultimate
        ownertrust assignment a fresh own key exports, so a repeat
        ``gen_key`` sees the uid and every consulted fact stays consistent.
        The stub's fingerprint/uid_hash derive from the uid's hash (stable
        across phases); ``algo`` and ``expire`` are not recorded -- no
        ``GpgKeyInfo`` field carries them. An already-present uid is left
        alone.
        """
        uid = f"{real_name} <{email}>"
        if any(key.get("uid") == uid for key in self.gpg_keys):
            return
        fingerprint = hashlib.sha256(uid.encode()).hexdigest().upper()[:40]
        entry: GpgKeyInfo = {
            "id": fingerprint[-16:],
            "validity": "u",
            "length": length,
            "fingerprint": fingerprint,
            "uid_hash": hashlib.sha256(uid.encode()).hexdigest().upper()[:40],
            "uid": uid,
            "subkeys": [],
        }
        self.gpg_keys.append({**entry})
        self.gpg_secret_keys.append({**entry})
        self.gpg_trust.append(
            {"fingerprint": fingerprint, "ownertrust": 6, "level": "ultimate"}
        )

    def set_gpg_trust(self, fingerprint: str, ownertrust: int) -> None:
        """Record a simulated ``gpg.trust`` ownertrust assignment.

        Upserts the ``GpgKeyTrust`` entry for ``fingerprint`` in
        ``gpg_trust`` so a repeat ``trust`` reads the level back and is a
        no-op. The numeric-to-level mapping mirrors gpg's ``_TRUST_LEVELS``
        (5 and 6 both mean ultimate; an unrecognized number keeps its raw
        value under ``unknown``).
        """
        level = {
            1: "unknown",
            2: "never",
            3: "marginal",
            4: "full",
            5: "ultimate",
            6: "ultimate",
        }.get(ownertrust, "unknown")
        for entry in self.gpg_trust:
            if entry["fingerprint"] == fingerprint:
                entry["ownertrust"] = ownertrust
                entry["level"] = level
                break
        else:
            self.gpg_trust.append(
                {
                    "fingerprint": fingerprint,
                    "ownertrust": ownertrust,
                    "level": level,
                }
            )

    def enable_module(self, module: str, stream: str) -> None:
        """Record a simulated ``dnf module enable``: set the enabled stream.

        A module is either enabled on one stream or disabled, never both, so
        enabling also clears any disabled marker -- keeping ``enabled_modules``
        and ``disabled_modules`` mutually consistent for the ``DnfEnabledModules``
        and ``DnfDisabledModules`` facts. A repeat enable of the same stream is
        then a no-op, and a switch re-points the stream.
        """
        self.enabled_modules[module] = stream
        if module in self.disabled_modules:
            self.disabled_modules.remove(module)

    def disable_module(self, module: str) -> None:
        """Record a simulated ``dnf module disable``: mark the module disabled.

        The mirror of ``enable_module``: clears any enabled stream and keeps
        ``disabled_modules`` sorted, matching what ``DnfDisabledModules`` reports
        (its seam projection yields sorted entries with stream "" -- a
        simulated disable names no stream), so a repeat disable
        is a no-op.
        """
        self.enabled_modules.pop(module, None)
        if module not in self.disabled_modules:
            self.disabled_modules.append(module)
        self.disabled_modules.sort()

    def make_link(self, path: str, target: str, symbolic: bool = True) -> None:
        """Record a simulated ``ln [-s] target path``.

        A symlink fills both link stores: the bare ``links`` mapping the
        cheap ``FileLink`` readlink probe reads, and a ``link_stats`` row
        (root-owned, mode 777 -- ``ln -s``'s own defaults) carrying the
        ``link_target`` the stat-level ``Link`` fact ``files.link`` compares,
        so the next step's fresh cache reads the link back as requested.
        A hard link (``symbolic=False``) is not a symlink at all: it is one
        more name for ``target``'s inode, so the pair lands in
        ``same_files``, the path becomes a regular file answering with a
        copy of ``target``'s metadata row, and the typed link stores record
        the wrong-type existence (``False``) a real ``stat`` of a hard link
        reports -- never ``None``, which would mean "absent".
        """
        if symbolic:
            self.links[path] = target
            existing = self.link_stats.get(path)
            row = dict(existing) if isinstance(existing, dict) else {}
            row.setdefault("mode", 777)
            row.setdefault("user", "root")
            row.setdefault("group", "root")
            row["link_target"] = target
            self.link_stats[path] = row
            return
        self.links.pop(path, None)
        # stat reports "-" for a hard link: the Link/Directory facts answer
        # False ("exists, wrong type"), which is exactly what files.link's
        # hard-link idempotence branch keys on before re-reading File and
        # SameFile.
        self.link_stats[path] = False
        self.directory_stats[path] = False
        self.same_files.add((path, target))
        self.existing_files.add(path)
        source = self.file_stats.get(target)
        self.file_stats[path] = (
            dict(source)
            if isinstance(source, dict)
            else {"mode": 644, "user": "root", "group": "root"}
        )

    def remove_link(self, path: str) -> None:
        """Record a simulated ``rm -f`` of a link: both link stores go.

        ``links`` (the readlink probe) and ``link_stats`` (the stat-level
        ``Link`` fact) stop answering for the path. A hard link is a plain
        file name, so its side of the state goes too -- the ``same_files``
        pairs naming the path, its existence marker and its ``file_stats``
        row: the ``rm -f`` drops one name of the inode, never the inode.
        """
        self.links.pop(path, None)
        self.link_stats.pop(path, None)
        self.same_files = {pair for pair in self.same_files if path not in pair}
        self.existing_files.discard(path)
        self.file_stats.pop(path, None)

    def create_file(self, src: str) -> None:
        """Record a simulated ``touch``: seed the existence marker and a
        ``file_stats`` row (root-owned, mode 644 -- an unprivileged
        ``touch`` creates exactly that), so the ``File`` fact ``files.file``
        consults and the cheaper ``FileExists`` probe both answer for the
        path. An existing row is kept: ``touch`` only moves timestamps.
        """
        self.existing_files.add(src)
        self.file_stats.setdefault(
            src, {"mode": 644, "user": "root", "group": "root"}
        )

    def remove_file(self, src: str) -> None:
        """Record a simulated ``rm -f``: the existence marker and any
        ``file_stats`` row go, so neither the ``File`` fact nor the
        ``FileExists`` probe answers for the path afterwards.
        """
        self.existing_files.discard(src)
        self.file_stats.pop(src, None)

    def add_group(self, name: str) -> None:
        """Record a simulated ``groupadd``: append the name to ``os_groups``.

        The ``Groups`` fact reads ``os_groups`` in file order, so a new group
        lands at the end and an already-present name is left alone (the real
        ``groupadd`` would exit non-zero; the operation's fact check never
        yields the command in that case). ``system``/``gid`` carry no
        simulated state: the fact reports names only.
        """
        if name not in self.os_groups:
            self.os_groups.append(name)

    def remove_group(self, name: str) -> None:
        """Record a simulated ``groupdel``: drop the name from ``os_groups``."""
        if name in self.os_groups:
            self.os_groups.remove(name)

    def add_user(self, cmd: UserAddCommand) -> None:
        """Record a simulated ``useradd``/``pw useradd``: add an ``os_users`` row.

        ``server.user``'s create mutation writes the row the ``Users`` fact
        reads back, seeding every field the operation compared (home, shell,
        group, groups, comment, password) so the next step's fresh fact cache
        sees the user already as requested. A missing detail stays ``None``
        (and ``groups`` ``[]``) exactly like an empty passwd field; ``uid``
        is the requested one or the next number after the seeded users, and
        the primary group defaults to the user's own name as useradd's
        user-Private-Group mode does. The ``-m`` skeleton content and the
        ``-r``/``-o``/``-M`` flags carry no simulated state. The home
        directory itself is not touched here: the operation's paired
        ``_HomeMkdirCommand``/``_HomeChownCommand`` write that seam.
        """
        info: dict[str, Any] = {
            "home": cmd.home if cmd.home else f"/home/{cmd.user}",
            "comment": cmd.comment,
            "shell": cmd.shell_path,
            "group": cmd.group or cmd.user,
            "groups": list(cmd.groups),
            "uid": cmd.uid if cmd.uid else 1000 + len(self.os_users),
            "gid": None,
            "lastlog": None,
            "login_time": None,
            "password": cmd.password,
        }
        self.os_users[cmd.user] = info

    def modify_user(self, cmd: UserModCommand) -> None:
        """Record a simulated ``usermod``/``pw usermod``: edit an ``os_users`` row.

        Only the fields the operation deemed different are written (the
        descriptor carries nothing else), so a repeat update reads back equal
        and is a no-op. A ``groups`` write replaces the membership, or unions
        it in when ``append`` set (``usermod -a -G``); the FreeBSD password
        form (the ``echo '<hash>' | pw usermod -n <user> -H 0`` pipe) writes
        the hash through the same path. An absent user changes nothing -- the
        real command would exit non-zero and fail the apply step.
        """
        info = self.os_users.get(cmd.user)
        if info is None:
            return
        if cmd.home:
            info["home"] = cmd.home
        if cmd.shell_path:
            info["shell"] = cmd.shell_path
        if cmd.group:
            info["group"] = cmd.group
        if cmd.comment:
            info["comment"] = cmd.comment
        if cmd.password:
            info["password"] = cmd.password
        if cmd.groups:
            if cmd.append:
                current = list(info.get("groups") or [])
                info["groups"] = current + [g for g in cmd.groups if g not in current]
            else:
                info["groups"] = list(cmd.groups)

    def remove_user(self, name: str) -> None:
        """Record a simulated ``userdel``/``pw userdel``: drop the ``os_users`` row.

        The home directory is deliberately left in place -- plain ``userdel``
        removes no files (only ``userdel -r`` does, which the baseline and this
        operation never emit). An absent user is tolerated.
        """
        self.os_users.pop(name, None)

    def install_authorized_keys_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``user_authorized_keys`` install of the spooled text.

        ``server.user_authorized_keys`` first uploads the transformed
        key-file content to a ``/tmp`` spool path (the paired
        ``AuthorizedKeysUploadCommand`` runs through ``put_file``, storing
        the bytes in ``remote_files``), then installs it over the key file
        and removes the spool. This mirrors the install-and-remove: the
        spool bytes are popped out of ``remote_files`` and stored at
        ``dest`` -- the same store ``AuthorizedKeysFile`` reads back -- so a
        repeat request with identical arguments is a no-op, since the fresh
        per-step fact cache sees these bytes and the content matches.
        ``cp`` over an existing file preserves its owner, so an existing
        ``file_stats`` row keeps its user/group while the mode resets to
        600 (the real command's ``chmod 600``) and the size refreshes; a
        descriptor-only test with no prior row (the paired
        ``_KeysTouchCommand`` never ran) gets a minimal root-owned mode-600
        one, and the path joins ``existing_files``. The parsed
        ``authorized_keys`` seam is recomputed from the installed text
        through ``AuthorizedKeys.parse`` under the caller-supplied ``path``
        key, so ``AuthorizedKeys(user, path=...)`` -- the shape this
        operation always reads -- sees exactly the written keys: content-
        based, so a repeat install never duplicates an entry and a
        ``delete_keys`` rewrite drops the unrequested ones. An absent spool
        path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        stats = self.file_stats.get(dest)
        if isinstance(stats, dict):
            stats["mode"] = 600
            stats["size"] = len(data)
        else:
            self.file_stats[dest] = {
                "user": "root",
                "group": "root",
                "mode": 600,
                "size": len(data),
            }
        self.authorized_keys[dest] = AuthorizedKeys.parse(
            data.decode("utf-8").splitlines()
        )

    def install_etc_hosts_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``etc_hosts`` install of the spooled hosts text.

        ``server.etc_hosts`` first uploads the transformed hosts-file
        content to a ``/tmp`` spool path (the paired
        ``EtcHostsUploadCommand`` runs through ``put_file``, storing the
        bytes in ``remote_files``), then installs it over the hosts file and
        removes the spool. This mirrors the install-and-remove: the spool
        bytes are popped out of ``remote_files`` and stored at ``dest`` --
        the same store ``AuthorizedKeysFile`` reads back and ``EtcHosts``
        parses directly, so no separate parsed seam exists to refresh and a
        repeat request with identical arguments is a no-op, since the fresh
        per-step fact cache sees these bytes and the entry matches.
        ``cp`` over an existing file preserves its owner, so an existing
        ``file_stats`` row keeps its user/group while the mode resets to
        644 (the real command's ``chmod 0644``) and the size refreshes; a
        descriptor-only test with no prior row gets a minimal root-owned
        mode-644 one, and the path joins ``existing_files``. An empty
        payload is legitimate (a removal that empties the hosts file) and
        installs as empty bytes. An absent spool path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        stats = self.file_stats.get(dest)
        if isinstance(stats, dict):
            stats["mode"] = 644
            stats["size"] = len(data)
        else:
            self.file_stats[dest] = {
                "user": "root",
                "group": "root",
                "mode": 644,
                "size": len(data),
            }

    def install_locale_gen_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``locale`` install of the spooled locale.gen text.

        ``server.locale`` first uploads the transformed locale.gen content
        to a ``/tmp`` spool path (the paired ``LocaleUploadCommand`` runs
        through ``put_file``, storing the bytes in ``remote_files``), then
        installs it over the file and removes the spool. This mirrors the
        install-and-remove: the spool bytes are popped out of
        ``remote_files`` and stored at ``dest`` -- the same store
        ``AuthorizedKeysFile`` reads back -- so a repeat request with
        identical arguments is a no-op, since the fresh per-step fact cache
        sees these bytes and the definition line matches. ``cp`` over an
        existing file preserves its owner, so an existing ``file_stats``
        row keeps its user/group while the mode resets to 644 (the real
        command's ``chmod 0644``) and the size refreshes; a descriptor-only
        test with no prior row gets a minimal root-owned mode-644 one, and
        the path joins ``existing_files``. An absent spool path installs
        nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        stats = self.file_stats.get(dest)
        if isinstance(stats, dict):
            stats["mode"] = 644
            stats["size"] = len(data)
        else:
            self.file_stats[dest] = {
                "user": "root",
                "group": "root",
                "mode": 644,
                "size": len(data),
            }

    def generate_locales(self, locale: str, present: bool) -> None:
        """Record a simulated ``locale-gen``: flip the name in ``locales``.

        The real ``locale-gen`` regenerates every uncommented definition in
        ``/etc/locale.gen``; the seam models only the request's delta --
        enabling appends the name to the ``Locales`` listing if absent,
        disabling drops it -- so the next step's fresh fact cache sees the
        locale in the requested state and a repeat request is a no-op.
        """
        if present:
            if locale not in self.locales:
                self.locales.append(locale)
        elif locale in self.locales:
            self.locales.remove(locale)

    def install_security_limits_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``security_limit`` install of the spooled limits text.

        ``server.security_limit`` first uploads the transformed limits.conf
        content to a ``/tmp`` spool path (the paired
        ``SecurityLimitUploadCommand`` runs through ``put_file``, storing the
        bytes in ``remote_files``), then installs it over the file and
        removes the spool. This mirrors the install-and-remove: the spool
        bytes are popped out of ``remote_files`` and stored at ``dest`` --
        the same store ``AuthorizedKeysFile`` reads back -- so a repeat
        request with identical arguments is a no-op, since the fresh
        per-step fact cache sees these bytes and the limit line matches.
        ``cp`` over an existing file preserves its owner, so an existing
        ``file_stats`` row keeps its user/group while the mode resets to 644
        (the real command's ``chmod 0644``) and the size refreshes; a
        descriptor-only test with no prior row gets a minimal root-owned
        mode-644 one, and the path joins ``existing_files``. An absent spool
        path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        stats = self.file_stats.get(dest)
        if isinstance(stats, dict):
            stats["mode"] = 644
            stats["size"] = len(data)
        else:
            self.file_stats[dest] = {
                "user": "root",
                "group": "root",
                "mode": 644,
                "size": len(data),
            }

    def set_hostname(self, name: str) -> None:
        """Record a simulated ``hostnamectl set-hostname``/``hostname``.

        Both command forms set the same running hostname, so the seam just
        replaces ``os_hostname`` -- the one field the ``Hostname`` fact
        reads -- and the next step's fresh fact cache sees the new name.
        """
        self.os_hostname = name

    def set_timezone(self, timezone: str) -> None:
        """Record a simulated ``timedatectl set-timezone``.

        The seam replaces ``os_timezone`` -- the one field the ``Timezone``
        fact reads -- so the next step's fresh fact cache sees the new zone
        and a repeat timezone set is a no-op.
        """
        self.os_timezone = timezone

    def kill_process(self, pid: int) -> None:
        """Record a simulated ``kill``: drop the PID from ``processes``.

        The ``Processes`` fact reads ``processes``, so the next step's fresh
        fact cache no longer lists the killed PID. The simulation treats
        every signal as terminal (it models no stopped/continued states),
        and an absent PID is left alone -- the real ``kill`` would exit
        non-zero and fail the apply step.
        """
        self.processes.pop(pid, None)

    def load_modules(self, modules: tuple[str, ...]) -> None:
        """Record a simulated ``modprobe -a``: add names to ``kernel_modules``.

        The ``KernelModules`` fact keys on the module name, so a newly loaded
        module gets a minimal Linux-shaped info dict; an already-loaded name
        is left alone (the real ``modprobe -a`` succeeds silently on it).
        ``force`` carries no simulated state: the fact reports no version or
        force-related field.
        """
        for name in modules:
            self.kernel_modules.setdefault(
                name, {"size": "16384", "instances": 1, "state": "Live"}
            )

    def unload_modules(self, modules: tuple[str, ...]) -> None:
        """Record a simulated ``modprobe -r -a``: drop names from ``kernel_modules``.

        The simulation models no reference counts, so an in-use module is
        unloaded too -- the real ``modprobe -r`` would exit non-zero and fail
        the apply step. An absent name is left alone.
        """
        for name in modules:
            self.kernel_modules.pop(name, None)

    def mount_filesystem(
        self, path: str, device: str | None, fs_type: str | None, options: tuple[str, ...]
    ) -> None:
        """Record a simulated ``mount``: add the mount point to ``mounts``.

        The ``Mounts`` fact keys on the mount point, so the entry holds the
        device/type as given (None when the request omitted them -- the
        simulation has no fstab to resolve them from) and the requested
        options. A repeat mount is then a no-op for the operation's
        membership check, and an unmount or remount by device fallback finds
        the entry.
        """
        self.mounts[path] = {"device": device, "type": fs_type, "options": list(options)}

    def unmount_filesystem(self, path: str) -> None:
        """Record a simulated ``umount``: drop the mount point from ``mounts``.

        An absent mount point is left alone -- the real ``umount`` would exit
        non-zero and fail the apply step.
        """
        self.mounts.pop(path, None)

    def remount_filesystem(self, path: str, options: tuple[str, ...]) -> None:
        """Record a simulated options remount: merge options into the entry.

        The existing options are kept and the requested ones appended in
        order, so the operation's subset check sees a repeat remount as a
        no-op. The BSD form's device/type already sit in the entry and are
        left alone. An absent mount point gains a minimal entry (the real
        ``mount -o remount`` would fail; the seam stays safe regardless).
        """
        info = self.mounts.setdefault(path, {"device": None, "type": None, "options": []})
        existing = list(info.get("options", []))
        info["options"] = existing + [opt for opt in options if opt not in existing]

    def install_repo_file(self, spool: str, dest: str, mode: int = 644) -> None:
        """Record a simulated ``dnf.repo`` install of the spooled ``.repo`` text.

        ``dnf.repo`` first uploads the generated content to a ``/tmp`` spool
        path (the paired ``DnfRepoUploadCommand`` runs through ``put_file``,
        storing the bytes in ``remote_files``), then installs it as the
        ``dest`` repo file and removes the spool. This mirrors the
        install-and-remove: the spool bytes are popped out of ``remote_files``
        and stored at ``dest`` -- the same store ``DnfRepoFile`` reads back
        (and ``RemoteFileSha1`` hashes) -- so a repeat ``dnf.repo`` with
        identical arguments is a no-op, since the fresh per-step fact cache
        sees these bytes and the content matches. The path is added to
        ``existing_files`` plus a minimal ``file_stats`` row (root-owned,
        ``mode`` -- the real command's ``chmod``, 644 by default or 600 when
        the content carries a ``proxy_password``). An absent spool path
        installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": mode,
            "size": len(data),
        }

    def remove_repo_file(self, path: str) -> None:
        """Record a simulated ``dnf.repo`` removal (``present=False``).

        The content bytes (``remote_files``), the existence marker
        (``existing_files``) and any ``file_stats`` row all go, so the file
        facts stay consistent. An absent path is tolerated -- ``rm -f``
        exits 0 on a missing file.
        """
        self.remote_files.pop(path, None)
        self.existing_files.discard(path)
        self.file_stats.pop(path, None)

    def install_systemd_conf_file(self, spool: str, dest: str) -> None:
        """Record a simulated systemd file install of the spooled config text.

        The ``dropin``/``tmpfiles``/``sysusers`` write direction: the paired
        ``SystemdFileUploadCommand`` spools the content into ``remote_files``
        (through ``put_file``), then this install pops the spool and stores the
        bytes at ``dest`` -- the same store ``_SystemdConfFile`` reads back --
        so a repeat with identical content is a no-op. Mirrors
        ``install_repo_file``: the path is added to ``existing_files`` plus a
        minimal root-owned mode-644 ``file_stats`` row (the real command's
        ``chmod 0644``). An absent spool path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }

    def remove_systemd_conf_file(self, path: str) -> None:
        """Record a simulated systemd config removal (``present=False``).

        The content bytes (``remote_files``), the existence marker
        (``existing_files``) and any ``file_stats`` row all go, so the file
        facts stay consistent. An absent path is tolerated -- ``rm -f`` exits
        0 on a missing file. Mirrors ``remove_repo_file``.
        """
        self.remote_files.pop(path, None)
        self.existing_files.discard(path)
        self.file_stats.pop(path, None)

    def set_sysctl(self, key: str, value: str) -> None:
        """Record a simulated ``sysctl -w``: store the parsed live value.

        The runtime half of ``sysctl.setting`` (and every row of a
        ``reload_sysctl_file`` pass). The ``key = value`` row goes through
        ``Sysctl.parse`` so the store holds exactly the shape the fact
        returns -- an int for digits, a list for multi-word values -- and a
        repeat apply compares equal.
        """
        self.sysctls.update(Sysctl.parse([f"{key} = {value}"]))

    def install_sysctl_conf_file(self, spool: str, dest: str) -> None:
        """Record a simulated sysctl file install of the spooled config text.

        The ``sysctl.setting``/``sysctl.conf`` write direction: the paired
        ``SysctlFileUploadCommand`` spools the content into ``remote_files``
        (through ``put_file``), then this install pops the spool and stores
        the bytes at ``dest`` -- the same store ``SysctlFile``/``SysctlFiles``
        read back -- so a repeat with identical content is a no-op. Mirrors
        ``install_systemd_conf_file``: the path is added to ``existing_files``
        plus a minimal root-owned mode-644 ``file_stats`` row (the real
        command's ``chmod 0644``). An absent spool path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }

    def remove_sysctl_conf_file(self, path: str) -> None:
        """Record a simulated sysctl config removal (``conf(present=False)``).

        The content bytes (``remote_files``), the existence marker
        (``existing_files``) and any ``file_stats`` row all go, so the file
        facts stay consistent. An absent path is tolerated -- ``rm -f`` exits
        0 on a missing file. Mirrors ``remove_systemd_conf_file``.
        """
        self.remote_files.pop(path, None)
        self.existing_files.discard(path)
        self.file_stats.pop(path, None)

    def reload_sysctl_file(self, path: str) -> None:
        """Record a simulated ``sysctl -p <path>``: apply the file's settings.

        Each assignment in the installed config parses with ``_parse_conf``
        (comments/blanks skipped) and applies through ``set_sysctl``, so the
        ``Sysctl`` fact reflects the file after a reload -- exactly the
        runtime effect of the real command. An absent path applies nothing.
        """
        data = self.remote_files.get(path)
        if data is None:
            return
        for key, value in _parse_conf(data.decode("utf-8")).items():
            self.set_sysctl(key, value)

    def _set_systemd_unit_file_state(self, unit: str, state: str) -> None:
        """Set a unit's boot state in both stores the systemd facts consult.

        ``systemd_unit_files`` (the ``SystemdUnitFiles`` state/preset table the
        ``preset`` operation reads) and ``systemd_unit_properties`` (the
        ``UnitFileState`` property the ``mask`` operation reads) both carry a
        unit's boot state, so a mask/unmask/preset mutation updates them
        together -- otherwise one operation's idempotence read would miss the
        change another made. An absent unit gains a minimal entry.
        """
        entry = self.systemd_unit_files.setdefault(unit, {"state": "", "preset": ""})
        entry["state"] = state
        props = self.systemd_unit_properties.setdefault(unit, {})
        props["UnitFileState"] = state

    def mask_systemd_unit(self, unit: str) -> None:
        """Record a simulated ``systemctl mask``: the unit's state becomes
        ``masked`` (in both unit-file stores). Masking is orthogonal to the
        boot-enable set, so ``enabled_services`` is left alone."""
        self._set_systemd_unit_file_state(unit, "masked")

    def unmask_systemd_unit(self, unit: str) -> None:
        """Record a simulated ``systemctl unmask``: the mask symlink is removed,
        leaving the unit ``disabled`` (the common post-unmask state)."""
        self._set_systemd_unit_file_state(unit, "disabled")

    def preset_systemd_unit(self, unit: str) -> None:
        """Record a simulated ``systemctl preset <unit>``: apply the unit's
        preset policy. Only an ``enabled``/``disabled`` policy changes the
        state (and the boot-enable set); an empty policy or an absent unit is a
        no-op, exactly as the real command leaves the unit unchanged."""
        entry = self.systemd_unit_files.get(unit)
        if entry is None:
            return
        policy = entry.get("preset", "")
        if policy not in ("enabled", "disabled"):
            return
        self._set_systemd_unit_file_state(unit, policy)
        if policy == "enabled":
            self.enabled_services.add(unit)
        else:
            self.enabled_services.discard(unit)

    def preset_all_systemd_units(self) -> None:
        """Record a simulated ``systemctl preset-all``: apply every installed
        unit's preset policy (the per-unit ``preset_systemd_unit`` over a
        snapshot of the keys, so the iteration never mutates mid-loop)."""
        for unit in list(self.systemd_unit_files):
            self.preset_systemd_unit(unit)

    def put_file(self, src, dest: str) -> None:
        """Record a simulated scp upload: store the IO-like source's content.

        The source is read through the library's own ``read_source_bytes``
        (rewind + UTF-8 normalization), so the bytes stored are exactly what
        ``LocalSha1`` hashes and what the real transport would copy. Storing
        the content -- rather than only its hash -- lets ``RemoteFileSha1``
        derive the digest and lets a later ``files.get`` read the same bytes
        back out.
        """
        self.remote_files[dest] = read_source_bytes(src)

    def get_file(self, src: str, dest) -> None:
        """Record a simulated scp download: write the remote content into dest.

        The mirror of ``put_file``: the bytes stored at the remote path are
        written into the IO-like destination through the library's own
        ``write_dest_bytes`` (rewind + truncate + UTF-8 for a text stream), so
        the destination ends up holding exactly the content ``RemoteFileSha1``
        describes and a repeat get is a no-op. An absent remote path has no
        content to copy, so the destination is left untouched.
        """
        data = self.remote_files.get(src)
        if data is not None:
            write_dest_bytes(dest, data)

    def install_file(
        self,
        spool: str,
        dest: str,
        mode: int,
        user: str | None = None,
        group: str | None = None,
    ) -> None:
        """Record a simulated ``files.install``: install the spooled content.

        The elevated second half of ``files.install``: the paired
        ``InstallSpoolCommand`` spools the content into ``remote_files``
        (through ``put_file``), then this install pops the spool and stores
        the bytes at ``dest`` -- the same store ``FileContents`` reads back
        -- so a repeat with identical content is a no-op. Mirrors
        ``install_systemd_conf_file`` with the caller-chosen mode: the path
        is added to ``existing_files`` plus a ``file_stats`` row carrying
        the operation's canonical octal-digit mode (the real command's
        ``chmod``) and the requested owner -- the ``chown``/``chgrp`` step
        sets only the halves the request named, and neither named leaves
        the root:root owner the elevated ``cp`` creates -- while the
        ``mkdir -p`` parent lands in ``directory_stats``. An absent spool
        path installs nothing.
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.existing_files.discard(spool)
        self.file_stats.pop(spool, None)
        self.make_directory(str(PurePosixPath(dest).parent))
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": user or "root",
            "group": group or "root",
            "mode": mode,
            "size": len(data),
        }

    def move_path(self, src: str, dest: str) -> None:
        """Record a simulated ``mv``: relocate ``src`` into the directory ``dest``.

        The moved path is ``dest/basename(src)``, exactly as the operation
        computes it. The content bytes move within ``remote_files`` (so
        ``FileContents`` and ``RemoteFileSha1`` read them at the new path),
        any ``existing_files`` membership moves with them, and a seeded
        ``file_stats`` row relocates too, so the ``File`` fact answers
        consistently after the move. An absent source moves nothing -- the
        real ``mv`` would exit non-zero and fail the apply step.
        """
        full_dest = posixpath.join(dest, PurePosixPath(src).name)
        data = self.remote_files.pop(src, None)
        if data is not None:
            self.remote_files[full_dest] = data
        if src in self.existing_files:
            self.existing_files.discard(src)
            self.existing_files.add(full_dest)
        stats = self.file_stats.pop(src, None)
        if stats is not None:
            self.file_stats[full_dest] = stats

    def copy_path(self, src: str, dest: str) -> None:
        """Record a simulated ``cp -r``: duplicate ``src`` into the directory ``dest``.

        The copied path is ``dest/basename(src)``, exactly as the operation
        computes it, and the source stays in place -- the difference from
        ``move_path``. The content bytes are duplicated within
        ``remote_files`` (so ``FileContents`` and ``RemoteFileSha1`` read
        them at both paths, which is what makes a repeat ``files.copy``
        with ``overwrite`` unset a no-op), ``existing_files`` gains the new
        member, and seeded ``file_stats``/``directory_stats`` rows are
        copied as fresh dicts so the two paths never share one mutable
        row. Nested paths inside a copied directory are not modelled --
        the simulation's stores are flat. An absent source copies nothing:
        the real ``cp`` would exit non-zero and fail the apply step. The
        ``overwrite`` flag needs no separate arm: ``cp -r -f`` replaces the
        target's content exactly as the plain copy does.
        """
        full_dest = posixpath.join(dest, PurePosixPath(src).name)
        data = self.remote_files.get(src)
        if data is not None:
            self.remote_files[full_dest] = data
        if src in self.existing_files:
            self.existing_files.add(full_dest)
        stats = self.file_stats.get(src)
        if stats is not None:
            self.file_stats[full_dest] = (
                dict(stats) if isinstance(stats, dict) else stats
            )
        dir_stats = self.directory_stats.get(src)
        if dir_stats is not None:
            self.directory_stats[full_dest] = (
                dict(dir_stats) if isinstance(dir_stats, dict) else dir_stats
            )

    def remove_tree(self, src: str) -> None:
        """Record a simulated ``rm -rf``: drop the path from every file store.

        The overwrite branch of ``files.move`` and the removal/``force``
        branches of ``files.directory``/``files.link`` remove the path
        before anything lands, so the content bytes (``remote_files``), the
        existence marker (``existing_files``), any seeded ``file_stats``,
        ``directory_stats`` and ``link_stats`` rows, the ``links`` mapping
        and any ``same_files`` pair naming the path all go -- a real
        ``rm -rf`` clears a file, a directory or a symlink alike, and the
        typed facts must stop answering for the path.
        """
        self.remote_files.pop(src, None)
        self.existing_files.discard(src)
        self.links.pop(src, None)
        self.same_files = {pair for pair in self.same_files if src not in pair}
        self.file_stats.pop(src, None)
        self.directory_stats.pop(src, None)
        self.link_stats.pop(src, None)

    def make_directory(self, path: str) -> None:
        """Record a simulated ``mkdir -p``: add a directory row if absent.

        ``files.directory``'s create mutation writes into
        ``directory_stats`` (the seam the ``Directory`` fact reads) with
        the default umask-resolved ``755`` mode the real command produces,
        with the root:root owner a fresh ``mkdir`` leaves -- the row is
        complete from the start (like ``create_file``'s) so later owner
        comparisons, which subscribe ``info["user"]``/``info["group"]``,
        never KeyError. An existing path is left alone -- ``mkdir -p``
        succeeds silently on an existing directory without touching its
        metadata.
        """
        self.directory_stats.setdefault(
            path, {"mode": 755, "user": "root", "group": "root"}
        )

    def chmod_path(self, path: str, mode: int | str) -> None:
        """Record a simulated ``chmod``: replace the row's mode.

        ``files.directory``'s mode mutation writes the canonical spelling
        the operation normalised (an octal-digit int, or the symbolic
        string verbatim -- the simulation does not fold a symbolic mode
        into octal). ``chmod`` addresses files and directories alike, so
        both stats stores are visited; a ``False`` row (wrong type) or an
        absent path is left alone, and ``recursive`` has no separate
        effect because the stores are flat.
        """
        for store in (self.file_stats, self.directory_stats):
            stats = store.get(path)
            if isinstance(stats, dict):
                stats["mode"] = mode

    def chown_path(
        self,
        path: str,
        user: str | None,
        group: str | None,
        dereference: bool = True,
    ) -> None:
        """Record a simulated ``chown``/``chgrp``: replace the row's owner.

        ``files.directory``'s owner mutation sets only the halves the
        request named (``chgrp`` touches the group, a user-only ``chown``
        the user), exactly as the real commands do. Like ``chmod_path``
        both stats stores are visited and non-dict rows are left alone.
        ``dereference=False`` (``chown -h``, what ``files.link`` requests for
        the link itself) edits the symlink's own ``link_stats`` row instead;
        only a dict row is a symlink -- a hard link's ``False`` row or an
        absent path means the ownership lives with the file stores after
        all.
        """
        if not dereference and isinstance(self.link_stats.get(path), dict):
            stores: tuple[dict[str, dict[str, Any] | bool], ...] = (self.link_stats,)
        else:
            stores = (self.file_stats, self.directory_stats)
        for store in stores:
            stats = store.get(path)
            if isinstance(stats, dict):
                if user:
                    stats["user"] = user
                if group:
                    stats["group"] = group

    def set_file_flags(self, path: str, flags: tuple[str, ...]) -> None:
        """Record a simulated ``chattr +flags``: add the letters to the path.

        ``files.flags``' set mutation writes into ``file_flags`` (the seam
        the ``FileFlags`` fact reads), merging the requested letters into any
        already set so a repeat ``files.flags`` reads them back and is a
        no-op. Flags outside the ext2 vocabulary cannot reach this arm --
        the operation validates before yielding -- and ``chattr`` addresses
        files and directories alike, so one flat store covers both.
        """
        self.file_flags.setdefault(path, set()).update(flags)

    def clear_file_flags(self, path: str, flags: tuple[str, ...]) -> None:
        """Record a simulated ``chattr -flags``: drop the letters from the path.

        ``files.flags``' ``present=False`` mutation removes only the letters
        the operation computed (``current & requested``), leaving any other
        attribute set, exactly as the real command does; the operation only
        yields the clear when at least one requested letter is present, so a
        repeat reads back a no-op. An absent path is tolerated (the real
        command would exit non-zero, but the fact short-circuits absence to
        ``[]`` and the operation then plans no change).
        """
        self.file_flags.setdefault(path, set()).difference_update(flags)

    def add_worktree(self, worktree: str) -> None:
        """Record a simulated ``git worktree add``: create the tree directory.

        ``git.worktree``'s create mutation. The worktree path lands in
        ``directory_stats`` (the seam both ``_DirectoryExists`` and
        ``files.Directory`` read) as a non-empty metadata dict, so a repeat
        ``git.worktree`` for the same path sees the directory present and
        takes the pull/no-op branch instead of re-adding it. The source
        repository's ``.git/worktrees`` metadata the real command also writes
        is not modelled -- nothing in the library reads it back.
        """
        self.directory_stats[worktree] = {"mode": 755}

    def remove_worktree(self, worktree: str) -> None:
        """Record a simulated ``git worktree remove``: drop the tree directory.

        ``git.worktree``'s ``present=False`` mutation: the worktree path leaves
        ``directory_stats``, so a repeat removal sees it absent and is a no-op.
        An absent path is tolerated (the real command would exit non-zero, but
        the operation only yields the removal when the directory exists).
        """
        self.directory_stats.pop(worktree, None)

    def pull_worktree(
        self, worktree: str, from_remote_branch: tuple[str, str] | None
    ) -> None:
        """Record a simulated ``git pull``: fast-forward the tree's local HEAD.

        ``git.worktree``'s pull mutation. It advances ``git_local_commits``
        (the ``GitLocalCommit`` seam, keyed by the worktree path and ``HEAD``)
        to the remote tip in ``git_remote_commits`` (the
        ``GitRemoteBranchCommit`` seam), so a repeat pull reads local == remote
        and short-circuits via the operation's ``skip_pull``. The remote and
        branch are resolved exactly as the operation resolves them: the
        explicit ``from_remote_branch`` when given, else the worktree's
        ``GitTrackingBranch`` split on its first ``/``. A pull with neither a
        resolvable remote tip nor a tracking branch changes nothing.
        """
        if from_remote_branch:
            remote, branch = from_remote_branch
        else:
            tracking = self.git_tracking_branches.get(worktree)
            if not tracking or "/" not in tracking:
                return
            remote, branch = tracking.split("/", 1)
        tip = self.git_remote_commits.get(worktree, {}).get(remote, {}).get(branch)
        if tip:
            self.git_local_commits.setdefault(worktree, {})["HEAD"] = tip

    def set_git_config(
        self,
        key: str,
        value: str,
        multi_value: bool = False,
        repo: str | None = None,
        system: bool = False,
    ) -> None:
        """Record a simulated ``git config``: set or add a config item.

        ``git.config``'s mutation. It writes into ``git_configs`` (the
        ``GitConfig`` seam) under the scope the operation targets -- the repo
        path for a ``--local`` write, else the "system"/"global" sentinel for
        the two repo-less login-user scopes -- resolved exactly as
        ``GitConfig.process`` resolves it, so a repeat ``git.config`` reads the
        value back and is a no-op. A ``multi_value`` write appends the value to
        the key's list when absent (``git config --add``), leaving any existing
        entries; a single-valued write replaces the key's list with exactly
        ``[value]`` (``git config <key> <value>``).
        """
        scope = repo if repo is not None else ("system" if system else "global")
        config = self.git_configs.setdefault(scope, {})
        if multi_value:
            values = config.setdefault(key, [])
            if value not in values:
                values.append(value)
        else:
            config[key] = [value]

    def clone_repo(self, src: str, dest: str, branch: str | None) -> None:
        """Record a simulated ``git clone``: bring the repository to life.

        ``git.repo``'s create mutation. Every seam a later ``git.repo`` read
        consults must be updated together so a repeat clone short-circuits:
        ``directory_stats`` gets both the ``dest`` directory (so the top-level
        ``files.Directory`` shape stays coherent) and the ``<dest>/.git``
        directory that the operation's ``_DirectoryExists`` probe reads --
        with that present the next pass takes the reconcile/branch/pull
        branch instead of re-cloning. ``git_configs[dest]`` records the
        ``remote.origin.url`` value list ``GitConfig``'s entry carries for
        the freshly cloned repository (matching the baseline's own fixture shape,
        a one-element list), so the origin-reconcile check reads equal on the
        second pass. ``git_branches[dest]`` becomes the requested ``branch``,
        or ``"main"`` when none was named -- the modern ``init.defaultBranch``
        a real clone resolves HEAD to -- so the branch-match check reads
        equal too. The clone's commit SHAs are deliberately not seeded:
        without a real remote there is nothing sensible to record in
        ``git_local_commits``/``git_remote_commits``, and the baseline's own
        clone fixtures assert only the clone command, never a follow-up.
        """
        self.directory_stats[dest] = {"mode": 755}
        self.directory_stats[f"{dest}/.git"] = {"mode": 755}
        self.git_repos.add(dest)
        config = self.git_configs.setdefault(dest, {})
        config["remote.origin.url"] = [src]
        self.git_branches[dest] = branch or "main"

    def set_git_remote_url(self, dest: str, url: str) -> None:
        """Record a simulated ``git remote set-url origin``: re-point origin.

        ``git.repo``'s origin-reconcile mutation. Writes the fetched URL into
        ``git_configs[dest]["remote.origin.url"]`` -- the exact key
        ``GitConfig`` returns and the operation compares against ``src`` -- so
        a repeat ``git.repo`` with the same ``src`` sees the values equal and
        skips the reset. The push URL is not modelled (no migrated fact reads
        it), matching the baseline, whose reset command only rewrites the fetch.
        """
        config = self.git_configs.setdefault(dest, {})
        config["remote.origin.url"] = [url]

    def checkout_branch(self, dest: str, branch: str) -> None:
        """Record a simulated ``git checkout``: switch the repository's HEAD.

        ``git.repo``'s branch-switch (and tag-detach) mutation. Writes
        ``branch`` into ``git_branches[dest]`` -- the value ``GitBranch``
        reads back -- so a repeat ``git.repo`` for the same target sees the
        current branch already equal and skips the fetch/checkout pair. A tag
        checkout writes the tag name into the same seam: the baseline's
        ``is_tag`` branch does not model the resulting detached HEAD (the
        fact's ``git describe --all`` answer for a detached tree is out of
        scope here), and the operation only compares ``GitBranch`` against
        ``branch``, so keeping the two consistent is enough to short-circuit
        the second pass.
        """
        self.git_branches[dest] = branch

    def pull_repo(self, dest: str, branch: str | None) -> None:
        """Record a simulated ``git pull``: fast-forward the branch's local tip.

        ``git.repo``'s pull mutation. Advances ``git_local_commits[dest]`` at
        the ``branch`` key (the ``GitLocalCommit`` ref the operation compared)
        to ``git_remote_commits[dest]["origin"][branch]`` -- the
        ``GitRemoteBranchCommit`` tip the operation resolved against the same
        effective branch -- so a repeat ``git.repo`` reads local == remote
        and short-circuits through ``skip_pull``. A pull whose branch is
        ``None`` (an empty repository with nothing checked out) or whose
        remote tip is unseeded changes nothing: the real command would either
        refuse or fetch from an unreachable origin, but the operation only
        yields the pull when it decided the two differ, and the simulated
        seam has no remote to contact for a missing tip.
        """
        if not branch:
            return
        tip = self.git_remote_commits.get(dest, {}).get("origin", {}).get(branch)
        if tip:
            self.git_local_commits.setdefault(dest, {})[branch] = tip

    def _postgresql_tablespace_oid(self, name: str | None) -> int:
        """Resolve a tablespace name to the OID ``pg_database`` reports.

        The database operation compares OIDs (the fact's ``tablespace``
        column), so the create/alter mutators must store one too. None
        means the database default (``pg_default``, OID 1663 on a stock
        cluster); an unknown name falls back to 1663 as well -- the
        operation's own PostgresqlTablespaces check never yields a
        command naming a tablespace the seam does not list.
        """
        wanted = name or "pg_default"
        for space in self.postgres_tablespaces:
            if space["name"] == wanted:
                return space["oid"]
        return 1663

    def manage_postgresql_database(self, cmd: PostgresqlDatabaseCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP DATABASE.

        ``postgresql.database``'s mutation, maintaining the
        ``postgres_databases`` entry ``PostgresqlDatabases`` reads. A
        create seeds the full ``pg_database`` shape with the catalog
        defaults the clauses left unset imply (UTF8/C, connections
        allowed, unlimited), the tablespace NAME resolved to its OID
        through ``postgres_tablespaces``, and the owner's OID looked up
        in ``postgres_roles`` (10, the bootstrap superuser, when the
        role is unseeded); the xid placeholders mirror the baseline's own
        fact fixture. An alter applies only the non-None fields (the
        operation yields exactly the diffs), and a drop pops the entry.
        """
        if not cmd.present:
            self.postgres_databases.pop(cmd.database, None)
            return
        if cmd.alter:
            entry = self.postgres_databases.get(cmd.database)
            if entry is None:
                return
            if cmd.owner is not None:
                entry["owner"] = cmd.owner
                entry["dba"] = self.postgres_roles.get(cmd.owner, {}).get("oid", 10)
            if cmd.tablespace is not None:
                entry["tablespace"] = self._postgresql_tablespace_oid(cmd.tablespace)
            if cmd.connection_limit is not None:
                entry["connlimit"] = cmd.connection_limit
            if cmd.allow_connections is not None:
                entry["allowconn"] = cmd.allow_connections
            return
        owner = cmd.owner or "postgres"
        self.postgres_databases[cmd.database] = {
            "dba": self.postgres_roles.get(owner, {}).get("oid", 10),
            "encoding": cmd.encoding or "UTF8",
            "collate": cmd.lc_collate or "C",
            "ctype": cmd.lc_ctype or "C",
            "istemplate": False,
            "allowconn": True if cmd.allow_connections is None else cmd.allow_connections,
            "connlimit": -1 if cmd.connection_limit is None else cmd.connection_limit,
            "lastsysoid": 13052,
            "frozenxid": 548,
            "minmxid": 1,
            "tablespace": self._postgresql_tablespace_oid(cmd.tablespace),
            "acl": None,
            "owner": owner,
        }

    def dump_postgresql_database(self, cmd: PostgresqlDumpCommand) -> None:
        """Record a simulated ``pg_dump ... > dest``.

        ``postgresql.dump``'s mutation. The dump content itself is not
        modelled (the simulation holds no table data); a marker line
        naming the dumped database lands in ``remote_files`` so a test
        can observe the file exists -- and a later ``files.get`` of the
        path round-trips those bytes.
        """
        database = cmd.psql_database or "postgres"
        self.remote_files[cmd.dest] = f"-- simulated pg_dump of {database}\n".encode()

    def manage_postgresql_extension(self, cmd: PostgresqlExtensionCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP EXTENSION.

        ``postgresql.extension``'s mutation on the database-keyed
        ``postgres_extensions`` seam. A create records the requested
        schema (``public`` when unset) and version -- an unset version
        gets the ``""`` placeholder, since the simulation has no
        control files to resolve a default version from and the
        operation only compares a version the caller named. The
        alter forms move the schema / bump the version in place.
        """
        database = cmd.psql_database or "postgres"
        entries = self.postgres_extensions.setdefault(database, [])
        if not cmd.present:
            self.postgres_extensions[database] = [
                e for e in entries if e["name"] != cmd.extension
            ]
            return
        current = next((e for e in entries if e["name"] == cmd.extension), None)
        if cmd.alter_schema:
            if current is not None:
                current["schema"] = cmd.schema or ""
            return
        if cmd.alter_version:
            if current is not None:
                current["version"] = cmd.version or ""
            return
        entries.append(
            {
                "name": cmd.extension,
                "version": cmd.version or "",
                "schema": cmd.schema or "public",
            }
        )

    def manage_postgresql_index(self, cmd: PostgresqlIndexCommand) -> None:
        """Record a simulated CREATE/DROP INDEX.

        ``postgresql.index``'s mutation on the database-keyed
        ``postgres_indexes`` seam. A create splits the (possibly
        qualified) index name into schema (``public`` when bare) and
        name, records the table's unqualified part (the fact reports
        ``table_name`` bare) and the definition fields the operation
        drift-checks; the owner is the simulated ``postgres`` run-as
        user (no fact consumer compares it). A drop removes the entry
        the operation's own lookup matched: bare name, optionally
        pinned to the named schema.
        """
        database = cmd.psql_database or "postgres"
        entries = self.postgres_indexes.setdefault(database, [])
        schema, _, bare = cmd.index.rpartition(".")
        if not cmd.present:
            self.postgres_indexes[database] = [
                e
                for e in entries
                if not (e["name"] == bare and (not schema or e["schema"] == schema))
            ]
            return
        entries.append(
            {
                "schema": schema or "public",
                "name": bare,
                "table": cmd.table.rpartition(".")[2],
                "owner": "postgres",
                "tablespace": cmd.tablespace,
                "unique": cmd.unique,
                "method": cmd.method,
            }
        )

    def manage_postgresql_membership(self, cmd: PostgresqlMembershipCommand) -> None:
        """Record a simulated GRANT/REVOKE role membership.

        ``postgresql.membership``'s mutation on ``postgres_role_memberships``.
        A grant upserts the (role, member) pair with the requested
        admin option; ``admin_only`` withdraws just the option (the
        membership stays, the REVOKE ADMIN OPTION FOR direction); a
        plain revoke drops the entry.
        """
        current = next(
            (
                m
                for m in self.postgres_role_memberships
                if m["role"] == cmd.role and m["member"] == cmd.member
            ),
            None,
        )
        if cmd.present:
            if current is None:
                self.postgres_role_memberships.append(
                    {
                        "role": cmd.role,
                        "member": cmd.member,
                        "admin_option": cmd.admin_option,
                    }
                )
            else:
                current["admin_option"] = cmd.admin_option
            return
        if cmd.admin_only:
            if current is not None:
                current["admin_option"] = False
            return
        if current is not None:
            self.postgres_role_memberships.remove(current)

    def manage_postgresql_privileges(self, cmd: PostgresqlPrivilegeCommand) -> None:
        """Record a simulated GRANT/REVOKE of object privileges.

        ``postgresql.privilege``'s mutation on the database-keyed
        ``postgres_privileges`` seam. Targets resolve exactly as the
        operation resolves them (the shared ``_normalize_object``
        helper; an ALL IN SCHEMA grant addresses the objects the
        grantee already holds grants on -- the same set the operation
        derived its targets from, since the simulation has no object
        catalog to enumerate). Matching uses ``_PRIVILEGE_ALIASES`` so
        a "table" grant finds an existing "view" row. A grant upserts
        one entry per (object, privilege), ORing the grant option in
        (a plain re-grant never strips an existing option, as in real
        SQL); REVOKE GRANT OPTION FOR only clears ``grantable``; a
        plain revoke removes the entries.
        """
        database = cmd.psql_database or "postgres"
        entries = self.postgres_privileges.setdefault(database, [])
        aliases = _PRIVILEGE_ALIASES[cmd.object_type]
        if cmd.all_in_schema:
            targets = sorted(
                {
                    (e["schema"], e["name"])
                    for e in entries
                    if e["grantee"] == cmd.role
                    and e["object_type"] in aliases
                    and e["schema"] == cmd.all_in_schema
                }
            )
        else:
            targets = sorted(
                {_normalize_object(cmd.object_type, name) for name in cmd.objects}
            )
        for schema, name in targets:
            for privilege in cmd.privileges:
                current = next(
                    (
                        e
                        for e in entries
                        if e["grantee"] == cmd.role
                        and e["object_type"] in aliases
                        and e["schema"] == schema
                        and e["name"] == name
                        and e["privilege"] == privilege
                    ),
                    None,
                )
                if cmd.present:
                    if current is None:
                        entries.append(
                            {
                                "object_type": cmd.object_type,
                                "schema": schema,
                                "name": name,
                                "grantee": cmd.role,
                                "privilege": privilege,
                                "grantable": cmd.grant_option,
                            }
                        )
                    else:
                        current["grantable"] = current["grantable"] or cmd.grant_option
                elif cmd.revoke_grant_option:
                    if current is not None:
                        current["grantable"] = False
                elif current is not None:
                    entries.remove(current)

    def manage_postgresql_publication(self, cmd: PostgresqlPublicationCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP PUBLICATION.

        ``postgresql.publication``'s mutation on the database-keyed
        ``postgres_publications`` seam. A create records the table set
        (or the ALL TABLES flag) under the simulated ``postgres``
        run-as owner -- CREATE PUBLICATION has no OWNER clause, the
        operation yields a separate OWNER TO alter the arm below then
        applies. An alter replaces the table list when one is named
        and re-owns when an owner is named; a drop removes the entry.
        """
        database = cmd.psql_database or "postgres"
        entries = self.postgres_publications.setdefault(database, [])
        if not cmd.present:
            self.postgres_publications[database] = [
                p for p in entries if p["name"] != cmd.publication
            ]
            return
        current = next((p for p in entries if p["name"] == cmd.publication), None)
        if cmd.alter:
            if current is not None:
                if cmd.tables:
                    current["tables"] = list(cmd.tables)
                if cmd.owner is not None:
                    current["owner"] = cmd.owner
            return
        entries.append(
            {
                "name": cmd.publication,
                "owner": "postgres",
                "all_tables": cmd.all_tables,
                "tables": list(cmd.tables),
            }
        )

    def manage_postgresql_role(self, cmd: PostgresqlRoleCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP ROLE.

        ``postgresql.role``'s mutation on ``postgres_roles``. A create
        seeds the full ``pg_roles`` shape from the descriptor's flags
        (the operation renders every flag explicitly, so None only
        appears when a test builds the descriptor by hand -- the
        catalog defaults apply then), no password (the namespace
        credentials decision), and a synthetic OID one past the
        highest seeded one. An alter applies only the non-None
        tri-state fields onto the fact's catalog keys (canlogin,
        super, connlimit, ... -- the same mapping the operation's
        drift check uses); a drop pops the entry.
        """
        if not cmd.present:
            self.postgres_roles.pop(cmd.role, None)
            return
        if cmd.alter:
            entry = self.postgres_roles.get(cmd.role)
            if entry is None:
                return
            for option, key in (
                ("login", "canlogin"),
                ("superuser", "super"),
                ("inherit", "inherit"),
                ("createdb", "createdb"),
                ("createrole", "createrole"),
                ("replication", "replication"),
            ):
                value = getattr(cmd, option)
                if value is not None:
                    entry[key] = value
            if cmd.connection_limit is not None:
                entry["connlimit"] = cmd.connection_limit
            return
        self.postgres_roles[cmd.role] = {
            "super": False if cmd.superuser is None else cmd.superuser,
            "inherit": True if cmd.inherit is None else cmd.inherit,
            "createrole": False if cmd.createrole is None else cmd.createrole,
            "createdb": False if cmd.createdb is None else cmd.createdb,
            "canlogin": True if cmd.login is None else cmd.login,
            "replication": False if cmd.replication is None else cmd.replication,
            "connlimit": -1 if cmd.connection_limit is None else cmd.connection_limit,
            "password": None,
            "validuntil": None,
            "bypassrls": False,
            "config": None,
            "oid": max(
                (e["oid"] for e in self.postgres_roles.values()), default=16383
            )
            + 1,
        }

    def manage_postgresql_schema(self, cmd: PostgresqlSchemaCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP SCHEMA.

        ``postgresql.schema``'s mutation on the database-keyed
        ``postgres_schemas`` seam. A create records the owner (the
        simulated ``postgres`` run-as user when no AUTHORIZATION was
        named); an alter re-owns; a drop removes the entry (CASCADE
        needs no extra modelling -- the simulation tracks no objects
        living inside the schema beyond what other seams hold).
        """
        database = cmd.psql_database or "postgres"
        entries = self.postgres_schemas.setdefault(database, [])
        if not cmd.present:
            self.postgres_schemas[database] = [
                s for s in entries if s["name"] != cmd.schema
            ]
            return
        current = next((s for s in entries if s["name"] == cmd.schema), None)
        if cmd.alter:
            if current is not None:
                current["owner"] = cmd.owner or ""
            return
        entries.append({"name": cmd.schema, "owner": cmd.owner or "postgres"})

    def set_postgresql_setting(self, cmd: PostgresqlSettingCommand) -> None:
        """Record a simulated ALTER SYSTEM SET/RESET.

        ``postgresql.setting``'s mutation on ``postgres_settings``. A
        SET writes the value text and moves the source to
        "configuration file" (ALTER SYSTEM writes postgresql.auto.conf);
        a RESET moves the source back to "default" -- the boot value
        itself is not modelled (the operation's reset branch compares
        only the source, and its set branch only the value text). The
        pending_restart flag is left as seeded: whether a GUC needs a
        restart is server knowledge the simulation does not hold. An
        absent parameter is tolerated -- the operation's unknown-GUC
        check never yields for one.
        """
        current = next(
            (s for s in self.postgres_settings if s["name"] == cmd.name), None
        )
        if current is None:
            return
        if cmd.present:
            current["setting"] = cmd.value or ""
            current["source"] = "configuration file"
        else:
            current["source"] = "default"

    def manage_postgresql_subscription(self, cmd: PostgresqlSubscriptionCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP SUBSCRIPTION.

        ``postgresql.subscription``'s mutation on the database-keyed
        ``postgres_subscriptions`` seam. A create records the
        publication list and enabled flag under the simulated
        ``postgres`` run-as owner; the connection string is never
        stored (the fact deliberately hides conninfo). An alter
        replaces the publications when named and flips enabled when
        set; a drop removes the entry.
        """
        database = cmd.psql_database or "postgres"
        entries = self.postgres_subscriptions.setdefault(database, [])
        if not cmd.present:
            self.postgres_subscriptions[database] = [
                s for s in entries if s["name"] != cmd.subscription
            ]
            return
        current = next(
            (s for s in entries if s["name"] == cmd.subscription), None
        )
        if cmd.alter:
            if current is not None:
                if cmd.publications:
                    current["publications"] = list(cmd.publications)
                if cmd.enabled is not None:
                    current["enabled"] = cmd.enabled
            return
        entries.append(
            {
                "name": cmd.subscription,
                "owner": "postgres",
                "enabled": True if cmd.enabled is None else cmd.enabled,
                "publications": list(cmd.publications),
            }
        )

    def manage_postgresql_tablespace(self, cmd: PostgresqlTablespaceCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP TABLESPACE.

        ``postgresql.tablespace``'s mutation on ``postgres_tablespaces``.
        A create appends the entry with a synthetic OID one past the
        highest seeded one (1663/1664 are the built-ins on a stock
        cluster) so a later ``postgresql.database`` tablespace
        resolution finds it; an alter re-owns; a drop removes the
        entry (the real command fails while objects still live there,
        but the simulation models no tablespace occupancy).
        """
        if not cmd.present:
            self.postgres_tablespaces = [
                t for t in self.postgres_tablespaces if t["name"] != cmd.tablespace
            ]
            return
        current = next(
            (t for t in self.postgres_tablespaces if t["name"] == cmd.tablespace),
            None,
        )
        if cmd.alter:
            if current is not None:
                current["owner"] = cmd.owner or ""
            return
        self.postgres_tablespaces.append(
            {
                "oid": max(
                    (t["oid"] for t in self.postgres_tablespaces), default=16384
                )
                + 1,
                "name": cmd.tablespace,
                "owner": cmd.owner or "postgres",
                "location": cmd.location,
            }
        )

    @staticmethod
    def _mysql_ident(name: str) -> str:
        """Backtick-quote an identifier the way SHOW GRANTS renders it."""
        return "`" + name.replace("`", "``") + "`"

    def manage_mysql_database(self, cmd: MysqlDatabaseCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP DATABASE.

        ``mysql.database``'s mutation, maintaining the ``mysql_databases``
        entry ``MysqlDatabases`` reads. A create seeds the server's
        utf8mb4 defaults for the clauses left unset (the charset ->
        default-collation mapping of a non-utf8mb4 charset is not
        modelled); an alter applies only the non-None fields (the
        operation yields exactly the diffs). A drop also clears what
        DROP DATABASE destroys server-side: the database's tables,
        indexes and column seams, and every grant target scoped to it.
        """
        if not cmd.present:
            self.mysql_databases.pop(cmd.database, None)
            self.mysql_tables.pop(cmd.database, None)
            self.mysql_indexes.pop(cmd.database, None)
            prefix = f"{cmd.database}."
            for key in [k for k in self.mysql_columns if k.startswith(prefix)]:
                del self.mysql_columns[key]
            grant_prefix = f"{self._mysql_ident(cmd.database)}."
            for grants in self.mysql_user_grants.values():
                for target in [t for t in grants if t.startswith(grant_prefix)]:
                    del grants[target]
            return
        if cmd.alter:
            entry = self.mysql_databases.get(cmd.database)
            if entry is None:
                return
            if cmd.charset is not None:
                entry["character_set"] = cmd.charset
            if cmd.collate is not None:
                entry["collation_name"] = cmd.collate
            return
        self.mysql_databases[cmd.database] = {
            "character_set": cmd.charset or "utf8mb4",
            "collation_name": cmd.collate or "utf8mb4_0900_ai_ci",
        }

    def dump_mysql_database(self, cmd: MysqlDumpCommand) -> None:
        """Record a simulated ``mysqldump ... > dest``.

        ``mysql.dump``'s mutation. The dump content itself is not
        modelled (the simulation holds no table data); a marker line
        naming the dumped database lands in ``remote_files`` so a test
        can observe the file exists -- and a later ``files.get`` of the
        path round-trips those bytes.
        """
        database = cmd.mysql_database or "an unspecified database"
        self.remote_files[cmd.dest] = f"-- simulated mysqldump of {database}\n".encode()

    def manage_mysql_index(self, cmd: MysqlIndexCommand) -> None:
        """Record a simulated CREATE/DROP INDEX.

        ``mysql.index``'s mutation on the database-keyed ``mysql_indexes``
        seam ``MysqlIndexes`` reads. A create replaces any same-named
        entry on the table (the operation never yields one on drift, so
        this only matters for a directly sent descriptor); a drop
        filters the entry out, tolerating an absent index.
        """
        entries = self.mysql_indexes.setdefault(cmd.database, [])
        entries[:] = [
            e for e in entries if not (e["table"] == cmd.table and e["name"] == cmd.index)
        ]
        if cmd.present:
            entries.append(
                {
                    "name": cmd.index,
                    "table": cmd.table,
                    "columns": list(cmd.columns),
                    "unique": cmd.unique,
                    "index_type": cmd.index_type,
                }
            )

    def kill_mysql_connection(self, cmd: MysqlKillCommand) -> None:
        """Record a simulated KILL / KILL QUERY.

        ``mysql.kill``'s mutation on ``mysql_connections``. A full kill
        drops the thread; KILL QUERY only cancels the running statement,
        so the connection remains, idling (Sleep, no state, zero time).
        An absent id is tolerated -- the real command errors, but the
        state outcome is the same.
        """
        for entry in self.mysql_connections:
            if entry["id"] == cmd.process_id:
                if cmd.query_only:
                    entry["command"] = "Sleep"
                    entry["state"] = None
                    entry["time"] = 0
                else:
                    self.mysql_connections.remove(entry)
                return

    def manage_mysql_plugin(self, cmd: MysqlPluginCommand) -> None:
        """Record a simulated INSTALL/UNINSTALL PLUGIN.

        ``mysql.plugin``'s mutation on ``mysql_plugins``. An install
        records an ACTIVE plugin loaded from the named library; the
        plugin type is server knowledge the INSTALL statement does not
        carry, so it records empty (the operation compares only the
        library). An uninstall drops the named plugin.
        """
        self.mysql_plugins[:] = [p for p in self.mysql_plugins if p["name"] != cmd.plugin]
        if cmd.present:
            self.mysql_plugins.append(
                {
                    "name": cmd.plugin,
                    "status": "ACTIVE",
                    "type": "",
                    "library": cmd.soname,
                    "license": "GPL",
                }
            )

    def manage_mysql_privileges(self, cmd: MysqlPrivilegesCommand) -> None:
        """Record a simulated GRANT/REVOKE ... ON ... TO/FROM.

        ``mysql.privileges``' mutation on ``mysql_user_grants`` (also the
        privileges bundle of ``mysql.database``/``mysql.user``). The
        descriptor's database/table arrive already rendered (backticked
        or ``*``), so the target key matches what SHOW GRANTS -- and the
        planner's fact lookup -- uses. REVOKE ALL PRIVILEGES clears every
        privilege except GRANT OPTION and role memberships; granting
        real privileges overrides USAGE, exactly as the server does.
        """
        grants = self.mysql_user_grants.setdefault(f"{cmd.user}@{cmd.user_hostname}", {})
        target = f"{cmd.database}.{cmd.table}"
        current = set(grants.get(target, []))
        if cmd.action == "GRANT":
            current.update(cmd.privileges)
            if cmd.with_statement == "GRANT OPTION":
                current.add("GRANT OPTION")
            if current - {"USAGE", "ROLE", "GRANT OPTION"}:
                current.discard("USAGE")
        else:
            if "ALL PRIVILEGES" in cmd.privileges:
                current &= {"GRANT OPTION", "ROLE"}
            current.difference_update(cmd.privileges)
        if current:
            grants[target] = sorted(current)
        else:
            grants.pop(target, None)

    def manage_mysql_replica(self, cmd: MysqlReplicaCommand) -> None:
        """Record a simulated START/STOP/RESET REPLICA.

        ``mysql.replica``'s mutation on ``mysql_replication``. A reset
        (RESET REPLICA ALL) forgets every channel's configuration; a
        start/stop flips both thread states on the named channel (the
        default channel when none is named). Starting clears the last
        error and the lag; stopping NULLs the lag, as SHOW REPLICA
        STATUS reports it with the applier stopped.
        """
        if cmd.reset:
            if cmd.channel is None:
                self.mysql_replication.clear()
            else:
                self.mysql_replication[:] = [
                    e for e in self.mysql_replication if e["channel"] != cmd.channel
                ]
            return
        for entry in self.mysql_replication:
            if entry["channel"] == (cmd.channel or ""):
                state = "Yes" if cmd.running else "No"
                entry["io_running"] = state
                entry["sql_running"] = state
                if cmd.running:
                    entry["last_error"] = ""
                    entry["seconds_behind_source"] = 0
                else:
                    entry["seconds_behind_source"] = None

    def manage_mysql_role(self, cmd: MysqlRoleCommand) -> None:
        """Record a simulated CREATE/DROP ROLE or GRANT/REVOKE role.

        ``mysql.role``'s mutation. A create seeds the role account
        (``is_role``, locked and password-expired, as CREATE ROLE leaves
        it) in ``mysql_users``; a drop pops it and revokes every
        membership server-side. A membership grant/revoke edits the
        member's ``mysql_user_grants`` under the backticked role@host
        target with the single "ROLE" privilege -- the same key the
        operation's idempotence check reads.
        """
        role_key = f"{cmd.role}@{cmd.role_hostname}"
        grant_key = f"{self._mysql_ident(cmd.role)}@{self._mysql_ident(cmd.role_hostname)}"
        if cmd.member is not None:
            grants = self.mysql_user_grants.setdefault(
                f"{cmd.member}@{cmd.member_hostname}", {}
            )
            if cmd.member_present:
                grants[grant_key] = ["ROLE"]
            else:
                grants.pop(grant_key, None)
            return
        if cmd.present:
            self.mysql_users[role_key] = {
                "privileges": [],
                "password_expired": True,
                "is_role": True,
                "account_locked": True,
            }
            return
        self.mysql_users.pop(role_key, None)
        for grants in self.mysql_user_grants.values():
            grants.pop(grant_key, None)

    def manage_mysql_table(self, cmd: MysqlTableCommand) -> None:
        """Record a simulated CREATE/DROP TABLE.

        ``mysql.table``'s mutation on the database-keyed ``mysql_tables``
        seam ``MysqlTables`` reads. A create seeds an empty table with
        the InnoDB/utf8mb4 defaults for the clauses left unset; the
        column definitions are free SQL text the simulation does not
        parse, so ``mysql_columns`` is NOT seeded (a test wanting column
        visibility seeds that seam directly). A drop also clears the
        table's indexes and column entries, as DROP TABLE does.
        """
        entries = self.mysql_tables.setdefault(cmd.database, [])
        entries[:] = [e for e in entries if e["name"] != cmd.table]
        if cmd.present:
            entries.append(
                {
                    "name": cmd.table,
                    "engine": cmd.engine or "InnoDB",
                    "rows": 0,
                    "data_length": 0,
                    "index_length": 0,
                    "collation": cmd.collate or "utf8mb4_0900_ai_ci",
                    "comment": cmd.comment or "",
                }
            )
            return
        indexes = self.mysql_indexes.get(cmd.database)
        if indexes is not None:
            indexes[:] = [e for e in indexes if e["table"] != cmd.table]
        self.mysql_columns.pop(f"{cmd.database}.{cmd.table}", None)

    def manage_mysql_user(self, cmd: MysqlUserCommand) -> None:
        """Record a simulated CREATE/ALTER/DROP USER.

        ``mysql.user``'s mutation on ``mysql_users``. A create seeds the
        account with the caching_sha2_password default plugin (no
        password is ever modelled -- the credentials decision), the
        REQUIRE level mapped to its ssl_type column (SSL -> ANY,
        X509 -> X509, or SPECIFIED with cipher/issuer/subject clauses)
        and the resource limits in their mysql.user columns. An alter
        applies only the non-None fields (the operation yields exactly
        the diffs). A drop pops the account and its grants, as DROP
        USER does.
        """
        key = f"{cmd.user}@{cmd.user_hostname}"
        if not cmd.present:
            self.mysql_users.pop(key, None)
            self.mysql_user_grants.pop(key, None)
            return

        def ssl_type() -> str | None:
            if cmd.require == "SSL":
                return "ANY"
            if cmd.require == "X509":
                if cmd.require_cipher or cmd.require_issuer or cmd.require_subject:
                    return "SPECIFIED"
                return "X509"
            return None

        if cmd.alter:
            entry = self.mysql_users.get(key)
            if entry is None:
                return
            if cmd.auth_plugin is not None:
                entry["plugin"] = cmd.auth_plugin
            if cmd.require is not None:
                entry["ssl_type"] = ssl_type()
            if cmd.require_cipher is not None:
                entry["ssl_cipher"] = cmd.require_cipher
            if cmd.require_issuer is not None:
                entry["x509_issuer"] = cmd.require_issuer
            if cmd.require_subject is not None:
                entry["x509_subject"] = cmd.require_subject
            if cmd.max_connections is not None:
                entry["max_user_connections"] = cmd.max_connections
            if cmd.max_queries_per_hour is not None:
                entry["max_questions"] = cmd.max_queries_per_hour
            if cmd.max_updates_per_hour is not None:
                entry["max_updates"] = cmd.max_updates_per_hour
            if cmd.max_connections_per_hour is not None:
                entry["max_connections"] = cmd.max_connections_per_hour
            return
        self.mysql_users[key] = {
            "privileges": [],
            "plugin": cmd.auth_plugin or "caching_sha2_password",
            "authentication_string": None,
            "ssl_type": ssl_type(),
            "ssl_cipher": cmd.require_cipher,
            "x509_issuer": cmd.require_issuer,
            "x509_subject": cmd.require_subject,
            "max_user_connections": cmd.max_connections or 0,
            "max_questions": cmd.max_queries_per_hour or 0,
            "max_updates": cmd.max_updates_per_hour or 0,
            "max_connections": cmd.max_connections_per_hour or 0,
            "password_expired": False,
            "is_role": False,
            "account_locked": False,
        }

    def set_mysql_variable(self, cmd: MysqlVariableCommand) -> None:
        """Record a simulated SET GLOBAL/PERSIST or RESET PERSIST.

        ``mysql.variable``'s mutation on ``mysql_variables``. A SET
        writes the value text (both scopes change the runtime value);
        the persisted-override set is not modelled, so RESET PERSIST
        leaves the runtime value untouched -- it keeps until restart
        either way, and the operation's reset branch is stateless.
        """
        if cmd.present:
            self.mysql_variables[cmd.name] = cmd.value or ""

    def set_selinux_boolean(self, boolean: str, value: str) -> None:
        """Record a simulated ``setsebool``: store the on/off bool.

        The ``SEBoolean``/``Booleans`` facts read ``selinux_booleans``, so
        the next step's fresh fact cache sees the new value and a repeat
        set is a no-op. ``persistent`` carries no simulated state: the
        simulation models no separate policy store, the runtime value is
        the only one the facts report.
        """
        self.selinux_booleans[boolean] = value == "on"

    def chcon_path(self, cmd: ChconCommand) -> None:
        """Record a simulated ``chcon``: edit the named context components.

        Only the components the descriptor carries are changed; the path
        gains a stock ``system_u:object_r:default_t:s0`` context first when
        unlabelled (the real chcon on an unlabelled filesystem would fail;
        the seam stays safe). ``FileContext`` reads the same store, so the
        relabel is observable on the next step.
        """
        context = self.selinux_file_contexts.setdefault(
            cmd.path,
            SeContext(user="system_u", role="object_r", type="default_t", level="s0"),
        )
        for component, value in (
            ("user", cmd.se_user),
            ("role", cmd.se_role),
            ("type", cmd.se_type),
            ("level", cmd.se_level),
        ):
            if value is not None:
                context[component] = value

    def manage_selinux_fcontext(self, cmd: SemanageFcontextCommand) -> None:
        """Record a simulated ``semanage fcontext -a/-m/-d``.

        Add seeds the stock ``system_u:object_r:<type>:s0`` context (the
        components semanage defaults when only ``-t`` is given), modify
        changes the type only, delete drops the rule. The
        ``FileContextMapping`` fact reads the same store; existing file
        labels are untouched, exactly like the real database-only write.
        """
        if not cmd.present:
            self.selinux_fcontext_mappings.pop(cmd.target, None)
        elif cmd.modify:
            if cmd.target in self.selinux_fcontext_mappings:
                self.selinux_fcontext_mappings[cmd.target]["type"] = cmd.se_type
        else:
            self.selinux_fcontext_mappings[cmd.target] = SeContext(
                user="system_u", role="object_r", type=cmd.se_type, level="s0"
            )

    def manage_selinux_port(self, cmd: SemanagePortCommand) -> None:
        """Record a simulated ``semanage port -a/-m/-d``.

        Add and modify both store the type at the protocol/port pair (the
        simulation keeps no add-vs-modify distinction in the label map),
        delete drops it and prunes a protocol left empty, so ``SEPorts``
        never reports an empty protocol dict.
        """
        if not cmd.present:
            ports = self.selinux_ports.get(cmd.protocol)
            if ports is not None:
                ports.pop(cmd.port, None)
                if not ports:
                    del self.selinux_ports[cmd.protocol]
        else:
            self.selinux_ports.setdefault(cmd.protocol, {})[cmd.port] = cmd.se_type

    def set_selinux_mode(self, mode: str) -> None:
        """Record a simulated ``setenforce``: switch the runtime mode only.

        The ``Mode`` fact reads ``selinux_mode``; only its ``mode`` key
        changes, mirroring setenforce's runtime-only effect -- the
        config-file mode and policy survive untouched.
        """
        self.selinux_mode["mode"] = mode

    def install_selinux_config_file(self, spool: str, dest: str) -> None:
        """Record a simulated ``selinux.config`` install of the spooled text.

        ``selinux.config`` first uploads the transformed /etc/selinux/config
        text to a ``/tmp`` spool path (the paired
        ``SelinuxConfigUploadCommand`` runs through ``put_file``, storing the
        bytes in ``remote_files``), then installs it as the ``dest`` file and
        removes the spool. This mirrors the install-and-remove: the spool
        bytes are popped out of ``remote_files`` and stored at ``dest``, and
        the path is added to ``existing_files`` plus a minimal ``file_stats``
        row (root-owned, mode 644 -- the real command's ``chmod 0644``), so
        the content-read (``SelinuxConfigFile``), existence and metadata
        facts all see the installed file consistently. ``Mode``'s
        ``config_mode`` is refreshed from the installed text's last active
        ``SELINUX=`` assignment, because sestatus reads the config file live
        -- except on a disabled subsystem, where sestatus reports no config
        mode at all. The runtime ``mode`` and loaded ``policy`` survive
        untouched: both change only at the next reboot. An absent spool path
        installs nothing (the ``install_openrc_config_file`` tolerance).
        """
        data = self.remote_files.pop(spool, None)
        if data is None:
            return
        self.remote_files[dest] = data
        self.existing_files.add(dest)
        self.file_stats[dest] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(data),
        }
        if self.selinux_mode["mode"] != "disabled":
            for line in data.decode("utf-8").splitlines():
                match = re.match(r"^[ \t]*SELINUX[ \t]*=[ \t]*(\S+)", line)
                if match:
                    self.selinux_mode["config_mode"] = match.group(1).lower()

    def manage_selinux_login(self, cmd: SemanageLoginCommand) -> None:
        """Record a simulated ``semanage login -a/-m/-d``.

        Add and modify store the seuser/range with the universal ``*``
        service (the simulation models no service-scoped mappings), delete
        drops the login. ``SELogin`` reads the same store.
        """
        if not cmd.present:
            self.selinux_logins.pop(cmd.login, None)
        else:
            self.selinux_logins[cmd.login] = SeLogin(
                seuser=cmd.seuser, mls_range=cmd.mls_range, service="*"
            )

    def install_selinux_module(self, source: str, priority: int) -> None:
        """Record a simulated ``semodule -X <priority> -i <source>``.

        The module name is the source basename minus its ``.pp``/``.cil``
        suffix (the name semodule derives from the package), the format is
        that suffix, and the instance lands enabled. An instance already
        stored at the same name/priority is replaced (a real reinstall
        upgrades it); ``Modules`` reads the same list.
        """
        base = PurePosixPath(source).name
        name, _, suffix = base.rpartition(".")
        fmt = suffix if suffix in ("pp", "cil") else "pp"
        if not name:
            name, fmt = base, "pp"
        self.selinux_modules = [
            entry
            for entry in self.selinux_modules
            if not (entry["name"] == name and entry["priority"] == priority)
        ]
        self.selinux_modules.append(
            Module(name=name, priority=priority, format=fmt, enabled=True)
        )

    def remove_selinux_module(self, name: str, priority: int) -> None:
        """Record a simulated ``semodule -X <priority> -r <name>``.

        Drops the priority's instance of the module; other priorities of
        the same name survive, exactly like the real priority-scoped
        removal. ``Modules`` reads the same list.
        """
        self.selinux_modules = [
            entry
            for entry in self.selinux_modules
            if not (entry["name"] == name and entry["priority"] == priority)
        ]

    def enable_selinux_module(self, name: str, present: bool) -> None:
        """Record a simulated ``semodule -e/-d <name>``: flip every instance.

        semodule's enable/disable acts on the name, so every stored
        priority of it flips together; ``Modules`` reads the same list.
        """
        for entry in self.selinux_modules:
            if entry["name"] == name:
                entry["enabled"] = present

    def manage_selinux_permissive(self, cmd: SemanagePermissiveCommand) -> None:
        """Record a simulated ``semanage permissive -a/-d``.

        Add appends the domain (keeping listing order, no duplicates),
        delete drops it; ``PermissiveDomains`` reads the same list.
        """
        if cmd.present:
            if cmd.domain not in self.selinux_permissive_domains:
                self.selinux_permissive_domains.append(cmd.domain)
        elif cmd.domain in self.selinux_permissive_domains:
            self.selinux_permissive_domains.remove(cmd.domain)

    def relabel_path(self, cmd: RestoreconCommand) -> None:
        """Record a simulated ``restorecon``: reapply the fcontext database.

        Every labelled path the command covers (the path itself, plus the
        paths under it when ``recurse``) is matched against the stored
        fcontext rules -- a rule's target expression is a regex, matched
        with ``re.fullmatch`` like semanage's own matching -- and the first
        matching rule's context replaces the path's label. ``force`` carries
        no extra simulated effect: the simulation keeps no user-customized
        context parts to protect. An unmatched path keeps its label, like
        the real restorecon falling back to a policy default the simulation
        does not model.
        """
        for path in list(self.selinux_file_contexts):
            covered = path == cmd.path or (
                cmd.recurse and path.startswith(cmd.path.rstrip("/") + "/")
            )
            if not covered:
                continue
            for target, context in self.selinux_fcontext_mappings.items():
                if re.fullmatch(target, path):
                    self.selinux_file_contexts[path] = dict(context)  # type: ignore[assignment]
                    break

    def _iptables_seams(
        self, version: int
    ) -> tuple[
        dict[str, dict[str, str]],
        dict[str, list[IptablesRule]],
        dict[str, list[IptablesOrderedRule]],
        dict[str, bool],
    ]:
        """The four rule-state stores for one address family.

        Every iptables mutator serves both families through this selector:
        version 4 reads the ``iptables_*`` fields, version 6 the
        ``ip6tables_*`` ones, so the two rule sets never mix.
        """
        prefix = "iptables" if version == 4 else "ip6tables"
        return (
            getattr(self, f"{prefix}_chains"),
            getattr(self, f"{prefix}_rules"),
            getattr(self, f"{prefix}_ordered_rules"),
            getattr(self, f"{prefix}_persistence"),
        )

    def _iptables_definition(self, line: str, version: int) -> IptablesRule:
        """Parse one rendered rule line into the iptables-save shape.

        Reuses the module's own parser so a stored definition compares
        equal to what ``iptables.rule`` builds, then mirrors the two
        normalizations the real ``iptables-save`` output applies: a bare
        source/destination gains its host prefix (/32 or /128), and a
        protocol implies its ``-m <protocol>`` extras pair (iptables
        auto-loads the matching extension, which the save file prints).
        """
        definition = _parse_iptables_rule(line)
        for key in ("source", "not_source", "destination", "not_destination"):
            if key in definition:
                definition[key] = _normalize_host(definition[key], version)  # type: ignore[literal-required]
        if "protocol" in definition:
            extras = definition.get("extras", [])
            if ["-m", definition["protocol"]] not in extras:
                extras.append(["-m", definition["protocol"]])
                extras.sort()
                definition["extras"] = extras
        return definition

    def _rebuild_iptables_ordered(self, table: str, version: int) -> None:
        """Reproject a table's numbered rows from its rule definitions.

        The ordered-rules seam is a derived view: every rule mutation
        rebuilds it with per-chain 1-based renumbering and zero counters
        (the simulation models no traffic). Negated matches and the
        free-form ``options`` detail text are not projected -- a test
        seeding rich rows for fact checks should not mutate rules after.
        """
        _, rules, ordered, _ = self._iptables_seams(version)
        any_host = "0.0.0.0/0" if version == 4 else "::/0"
        rows: list[IptablesOrderedRule] = []
        numbers: dict[str, int] = {}
        for rule in rules.get(table, []):
            chain = rule.get("chain", "")
            numbers[chain] = numbers.get(chain, 0) + 1
            rows.append(
                {
                    "chain": chain,
                    "number": numbers[chain],
                    "packets": 0,
                    "bytes": 0,
                    "target": rule.get("jump", "-"),
                    "protocol": rule.get("protocol", "all"),
                    "opt": "--",
                    "in_interface": rule.get("in_interface", "*"),
                    "out_interface": rule.get("out_interface", "*"),
                    "source": rule.get("source", any_host),
                    "destination": rule.get("destination", any_host),
                    "options": "",
                }
            )
        ordered[table] = rows

    def _mark_iptables_drift(self, version: int) -> None:
        """Flip every stored persistence path of one family to drifted.

        Any rule-set change invalidates every snapshot the simulation
        tracks (the live rules no longer match any previously synced
        file). Counter zeroing deliberately does not call this -- a save
        without -c omits counters.
        """
        _, _, _, persistence = self._iptables_seams(version)
        for path in persistence:
            persistence[path] = False

    def manage_iptables_chain(self, cmd: IptablesChainCommand) -> None:
        """Record a simulated ``iptables -N/-X``: create or delete a chain.

        A created chain carries policy ``-`` (a user chain has none);
        deleting a chain also drops its rules and rebuilds the numbered
        rows, like the kernel refusing to leave orphaned rules behind.
        """
        chains, rules, _, _ = self._iptables_seams(cmd.version)
        if cmd.present:
            chains.setdefault(cmd.table, {}).setdefault(cmd.chain, "-")
        else:
            chains.get(cmd.table, {}).pop(cmd.chain, None)
            rules[cmd.table] = [
                r for r in rules.get(cmd.table, []) if r.get("chain") != cmd.chain
            ]
            self._rebuild_iptables_ordered(cmd.table, cmd.version)
        self._mark_iptables_drift(cmd.version)

    def set_iptables_policy(self, cmd: IptablesPolicyCommand) -> None:
        """Record a simulated ``iptables -P``: set one chain's policy.

        The chain is created in the store if absent (the real command
        would fail on a missing chain; the simulation stays tolerant so
        an unprivileged empty fact read cannot break the apply path).
        """
        chains, _, _, _ = self._iptables_seams(cmd.version)
        chains.setdefault(cmd.table, {})[cmd.chain] = cmd.policy
        self._mark_iptables_drift(cmd.version)

    def apply_iptables_rule(self, cmd: IptablesRuleCommand) -> None:
        """Record a simulated ``iptables -A/-I/-D``: one rule change.

        The spec is re-rendered into a save line and parsed back through
        the module's own parser, so the stored definition compares equal
        to the operation's comparison form. An insert addresses the
        1-based position within its chain (a position past the end
        appends -- tolerant where the real command errors); a delete
        removes the first equal definition and tolerates an absent one.
        """
        _, rules, _, _ = self._iptables_seams(cmd.version)
        table_rules = rules.setdefault(cmd.table, [])
        definition = self._iptables_definition(
            f"-A {cmd.chain} " + " ".join(cmd.spec), cmd.version
        )
        if cmd.action == "-D":
            if definition in table_rules:
                table_rules.remove(definition)
        elif cmd.action == "-I":
            position = cmd.position if cmd.position is not None else 1
            indices = [
                i for i, r in enumerate(table_rules) if r.get("chain") == cmd.chain
            ]
            offset = min(position - 1, len(indices))
            index = indices[offset] if offset < len(indices) else len(table_rules)
            table_rules.insert(index, definition)
        else:  # -A
            table_rules.append(definition)
        self._rebuild_iptables_ordered(cmd.table, cmd.version)
        self._mark_iptables_drift(cmd.version)

    def flush_iptables_rules(self, cmd: IptablesFlushCommand) -> None:
        """Record a simulated ``iptables -F``: drop rules, keep chains.

        A chain scopes the flush to its rules; None flushes the whole
        table. Chain entries and policies survive, exactly like the real
        command (only -X/-P touch those).
        """
        _, rules, _, _ = self._iptables_seams(cmd.version)
        rules[cmd.table] = [
            r
            for r in rules.get(cmd.table, [])
            if cmd.chain is not None and r.get("chain") != cmd.chain
        ]
        self._rebuild_iptables_ordered(cmd.table, cmd.version)
        self._mark_iptables_drift(cmd.version)

    def zero_iptables_counters(self, cmd: IptablesZeroCommand) -> None:
        """Record a simulated ``iptables -Z``: zero counters in place.

        Only the seeded numbered rows' counters change; the rule set and
        the persistence flags are untouched (a save without -c omits
        counters, so a synced snapshot stays synced across a zero).
        """
        _, _, ordered, _ = self._iptables_seams(cmd.version)
        for row in ordered.get(cmd.table, []):
            if cmd.chain is None or row["chain"] == cmd.chain:
                row["packets"] = 0
                row["bytes"] = 0

    def save_iptables_rules(self, cmd: IptablesSaveCommand) -> None:
        """Record a simulated ``iptables-save > path``: mark the path synced.

        The simulation models persistence as the sync flag only -- no
        snapshot bytes are written to ``remote_files`` (rendering a full
        save file from definitions would need a spec renderer the facts
        never read back).
        """
        _, _, _, persistence = self._iptables_seams(cmd.version)
        persistence[cmd.path] = True

    def restore_iptables_rules(self, cmd: IptablesRestoreCommand) -> None:
        """Record a simulated ``iptables-restore < path``: load a snapshot.

        When ``remote_files`` holds an iptables-save format file at the
        path (a ``files.put`` upload, the atomic bulk-apply recipe), its
        ``*table``/``:chain``/``-A`` lines load into the rule stores --
        replacing each named table, or merging with ``noflush`` -- and
        the numbered rows rebuild. Without stored content only the sync
        flag is set (the simulation cannot diff what it cannot read).
        """
        chains, rules, _, persistence = self._iptables_seams(cmd.version)
        content = self.remote_files.get(cmd.path)
        if content is not None:
            parsed_chains: dict[str, dict[str, str]] = {}
            parsed_rules: dict[str, list[IptablesRule]] = {}
            table: str | None = None
            for raw in content.decode().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("*"):
                    table = line[1:]
                    parsed_chains.setdefault(table, {})
                    parsed_rules.setdefault(table, [])
                elif table is None:
                    continue
                elif line.startswith(":"):
                    name, policy, _ = line[1:].split()
                    parsed_chains[table][name] = policy
                elif line.startswith("-A"):
                    parsed_rules[table].append(
                        self._iptables_definition(line, cmd.version)
                    )
            for t, table_chains in parsed_chains.items():
                if cmd.noflush:
                    chains.setdefault(t, {}).update(table_chains)
                    rules.setdefault(t, []).extend(parsed_rules[t])
                else:
                    chains[t] = table_chains
                    rules[t] = parsed_rules[t]
                self._rebuild_iptables_ordered(t, cmd.version)
        persistence[cmd.path] = True

    def create_ipset(self, set_name: str, set_type: str) -> None:
        """Record a simulated ``ipset create ... -exist``.

        A missing set lands with default creation parameters and no
        members; an existing set is left alone whatever its type (the
        -exist tolerance -- the operation validates the type against the
        fact before yielding).
        """
        if set_name not in self.ipsets:
            self.ipsets[set_name] = {
                "type": set_type,
                "revision": 0,
                "header": "family inet hashsize 1024 maxelem 65536",
                "size_in_memory": 0,
                "references": 0,
                "members": [],
            }

    def destroy_ipset(self, set_name: str) -> None:
        """Record a simulated ``ipset destroy``: drop the set, tolerantly."""
        self.ipsets.pop(set_name, None)

    def manage_ipset_entry(self, set_name: str, entry: str, present: bool) -> None:
        """Record a simulated ``ipset add/del``: one membership change.

        Adds are idempotent (-exist) and keep insertion order; deletes
        drop the entry if present. An absent set is a tolerant no-op
        (the operation only yields entry commands for sets the fact saw).
        """
        info = self.ipsets.get(set_name)
        if info is None:
            return
        if present:
            if entry not in info["members"]:
                info["members"].append(entry)
        elif entry in info["members"]:
            info["members"].remove(entry)

    def _firewalld_zone_store(self, permanent: bool) -> dict[str, FirewalldZone]:
        """The zone record map for one configuration scope.

        Every dual-apply firewalld mutator serves both scopes through
        this selector (the ``_iptables_seams`` precedent): True reads
        the ``firewalld_permanent_zones`` field, False the
        ``firewalld_zones`` one, so the two configurations never mix.
        """
        return self.firewalld_permanent_zones if permanent else self.firewalld_zones

    def manage_firewalld_zone(self, cmd: FirewalldZoneCommand) -> None:
        """Record a simulated ``--new-zone``/``--delete-zone``.

        Existence is permanent-only (both verbs reject a runtime
        invocation): a created zone lands fully shaped with the defaults
        a fresh zone carries, and a deleted zone's runtime copy lingers
        until ``reload_firewalld`` -- the dual-state contract the
        operation's reasons describe. A repeat create or delete is a
        no-op.
        """
        if cmd.present:
            if cmd.zone not in self.firewalld_permanent_zones:
                self.firewalld_permanent_zones[cmd.zone] = FirewalldZone(
                    active=False,
                    target="default",
                    short="",
                    description="",
                    icmp_block_inversion=False,
                    interfaces=[],
                    sources=[],
                    services=[],
                    ports=[],
                    protocols=[],
                    forward=False,
                    masquerade=False,
                    forward_ports=[],
                    source_ports=[],
                    icmp_blocks=[],
                    rich_rules=[],
                )
        else:
            self.firewalld_permanent_zones.pop(cmd.zone, None)

    def set_firewalld_zone_target(self, cmd: FirewalldZoneTargetCommand) -> None:
        """Record a simulated ``--set-target`` in one scope.

        An absent zone is a tolerant no-op (the operation only yields
        property commands for zones the fact saw).
        """
        record = self._firewalld_zone_store(cmd.permanent).get(cmd.zone)
        if record is not None:
            record["target"] = cmd.target

    def set_firewalld_zone_text(self, cmd: FirewalldZoneTextCommand) -> None:
        """Record a simulated ``--set-short``/``--set-description`` in one scope."""
        record = self._firewalld_zone_store(cmd.permanent).get(cmd.zone)
        if record is not None:
            if cmd.kind == "short":
                record["short"] = cmd.text
            else:
                record["description"] = cmd.text

    def manage_firewalld_zone_entry(self, cmd: FirewalldZoneEntryCommand) -> None:
        """Record a simulated zone membership add/remove in one scope.

        An interface add rebinds: the value is removed from every other
        zone of the scope first (the ``--change-interface`` semantics the
        descriptor renders), so one interface lives in at most one zone
        per scope. Other adds are idempotent and keep insertion order;
        removes drop the value if present. An absent zone is a tolerant
        no-op (the operations only yield for zones the fact saw).
        """
        zones = self._firewalld_zone_store(cmd.permanent)
        record = zones.get(cmd.zone)
        if record is None:
            return
        members = record[_ZONE_MEMBER_FIELDS[cmd.kind]]  # type: ignore[literal-required]
        if cmd.present:
            if cmd.kind == "interface":
                for other in zones.values():
                    if cmd.value in other["interfaces"]:
                        other["interfaces"].remove(cmd.value)
            if cmd.value not in members:
                members.append(cmd.value)
        elif cmd.value in members:
            members.remove(cmd.value)

    def set_firewalld_masquerade(self, cmd: FirewalldMasqueradeCommand) -> None:
        """Record a simulated ``--add-masquerade``/``--remove-masquerade``."""
        record = self._firewalld_zone_store(cmd.permanent).get(cmd.zone)
        if record is not None:
            record["masquerade"] = cmd.enabled

    def manage_firewalld_service(self, cmd: FirewalldServiceCommand) -> None:
        """Record a simulated ``--new-service``/``--delete-service``.

        Definitions are permanent-only, so one store serves both the
        fact and the mutator; a created definition lands fully shaped
        and empty, a repeat create or delete is a no-op.
        """
        if cmd.present:
            if cmd.service not in self.firewalld_services:
                self.firewalld_services[cmd.service] = FirewalldService(
                    short="",
                    description="",
                    ports=[],
                    source_ports=[],
                    protocols=[],
                    modules=[],
                    destination={},
                )
        else:
            self.firewalld_services.pop(cmd.service, None)

    def manage_firewalld_service_entry(self, cmd: FirewalldServiceEntryCommand) -> None:
        """Record a simulated service-definition membership add/remove.

        Adds are idempotent and keep insertion order; removes drop the
        value if present. An absent definition is a tolerant no-op.
        """
        record = self.firewalld_services.get(cmd.service)
        if record is None:
            return
        members = record[_SERVICE_MEMBER_FIELDS[cmd.kind]]  # type: ignore[literal-required]
        if cmd.present:
            if cmd.value not in members:
                members.append(cmd.value)
        elif cmd.value in members:
            members.remove(cmd.value)

    def set_firewalld_service_text(self, cmd: FirewalldServiceTextCommand) -> None:
        """Record a simulated service ``--set-short``/``--set-description``."""
        record = self.firewalld_services.get(cmd.service)
        if record is not None:
            if cmd.kind == "short":
                record["short"] = cmd.text
            else:
                record["description"] = cmd.text

    def _firewalld_ipset_store(self, permanent: bool) -> dict[str, FirewalldIpSet]:
        """The ipset record map for one configuration scope."""
        return self.firewalld_permanent_ipsets if permanent else self.firewalld_ipsets

    def manage_firewalld_ipset(self, cmd: FirewalldIpsetCommand) -> None:
        """Record a simulated ``--new-ipset``/``--delete-ipset``.

        Existence is permanent-only; the ``key=value`` creation options
        parse into the record's options map at creation only (an existing
        set keeps its options -- the operation validates the type before
        yielding). A deleted set's runtime copy lingers until
        ``reload_firewalld``.
        """
        if cmd.present:
            if cmd.ipset_name not in self.firewalld_permanent_ipsets:
                options: dict[str, str] = {}
                for token in cmd.options:
                    key, sep, value = token.partition("=")
                    if sep:
                        options[key] = value
                self.firewalld_permanent_ipsets[cmd.ipset_name] = FirewalldIpSet(
                    type=cmd.ipset_type, options=options, entries=[]
                )
        else:
            self.firewalld_permanent_ipsets.pop(cmd.ipset_name, None)

    def manage_firewalld_ipset_entry(self, cmd: FirewalldIpsetEntryCommand) -> None:
        """Record a simulated ipset ``--add-entry``/``--remove-entry`` in one scope."""
        record = self._firewalld_ipset_store(cmd.permanent).get(cmd.ipset_name)
        if record is None:
            return
        if cmd.present:
            if cmd.entry not in record["entries"]:
                record["entries"].append(cmd.entry)
        elif cmd.entry in record["entries"]:
            record["entries"].remove(cmd.entry)

    def reload_firewalld(self, cmd: FirewalldReloadCommand) -> None:
        """Record a simulated reload / complete-reload / runtime-to-permanent.

        reload and complete_reload copy the permanent zone/ipset/policy
        stores over the runtime ones (the simulation models no connection
        state, so the two verbs coincide); the runtime zone records regain
        their ``(active)`` marker from bound interfaces or sources.
        runtime_to_permanent copies the other way, clearing the marker (a
        permanent listing carries none). Records are copied through the
        module's own ``_copy_record`` so the two scopes never share
        mutable lists.
        """
        if cmd.mode == "runtime_to_permanent":
            self.firewalld_permanent_zones = {
                name: {**_copy_record(record), "active": False}  # type: ignore[assignment]
                for name, record in self.firewalld_zones.items()
            }
            self.firewalld_permanent_ipsets = {
                name: _copy_record(record)  # type: ignore[assignment]
                for name, record in self.firewalld_ipsets.items()
            }
            self.firewalld_permanent_policies = {
                name: _copy_record(record)  # type: ignore[assignment]
                for name, record in self.firewalld_policies.items()
            }
        else:
            self.firewalld_zones = {
                name: {
                    **_copy_record(record),
                    "active": bool(record["interfaces"] or record["sources"]),
                }  # type: ignore[assignment]
                for name, record in self.firewalld_permanent_zones.items()
            }
            self.firewalld_ipsets = {
                name: _copy_record(record)  # type: ignore[assignment]
                for name, record in self.firewalld_permanent_ipsets.items()
            }
            self.firewalld_policies = {
                name: _copy_record(record)  # type: ignore[assignment]
                for name, record in self.firewalld_permanent_policies.items()
            }

    def set_ufw_state(self, enabled: bool) -> None:
        """Record a simulated ``ufw --force enable`` / ``ufw disable``.

        The seam models the REPORTED status record: an inactive ufw prints
        only ``Status: inactive``, so disable restores the empty record
        (the real configuration persists in /etc/default/ufw, which the
        simulation does not model). Enable fills any empty field with the
        installation defaults (low logging, deny incoming, allow outgoing,
        disabled routed) a freshly enabled ufw reports; a seeded field is
        kept, so a policy set before enabling survives.
        """
        if enabled:
            self.ufw_status["active"] = True
            if not self.ufw_status["logging"]:
                self.ufw_status["logging"] = "low"
            if not self.ufw_status["default_incoming"]:
                self.ufw_status["default_incoming"] = "deny"
            if not self.ufw_status["default_outgoing"]:
                self.ufw_status["default_outgoing"] = "allow"
            if not self.ufw_status["default_routed"]:
                self.ufw_status["default_routed"] = "disabled"
        else:
            self.ufw_status = UfwStatusInfo(
                active=False,
                logging="",
                default_incoming="",
                default_outgoing="",
                default_routed="",
            )

    def set_ufw_default_policy(self, policy: str, direction: str) -> None:
        """Record a simulated ``ufw default <policy> <direction>``."""
        self.ufw_status[f"default_{direction}"] = policy  # type: ignore[literal-required]

    def set_ufw_logging(self, level: str) -> None:
        """Record a simulated ``ufw logging <level>``.

        ``on`` keeps the current level when already logging (ufw's
        set_loglevel semantics) and reads as ``low`` when off or
        unreadable; a concrete level replaces the word verbatim.
        """
        if level == "on":
            if self.ufw_status["logging"] in ("", "off", "unknown"):
                self.ufw_status["logging"] = "low"
        else:
            self.ufw_status["logging"] = level

    def reset_ufw(self) -> None:
        """Record a simulated ``ufw --force reset``.

        The real command backs up the current rules, deletes every user
        rule and disables the firewall; the post-reset status reads
        inactive, so the seam clears the ruleset and restores the
        inactive/empty record.
        """
        self.ufw_rules = []
        self.ufw_status = UfwStatusInfo(
            active=False,
            logging="",
            default_incoming="",
            default_outgoing="",
            default_routed="",
        )

    def manage_ufw_rule(self, cmd: UfwRuleCommand) -> None:
        """Record a simulated ``ufw [route] allow/deny/reject/limit`` change.

        The add form builds a ``UfwRule`` from the descriptor's identity
        projection (``_command_rule_key`` -- the same projection the
        operation matched against the fact) and appends it, or inserts it
        at ``insert - 1``; a non-v6 add also appends the ``(v6)`` twin ufw
        maintains itself, at the end of the v6 section the numbered
        listing prints. The delete form removes ONE matching row per
        descriptor (the operation yields one per match, so duplicates
        each get their delete) plus its v6 twin for a non-v6 delete; a
        comment-less delete ignores the stored comment, mirroring ufw's
        own delete. Numbers are renumbered after every mutation, as
        ``ufw status numbered`` always reads.
        """
        key = _command_rule_key(cmd)
        v6 = _is_v6(cmd.from_ip or None, cmd.to_ip or None)
        if cmd.present:
            row = UfwRule(number=0, v6=v6, **key)
            if cmd.insert is not None:
                self.ufw_rules.insert(min(cmd.insert - 1, len(self.ufw_rules)), row)
            else:
                self.ufw_rules.append(row)
            if not v6:
                self.ufw_rules.append(UfwRule(number=0, v6=True, **key))
        else:
            commentless = not cmd.comment
            self._remove_ufw_match(key, v6, commentless)
            if not v6:
                self._remove_ufw_match(key, True, commentless)
        for number, row in enumerate(self.ufw_rules, start=1):
            row["number"] = number

    def _remove_ufw_match(
        self, key: dict[str, Any], v6: bool, commentless: bool
    ) -> None:
        """Drop the first row of one family matching a rule key. Pure effect."""
        target = dict(key)
        if commentless:
            target.pop("comment")
        for index, row in enumerate(self.ufw_rules):
            if row["v6"] != v6:
                continue
            row_key = _rule_key(row)
            if commentless:
                row_key.pop("comment")
            if row_key == target:
                del self.ufw_rules[index]
                return

    def _lxd_instance(self, name: str) -> LxdInstance | None:
        """The stored listing entry for one instance, or None. Pure lookup."""
        return next(
            (entry for entry in self.lxd_instances if entry.get("name") == name), None
        )

    def _lxd_refresh_instance(self, name: str) -> None:
        """Keep every per-instance seam in step with the listing entry.

        Rebuilds the expanded config document (profile inheritance
        merged under the own config/devices, in profile order), syncs
        the fast container row's status and the runtime state
        document's status + snapshots copy. Called after every mutation
        that touches an instance, so no fact ever reads a stale view.
        """
        entry = self._lxd_instance(name)
        if entry is None:
            return
        expanded_config: dict[str, Any] = {}
        expanded_devices: dict[str, Any] = {}
        for profile_name in entry.get("profiles") or []:
            profile = next(
                (p for p in self.lxd_profiles if p.get("name") == profile_name), None
            )
            if profile is not None:
                expanded_config.update(profile.get("config") or {})
                expanded_devices.update(profile.get("devices") or {})
        expanded_config.update(entry.get("config") or {})
        expanded_devices.update(entry.get("devices") or {})
        self.lxd_instance_configs[name] = {
            "name": name,
            "description": entry.get("description", ""),
            "config": dict(entry.get("config") or {}),
            "devices": {
                key: dict(value)
                for key, value in (entry.get("devices") or {}).items()
            },
            "profiles": list(entry.get("profiles") or []),
            "expanded_config": expanded_config,
            "expanded_devices": expanded_devices,
        }
        container = next(
            (c for c in self.lxd_containers if c.get("name") == name), None
        )
        if container is not None:
            container["status"] = entry.get("status", "")
        state = self.lxd_instance_states.get(name)
        if state is not None:
            state["status"] = entry.get("status", "")
            state["snapshots"] = [
                dict(s) for s in self.lxd_instance_snapshots.get(name, [])
            ]

    def launch_lxd_instance(self, cmd: LxdLaunchCommand) -> None:
        """Record a simulated ``lxc launch``: create AND start an instance.

        ``lxd.container``/``lxd.instance``'s create mutation. Fills every
        seam the instance facts read: the full listing entry (status
        Running), the fast container row for a container (a VM never
        appears in ``lxc list --fast``), the expanded config document,
        the runtime state document and an empty snapshot list. The base
        image fingerprint resolves through the simulated image store
        (by fingerprint or alias) and falls back to a deterministic
        sha256 of the reference, recorded as ``volatile.base_image`` the
        way LXD does at launch -- which is what ``lxd.instance``'s
        image-drift report compares against.
        """
        name = cmd.instance
        instance_type = (
            "virtual-machine" if cmd.instance_type == "vm" else "container"
        )
        reference = cmd.image.partition(":")[2] if ":" in cmd.image else cmd.image
        matched = next(
            (
                entry
                for entry in self.lxd_images
                if entry.get("fingerprint") == reference
                or any(
                    alias.get("name") == reference
                    for alias in entry.get("aliases") or []
                )
            ),
            None,
        )
        fingerprint = (
            matched.get("fingerprint")
            if matched is not None
            else hashlib.sha256(cmd.image.encode()).hexdigest()
        )
        config = dict(_config_pairs(cmd.config))
        config["volatile.base_image"] = fingerprint or ""
        devices = {
            device_name: {"type": device_type, **dict(_config_pairs(props))}
            for device_name, device_type, props in cmd.devices
        }
        entry: LxdInstance = {
            "name": name,
            "description": "",
            "status": "Running",
            "type": instance_type,
            "architecture": "x86_64",
            "ephemeral": cmd.ephemeral,
            "profiles": list(cmd.profiles) or ["default"],
            "config": config,
            "devices": devices,
        }
        self.lxd_instances = [
            e for e in self.lxd_instances if e.get("name") != name
        ]
        self.lxd_instances.append(entry)
        if instance_type == "container":
            self.lxd_containers = [
                c for c in self.lxd_containers if c.get("name") != name
            ]
            self.lxd_containers.append(
                {"name": name, "status": "Running", "type": "container"}
            )
        self.lxd_instance_states[name] = {
            "name": name,
            "status": "Running",
            "type": instance_type,
            "pid": len(self.lxd_instances),
        }
        self.lxd_instance_snapshots.setdefault(name, [])
        self._lxd_refresh_instance(name)

    def set_lxd_instance_state(self, cmd: LxdStateCommand) -> None:
        """Record a simulated ``lxc start/stop/restart/pause/resume``.

        Updates the status in every seam that carries one (the listing
        entry, the fast container row, the runtime state document --
        created on first touch when a test seeded only the listing).
        ``restart``/``resume`` read as Running. Stopping an EPHEMERAL
        instance deletes it (LXD's own semantics), cascading through
        ``delete_lxd_instance``. A missing instance is a tolerant no-op:
        the stateless restarted/unfrozen requests never fact-check, and
        the real CLI would fail at apply time.
        """
        status = {
            "start": "Running",
            "stop": "Stopped",
            "restart": "Running",
            "pause": "Frozen",
            "resume": "Running",
        }[cmd.action]
        entry = self._lxd_instance(cmd.instance)
        if entry is None:
            return
        if cmd.action == "stop" and entry.get("ephemeral"):
            self.delete_lxd_instance(
                LxdDeleteCommand(
                    instance=cmd.instance, remote=cmd.remote, project=cmd.project
                )
            )
            return
        entry["status"] = status
        if cmd.instance not in self.lxd_instance_states:
            self.lxd_instance_states[cmd.instance] = {
                "name": cmd.instance,
                "status": status,
                "type": entry.get("type", "container"),
            }
        self._lxd_refresh_instance(cmd.instance)

    def delete_lxd_instance(self, cmd: LxdDeleteCommand) -> None:
        """Record a simulated ``lxc delete <instance>[/snapshot]``.

        The ``instance/snapshot`` name form (``lxd.instance_snapshot``'s
        delete) drops one snapshot and refreshes the state document's
        snapshots copy; a bare instance name cascades through every
        seam: the listing entry, the fast container row, the config and
        state documents, the snapshot list and the instance's own
        storage volumes (type container/virtual-machine) in every pool.
        An absent target is a tolerant no-op.
        """
        name = cmd.instance
        if "/" in name:
            instance, _, snapshot = name.partition("/")
            snapshots = self.lxd_instance_snapshots.get(instance)
            if snapshots is not None:
                self.lxd_instance_snapshots[instance] = [
                    s for s in snapshots if s.get("name") != snapshot
                ]
            self._lxd_refresh_instance(instance)
            return
        self.lxd_instances = [
            e for e in self.lxd_instances if e.get("name") != name
        ]
        self.lxd_containers = [
            c for c in self.lxd_containers if c.get("name") != name
        ]
        self.lxd_instance_configs.pop(name, None)
        self.lxd_instance_states.pop(name, None)
        self.lxd_instance_snapshots.pop(name, None)
        for pool, volumes in self.lxd_storage_volumes.items():
            self.lxd_storage_volumes[pool] = [
                v
                for v in volumes
                if not (
                    v.get("name") == name
                    and v.get("type") in ("container", "virtual-machine")
                )
            ]

    def copy_lxd_instance(self, cmd: LxdCopyCommand) -> None:
        """Record a simulated ``lxc copy``: register the destination.

        The destination inherits the source's config/devices/profiles
        (copied, never shared) and its snapshots unless instance_only;
        a stateless copy reads Stopped, otherwise the source's status
        carries over. The source's instance storage volumes are cloned
        alongside. A missing source or an already-registered destination
        is a tolerant no-op (the operation fact-checks same-daemon
        destinations; the real CLI fails otherwise).
        """
        source = self._lxd_instance(cmd.instance)
        if source is None or self._lxd_instance(cmd.dest_instance) is not None:
            return
        entry: LxdInstance = {
            "name": cmd.dest_instance,
            "description": source.get("description", ""),
            "status": "Stopped" if cmd.stateless else source.get("status", "Stopped"),
            "type": source.get("type", "container"),
            "architecture": source.get("architecture", "x86_64"),
            "ephemeral": False,
            "profiles": list(source.get("profiles") or []),
            "config": dict(source.get("config") or {}),
            "devices": {
                key: dict(value)
                for key, value in (source.get("devices") or {}).items()
            },
        }
        self.lxd_instances.append(entry)
        if entry.get("type") == "container":
            self.lxd_containers.append(
                {
                    "name": cmd.dest_instance,
                    "status": entry.get("status", "Stopped"),
                    "type": "container",
                }
            )
        self.lxd_instance_states[cmd.dest_instance] = {
            "name": cmd.dest_instance,
            "status": entry.get("status", "Stopped"),
            "type": entry.get("type", "container"),
            "pid": len(self.lxd_instances),
        }
        self.lxd_instance_snapshots[cmd.dest_instance] = (
            []
            if cmd.instance_only
            else [dict(s) for s in self.lxd_instance_snapshots.get(cmd.instance, [])]
        )
        for volumes in self.lxd_storage_volumes.values():
            volumes.extend(
                {
                    **v,
                    "name": cmd.dest_instance,
                    "config": dict(v.get("config") or {}),
                }
                for v in list(volumes)
                if v.get("name") == cmd.instance
                and v.get("type") in ("container", "virtual-machine")
            )
        self._lxd_refresh_instance(cmd.dest_instance)

    def _lxd_refresh_profile(self, profile: str) -> None:
        """Re-expand every instance applying one profile.

        A profile's config/devices feed the expanded views of each
        instance that applies it, so a profile edit refreshes those
        instances too -- otherwise ``LxdInstanceConfig`` would report
        the pre-edit inheritance.
        """
        for instance in self.lxd_instances:
            if profile in (instance.get("profiles") or []):
                self._lxd_refresh_instance(instance.get("name", ""))

    def manage_lxd_image(self, cmd: LxdImageCommand) -> None:
        """Record a simulated ``lxc image copy`` / ``lxc image delete``.

        ``lxd.image``'s mutations. Caching registers an entry in
        ``lxd_images`` -- the simulation downloads nothing, so the
        fingerprint is the requested one when it already looks like a
        full SHA-256, else a deterministic sha256 of the source
        reference -- plus the ``--alias`` mapping in both alias views
        (the ``lxd_image_aliases`` listing and the image's own aliases
        array), so ``LxdImages`` and ``LxdImageAliases`` agree. Deleting
        resolves the reference by fingerprint OR alias (the CLI accepts
        both) and drops the image together with every alias targeting
        it. An already-cached fingerprint or an absent reference is a
        tolerant no-op: the operation fact-checks before yielding.
        """
        if not cmd.present:
            target = next(
                (
                    entry
                    for entry in self.lxd_images
                    if entry.get("fingerprint") == cmd.image
                    or any(
                        alias.get("name") == cmd.image
                        for alias in entry.get("aliases") or []
                    )
                ),
                None,
            )
            if target is None:
                return
            fingerprint = target.get("fingerprint")
            self.lxd_images = [
                e for e in self.lxd_images if e.get("fingerprint") != fingerprint
            ]
            self.lxd_image_aliases = [
                a for a in self.lxd_image_aliases if a.get("target") != fingerprint
            ]
            return

        hex_digits = set("0123456789abcdef")
        fingerprint = (
            cmd.image
            if len(cmd.image) == 64 and set(cmd.image) <= hex_digits
            else hashlib.sha256(cmd.source.encode()).hexdigest()
        )
        if any(e.get("fingerprint") == fingerprint for e in self.lxd_images):
            return
        entry: LxdImage = {
            "fingerprint": fingerprint,
            "aliases": [{"name": cmd.alias, "description": ""}] if cmd.alias else [],
            "public": False,
            "cached": True,
            "size": 0,
            "type": "container",
            "properties": {},
            "auto_update": False,
        }
        self.lxd_images.append(entry)
        if cmd.alias:
            self.lxd_image_aliases = [
                a for a in self.lxd_image_aliases if a.get("name") != cmd.alias
            ]
            self.lxd_image_aliases.append(
                {
                    "name": cmd.alias,
                    "target": fingerprint,
                    "type": "image",
                    "description": "",
                }
            )

    def manage_lxd_image_alias(self, cmd: LxdImageAliasCommand) -> None:
        """Record a simulated ``lxc image alias create/delete``.

        ``lxd.image_alias``'s mutations, keeping both alias views in
        step: the ``lxd_image_aliases`` listing and the target image's
        own aliases array in ``lxd_images``. Any same-name mapping is
        dropped first (the CLI's create fails on a taken name, which is
        why a repoint arrives as delete+create); deleting removes the
        mapping only, never the image it targets. A fingerprint no
        cached image carries is tolerated -- the alias listing then
        points at a target ``LxdImages`` does not report, exactly as a
        real daemon allows.
        """
        self.lxd_image_aliases = [
            a for a in self.lxd_image_aliases if a.get("name") != cmd.alias
        ]
        for image in self.lxd_images:
            image["aliases"] = [
                a for a in image.get("aliases") or [] if a.get("name") != cmd.alias
            ]
        if not cmd.present:
            return
        self.lxd_image_aliases.append(
            {
                "name": cmd.alias,
                "target": cmd.fingerprint,
                "type": "image",
                "description": "",
            }
        )
        target = next(
            (
                e
                for e in self.lxd_images
                if e.get("fingerprint") == cmd.fingerprint
            ),
            None,
        )
        if target is not None:
            aliases = list(target.get("aliases") or [])
            aliases.append({"name": cmd.alias, "description": ""})
            target["aliases"] = aliases

    def set_lxd_config(self, cmd: LxdSetCommand) -> None:
        """Record a simulated ``lxc <resource> set|unset <target> <key>``.

        One arm for the five resource families ``LxdSetCommand``
        renders: ``config`` (an instance's OWN keys), ``profile``,
        ``project`` (both the listing entry and the per-project
        configuration document), ``network`` and ``storage`` (the pool
        listing). ``value=None`` is the ``unset`` verb and pops the key.
        An instance or profile edit re-expands the affected instances;
        a missing target is a tolerant no-op -- every operation
        fact-checks existence before yielding a set.
        """
        if cmd.resource == "config":
            instance = self._lxd_instance(cmd.target)
            if instance is None:
                return
            config = dict(instance.get("config") or {})
            if cmd.value is None:
                config.pop(cmd.key, None)
            else:
                config[cmd.key] = cmd.value
            instance["config"] = config
            self._lxd_refresh_instance(cmd.target)
            return

        entries: list[Any]
        if cmd.resource == "profile":
            entries = self.lxd_profiles
        elif cmd.resource == "project":
            entries = self.lxd_projects
        elif cmd.resource == "network":
            entries = self.lxd_networks
        elif cmd.resource == "storage":
            entries = self.lxd_storage_pools
        else:
            raise TypeError(f"unknown lxd set resource: {cmd.resource!r}")
        entry = next((e for e in entries if e.get("name") == cmd.target), None)
        if entry is None:
            return
        config = dict(entry.get("config") or {})
        if cmd.value is None:
            config.pop(cmd.key, None)
        else:
            config[cmd.key] = cmd.value
        entry["config"] = config
        if cmd.resource == "profile":
            self._lxd_refresh_profile(cmd.target)
        elif cmd.resource == "project":
            document = self.lxd_project_configs.get(cmd.target)
            if document is not None:
                document["config"] = dict(config)

    def manage_lxd_device(self, cmd: LxdDeviceCommand) -> None:
        """Record a simulated ``lxc config/profile device add|remove``.

        Attaches a device (``{"type": ..., **props}``) to, or detaches it
        from, an instance's OWN devices or a profile's devices; a drifted
        device arrives as remove+add, which this models exactly. Both
        edits re-expand the affected instances so ``LxdInstanceConfig``
        never reports stale inheritance. A missing target is a tolerant
        no-op.
        """
        entry: Any
        if cmd.resource == "config":
            entry = self._lxd_instance(cmd.target)
        elif cmd.resource == "profile":
            entry = next(
                (p for p in self.lxd_profiles if p.get("name") == cmd.target), None
            )
        else:
            raise TypeError(f"unknown lxd device resource: {cmd.resource!r}")
        if entry is None:
            return
        devices = {key: dict(value) for key, value in (entry.get("devices") or {}).items()}
        if cmd.present:
            devices[cmd.device] = {
                "type": cmd.device_type,
                **dict(_config_pairs(cmd.props)),
            }
        else:
            devices.pop(cmd.device, None)
        entry["devices"] = devices
        if cmd.resource == "config":
            self._lxd_refresh_instance(cmd.target)
        else:
            self._lxd_refresh_profile(cmd.target)

    def assign_lxd_profiles(self, cmd: LxdProfileAssignCommand) -> None:
        """Record a simulated ``lxc profile assign``: replace the list.

        The instance's profile list is replaced wholesale, in the given
        order (profile application order matters in LXD), and the
        expanded config/devices views are recomputed from the new list.
        A missing instance is a tolerant no-op.
        """
        entry = self._lxd_instance(cmd.instance)
        if entry is None:
            return
        entry["profiles"] = list(cmd.profiles)
        self._lxd_refresh_instance(cmd.instance)

    def create_lxd_snapshot(self, cmd: LxdSnapshotCommand) -> None:
        """Record a simulated ``lxc snapshot``: register the snapshot.

        ``lxd.instance_snapshot``'s create mutation. Upserts the entry in
        the instance's snapshot list (the operation only yields for an
        absent name) and refreshes the runtime state document, whose
        ``snapshots`` array ``LxdInstanceSnapshots`` shares. The
        ``stateful`` flag is recorded verbatim -- the simulation keeps no
        snapshot contents, only the metadata the facts report.
        """
        snapshots = self.lxd_instance_snapshots.setdefault(cmd.instance, [])
        entry: LxdSnapshot = {
            "name": cmd.snapshot,
            "stateful": cmd.stateful,
            "description": "",
        }
        self.lxd_instance_snapshots[cmd.instance] = [
            s for s in snapshots if s.get("name") != cmd.snapshot
        ] + [entry]
        self._lxd_refresh_instance(cmd.instance)

    def restore_lxd_snapshot(self, cmd: LxdRestoreCommand) -> None:
        """Record a simulated ``lxc restore``.

        The simulation stores no snapshot contents, so a restore models
        only what the facts can observe: a STATEFUL restore puts the
        instance back Running (the runtime state comes with it), a
        non-stateful one leaves the status alone (the CLI requires the
        instance stopped first). A missing instance is a tolerant no-op:
        ``restore=True`` is stateless and never fact-checks.
        """
        entry = self._lxd_instance(cmd.instance)
        if entry is None:
            return
        if cmd.stateful:
            entry["status"] = "Running"
        self._lxd_refresh_instance(cmd.instance)

    def manage_lxd_network(self, cmd: LxdNetworkCommand) -> None:
        """Record a simulated ``lxc network create/delete``.

        ``lxd.network``'s existence mutations. A create registers a
        MANAGED network with its type, creation-time config and the
        standalone-server ``["none"]`` location; a delete drops the
        entry. Config drift on an existing network arrives separately
        through ``set_lxd_config``. An unmanaged interface is never
        touched here -- the operation refuses it in prepare.
        """
        if not cmd.present:
            self.lxd_networks = [
                n for n in self.lxd_networks if n.get("name") != cmd.network
            ]
            return
        entry: LxdNetwork = {
            "name": cmd.network,
            "type": cmd.network_type,
            "managed": True,
            "status": "Created",
            "config": dict(_config_pairs(cmd.config)),
            "description": "",
            "locations": ["none"],
        }
        self.lxd_networks = [
            n for n in self.lxd_networks if n.get("name") != cmd.network
        ]
        self.lxd_networks.append(entry)

    def cancel_lxd_operation(self, cmd: LxdOperationCancelCommand) -> None:
        """Record a simulated ``lxc operation delete``: prune the id.

        ``lxd.operation``'s cancel mutation. The daemon drops a
        cancelled operation from the listing, so the id disappears from
        ``lxd_operations`` and a following wait succeeds at once. An
        already-pruned id is a tolerant no-op (the operation only yields
        a cancel for a registered one).
        """
        self.lxd_operations = [
            o for o in self.lxd_operations if o.get("id") != cmd.operation_id
        ]

    def finish_lxd_operation(self, cmd: LxdOperationWaitCommand) -> None:
        """Model a simulated operation wait as immediate completion.

        ``execute_command`` intercepts EVERY descriptor, wait commands
        included, so ``LxdOperationWaitCommand.wait`` is never awaited
        on a SimulatedHost: this arm prunes the id instead, which is
        exactly the state the real poll loop waits for (the daemon
        drops finished operations). An in-memory state never changes on
        its own, so an already-absent id -- the common case -- succeeds
        without any mutation, the ``check_wait`` precedent.
        """
        self.lxd_operations = [
            o for o in self.lxd_operations if o.get("id") != cmd.operation_id
        ]

    def manage_lxd_profile(self, cmd: LxdProfileCommand) -> None:
        """Record a simulated ``lxc profile create/delete``.

        ``lxd.profile``'s existence mutations. A create registers an
        EMPTY profile (the CLI's create verb takes no config flags --
        the requested keys and devices arrive afterwards through
        ``set_lxd_config``/``manage_lxd_device``); a delete drops the
        entry. The real CLI refuses to delete a profile instances still
        apply, which the simulation leaves to the operation's caller.
        """
        if not cmd.present:
            self.lxd_profiles = [
                p for p in self.lxd_profiles if p.get("name") != cmd.profile
            ]
            return
        if any(p.get("name") == cmd.profile for p in self.lxd_profiles):
            return
        self.lxd_profiles.append(
            {"name": cmd.profile, "description": "", "config": {}, "devices": {}}
        )

    def manage_lxd_project(self, cmd: LxdProjectCommand) -> None:
        """Record a simulated ``lxc project create/delete``.

        ``lxd.project``'s existence mutations. A create registers the
        listing entry WITH its ``-c`` creation-time config and copies
        the same document into ``lxd_project_configs`` (the two facts
        read the same shape); a delete drops both. Config drift on an
        existing project arrives through ``set_lxd_config``, which keeps
        the two stores in step.
        """
        if not cmd.present:
            self.lxd_projects = [
                p for p in self.lxd_projects if p.get("name") != cmd.project
            ]
            self.lxd_project_configs.pop(cmd.project, None)
            return
        entry: LxdProject = {
            "name": cmd.project,
            "description": "",
            "config": dict(_config_pairs(cmd.config)),
            "used_by": [],
        }
        self.lxd_projects = [
            p for p in self.lxd_projects if p.get("name") != cmd.project
        ]
        self.lxd_projects.append(entry)
        self.lxd_project_configs[cmd.project] = {
            **entry,
            "config": dict(entry["config"]),
            "used_by": list(entry["used_by"]),
        }

    def manage_lxd_storage_pool(self, cmd: LxdStoragePoolCommand) -> None:
        """Record a simulated ``lxc storage create/delete``.

        ``lxd.storage_pool``'s existence mutations. A create registers
        the pool with its driver, creation-time config and the
        standalone-server ``["none"]`` location; a delete drops the pool
        AND its volume list (no pool, no volumes to report). Config
        drift arrives through ``set_lxd_config``; a driver mismatch
        never reaches here -- the operation raises in prepare.
        """
        if not cmd.present:
            self.lxd_storage_pools = [
                p for p in self.lxd_storage_pools if p.get("name") != cmd.pool
            ]
            self.lxd_storage_volumes.pop(cmd.pool, None)
            return
        entry: LxdStoragePool = {
            "name": cmd.pool,
            "driver": cmd.driver,
            "status": "Created",
            "config": dict(_config_pairs(cmd.config)),
            "description": "",
            "locations": ["none"],
        }
        self.lxd_storage_pools = [
            p for p in self.lxd_storage_pools if p.get("name") != cmd.pool
        ]
        self.lxd_storage_pools.append(entry)

    def manage_lxd_storage_volume(self, cmd: LxdStorageVolumeCommand) -> None:
        """Record a simulated ``lxc storage volume create/delete``.

        ``lxd.storage_volume``'s mutations. A create registers a CUSTOM
        filesystem volume (the only user-manageable type) with its
        creation-time config in the pool's list; a delete drops the
        named volume. Instance/image volumes are never created here --
        they appear through ``launch_lxd_instance``/``copy_lxd_instance``
        seeding and are refused by the operation in prepare.
        """
        volumes = self.lxd_storage_volumes.setdefault(cmd.pool, [])
        if not cmd.present:
            self.lxd_storage_volumes[cmd.pool] = [
                v for v in volumes if v.get("name") != cmd.volume
            ]
            return
        entry: LxdStorageVolume = {
            "name": cmd.volume,
            "type": "custom",
            "content_type": "filesystem",
            "config": dict(_config_pairs(cmd.config)),
            "description": "",
            "location": "none",
        }
        self.lxd_storage_volumes[cmd.pool] = [
            v for v in volumes if v.get("name") != cmd.volume
        ] + [entry]

    def install_flatpak_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``flatpak install``: mark the applications installed.

        ``flatpak.packages``' install mutation. Each name gains a
        ``FlatpakPackageInfo`` entry in ``flatpak_package_info`` (the store
        ``FlatpakPackage`` reads and whose keys ``FlatpakPackages`` projects) --
        id set to the application ID, ref and version left as the empty strings
        an offline simulation has no remote metadata to fill (snap's placeholder
        precedent). The install remote is not modeled: the simulation records no
        origin per app. An already-installed name keeps its recorded detail, as
        ``flatpak install`` leaves a present application alone.
        """
        for package in packages:
            if package in self.flatpak_package_info:
                continue
            self.flatpak_package_info[package] = {
                "id": package,
                "ref": "",
                "version": "",
            }

    def remove_flatpak_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``flatpak uninstall``: drop the applications.

        The ``present=False`` direction of ``flatpak.packages``. The name's
        entry leaves ``flatpak_package_info``, so ``FlatpakPackage`` reports
        ``None`` and ``FlatpakPackages`` no longer lists it afterwards. An
        absent name is tolerated (the real command would exit non-zero, but the
        operation's fact check never yields that removal).
        """
        for package in packages:
            self.flatpak_package_info.pop(package, None)

    def install_snap_packages(self, packages: tuple[str, ...], channel: str) -> None:
        """Record a simulated ``snap install``: mark the snaps installed.

        ``snap.package``'s install mutation. Each name gains a
        ``SnapPackageInfo`` entry in ``snap_package_info`` (the store
        ``SnapPackage`` reads) carrying the channel the command installed on --
        the one field the operation compares -- with the publisher, store id and
        version left as the empty strings an offline simulation has no store
        metadata to fill (apk's ``[""]`` placeholder precedent). An
        already-installed name keeps its recorded detail, as ``snap`` leaves a
        present snap alone.
        """
        for package in packages:
            if package in self.snap_package_info:
                continue
            self.snap_package_info[package] = {
                "name": package,
                "publisher": "",
                "snap_id": "",
                "channel": channel,
                "version": "",
            }

    def remove_snap_packages(self, packages: tuple[str, ...]) -> None:
        """Record a simulated ``snap remove``: drop the snaps.

        The ``present=False`` direction of ``snap.package``. The name's entry
        leaves ``snap_package_info``, so ``SnapPackage`` reports ``None`` for it
        afterwards. An absent name is tolerated (the real command would exit
        non-zero, but the operation's fact check never yields that removal).
        """
        for package in packages:
            self.snap_package_info.pop(package, None)

    def refresh_snap_packages(self, packages: tuple[str, ...], channel: str) -> None:
        """Record a simulated ``snap refresh --channel=<channel>``: switch tracking.

        ``snap.package``'s channel-change mutation. The entry is REPLACED with a
        copy carrying the new ``channel`` -- never mutated in place, so a test's
        seeded ``SnapPackageInfo`` object is never aliased into host state --
        and keeps its recorded publisher, store id and version. An absent name
        is skipped: refreshing an uninstalled snap fails on the real host, and
        the operation only yields names the fact reported installed.
        """
        for package in packages:
            info = self.snap_package_info.get(package)
            if info is None:
                continue
            self.snap_package_info[package] = {**info, "channel": channel}

    def check_wait(self, cmd: WaitForFactCommand) -> None:
        """Evaluate a simulated wait once, without polling or sleeping.

        The in-memory state never changes on its own, so there is nothing to
        wait *for*: the condition either already holds -- the wait succeeds
        -- or it never will, and the wait fails at once with the same
        ``WaitTimeoutError`` the real poll loop raises at its deadline. The
        fact's value is read straight from the seam the fact itself would
        read (``execute_command`` is synchronous, so the async ``process``
        cannot be awaited here); a wait on a fact this simulation does not
        model is a hard error, like any unknown descriptor.
        """
        if cmd.fact_cls is SystemdServiceState:
            (unit,) = cmd.args
            value = self.services.get(unit)
        elif cmd.fact_cls is Port:
            # The same seam ``Port.process`` reads, with the same
            # nothing-listening record it returns for an unlisted port.
            port, protocol = cmd.args
            value = self.listening_ports.get(
                (protocol, port),
                PortInfo(listening=False, process=None, pid=None),
            )
        else:
            raise TypeError(f"unsupported simulated wait fact: {cmd.fact_cls!r}")
        if not cmd.predicate(value):
            raise WaitTimeoutError(
                f"host {self.name}: {cmd} -- condition not met within "
                f"{cmd.timeout}s (last value: {value!r})"
            )


__all__ = ["SimulatedHost"]
