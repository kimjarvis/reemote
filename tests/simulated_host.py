# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Test-framework stand-in for a real machine.

``SimulatedHost`` is part of the *test framework*, not of the library, so it
lives here in ``tests/`` rather than in ``src/reemote5/``. Tests and examples
inject it via ``run``'s ``host_factory`` argument (or hand it to
``run_prepare`` directly); the library never imports or constructs one.

The library relies only on the seams this class provides at the points where
a real asyncssh transport is missing:

- ``execute_command(cmd)``: applies the simulated effect of a command
  descriptor to the in-memory state. ``reemote5.drivers.send_command``
  forwards to it when the host provides it, and raises ``NotImplementedError``
  otherwise.
- ``packages`` / ``links`` / ``existing_files`` / ``file_stats`` /
  ``directory_stats`` / ``link_stats`` / ``remote_files`` /
  ``repositories`` / ``repo_list`` / ``enabled_modules`` / ``disabled_modules``
  / ``installed_modules`` / ``installed_groups`` / ``available_groups``
  / ``rpm_files`` / ``rpm_packages`` / ``services`` / ``enabled_services``
  / ``apk_autoremovable`` / ``apk_explicit_packages`` / ``apk_held_packages`` / ``apk_package_info`` / ``apk_packages`` / ``apk_repositories`` / ``apt_autoremovable`` / ``apt_held_packages`` / ``apt_keys`` / ``apt_packages`` / ``apt_policy`` / ``apt_simulations`` / ``apt_sources`` / ``apt_upgradable`` / ``arch`` / ``authorized_keys`` / ``block_devices`` / ``cpu_info`` / ``cpus`` / ``deb_files``
  / ``date`` / ``disk_stats`` / ``dmi_info`` / ``git_branches``
  / ``git_configs`` / ``git_head_detached`` / ``git_local_commits``
  / ``git_remote_commits`` / ``git_remote_urls`` / ``git_repos``
  / ``git_ssh_authenticated`` / ``git_statuses`` / ``git_submodules``
  / ``git_tags`` / ``git_tracking_branches`` / ``os_groups`` / ``has_gui``
  / ``home_directories`` / ``os_hostname`` / ``ipv4_addrs`` / ``ipv6_addrs``
  / ``os_kernel``
  / ``kernel_modules`` / ``os_kernel_version`` / ``login_records``
  / ``linux_distribution``
  / ``linux_gui`` / ``locales`` / ``lsb_release`` / ``lsblk_devices`` / ``mounts``
  / ``network_devices`` / ``os_path``
  / ``processes`` / ``smbios_info`` / ``sysctls`` / ``os_timezone`` / ``os_user`` / ``os_users``
  / ``uptime_seconds`` / ``command_paths``:
  the state the library's facts (``DnfPackages``, ``FileLink``, ``FileExists``,
  ``File``, ``Directory``, ``Link``, ``RemoteFileSha1``, ``FileContents``, ``DnfRepoFile``, ``DnfRepositories``,
  ``DnfRepoList``,
  ``DnfEnabledModules``, ``DnfDisabledModules``, ``DnfInstalledModules``,
  ``DnfGroups``, ``DnfAvailableGroups``, ``RpmPackage``,
  ``SystemdServiceState``, ``SystemdServiceEnabled``, ``ApkAutoremovablePackages``, ``ApkExplicitPackages``, ``ApkHeldPackages``, ``ApkInstalledPackages``, ``ApkPackage``, ``ApkRepositories``, ``AptAutoremovable``, ``AptHeldPackages``, ``AptKeys``, ``AptPackages``, ``AptPolicy``, ``AptSources``, ``AptSourcesFile``, ``AptUpgradable``, ``Arch``,
  ``AuthorizedKeys``, ``BlockDevices``, ``CpuInfo``, ``Cpus``, ``Date``, ``DebPackage``,
  ``DiskStats``, ``DmiInfo``, ``GitBranch``, ``GitConfig``,
  ``GitHeadDetached``, ``GitIsRepo``, ``GitLocalCommit``,
  ``GitRemoteBranchCommit``, ``GitRemoteUrl``, ``GitSshKeyConfigured``,
  ``GitStatus``, ``GitSubmodule``, ``GitTag``,
  ``GitTrackingBranch``, ``Groups``,
  ``HasGui``, ``Home``, ``Hostname``, ``Ipv4Addrs``, ``Ipv6Addrs``, ``Kernel``,
  ``KernelModules``, ``KernelVersion``, ``Last``,
  ``LinuxDistribution``, ``LinuxGui``, ``LinuxName``, ``Locales``,
  ``LsbRelease``, ``LsBlk``, ``Mounts``, ``NetworkDevices``, ``Path``, ``Processes``,
  ``SmbiosInfo``, ``SimulateOperationWillChange``, ``Sysctl``, ``Timezone``,
  ``Uptime``, ``User``, ``Users``, ``Which``) read as a
  stand-in for gathering over SSH.

Holding this state is what makes the "fresh fact cache per lockstep step"
property observable in the tests and the demo: ``send_command`` mutates the
state when an operation is changed, and the next operation's fact reads see
the mutation.
"""

import io
import posixpath
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from reemote5.commands import Command, StringCommand, WaitTimeoutError
from reemote5.hosts import Host
from reemote5.modules.apk.apk import (
    ApkHoldCommand,
    ApkInstallCommand,
    ApkPackageInfo,
    ApkPackageVersion,
    ApkPurgeCommand,
    ApkRemoveCommand,
    ApkRepository,
    ApkUpgradeCommand,
    ApkVirtualCommand,
    _split_constraint,
)
from reemote5.modules.apt.apt import (
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
    AptRepoAddCommand,
    AptRepoRemoveCommand,
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
    _repo_line_pattern,
)
from reemote5.modules.dnf.dnf import (
    DnfCleanCommand,
    DnfInstallCommand,
    DnfKeyImportCommand,
    DnfKeyUploadCommand,
    DnfModuleDisableCommand,
    DnfModuleEnableCommand,
    DnfRemoveCommand,
    DnfRepoInstallCommand,
    DnfRepoListEntry,
    DnfRepoRemoveCommand,
    DnfRepository,
    DnfRepoUploadCommand,
    DnfUpdateCommand,
    RpmInstallCommand,
    RpmPackageInfo,
)
from reemote5.modules.files.files import (
    CopyCommand,
    FileCommand,
    GetCommand,
    LinkCommand,
    MoveCommand,
    PutCommand,
    RemoveTreeCommand,
    read_source_bytes,
    write_dest_bytes,
)
from reemote5.modules.git.git import (
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
from reemote5.modules.hardware.hardware import (
    BlockDevice,
    DiskStat,
    LsBlkNode,
    NetworkDevice,
)
from reemote5.modules.server.server import (
    GroupAddCommand,
    GroupDelCommand,
    KillCommand,
    ModprobeCommand,
    MountCommand,
    SetHostnameCommand,
    SetTimezoneCommand,
    SystemctlCommand,
    SystemdServiceState,
    WaitForFactCommand,
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
    links: dict[str, str] = field(default_factory=dict)  # src -> target
    existing_files: set[str] = field(default_factory=set)
    # Remote path -> the content bytes the simulated remote filesystem holds
    # there. This single store backs both transfer directions: ``RemoteFileSha1``
    # derives its digest from it, ``FileContents`` decodes its text back out,
    # a simulated ``files.put`` writes the source
    # stream's bytes into it, and a simulated ``files.get`` reads them back out
    # into the destination stream. The ``dnf.key``/``dnf.repo``/``apt.key``/
    # ``apt.sources_file`` spool uploads land here too:
    # ``import_key``/``install_repo_file``/``install_apt_key``/
    # ``install_apt_sources_file`` pop the spool entry, and the install
    # mutators store the content bytes back at their dest path.
    # Storing content (not just a hash) is what
    # lets a put then a get of the same path round-trip, and what makes a repeat
    # transfer a no-op: execute_command mutates it, and the next step's fresh
    # fact cache sees the mutation.
    remote_files: dict[str, bytes] = field(default_factory=dict)
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
    # key). ``dnf.key`` is non-idempotent and reads no fact, so nothing in the
    # library reads this back; it exists so a test can observe that the
    # simulated ``rpm --import`` ran. ``import_key`` fills it and clears the
    # ``/tmp`` spool entry the paired ``DnfKeyUploadCommand`` wrote into
    # ``remote_files``.
    imported_keys: set[bytes] = field(default_factory=set)
    # Simulated systemd: full unit name (``httpd.service``) -> its ActiveState
    # string (``active``/``inactive``/...), backing ``SystemdServiceState``;
    # and the set of unit names enabled on boot, backing
    # ``SystemdServiceEnabled``. A unit absent from ``services`` reads as
    # None (as the fact reports when systemd answers nothing).
    services: dict[str, str] = field(default_factory=dict)
    enabled_services: set[str] = field(default_factory=set)
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
    # fact's answer for a missing or empty file. A read-only seam: the
    # namespace has no repository operation yet, so no ``execute_command``
    # mutator maintains it and a test seeds it directly.
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
    # simulated machine with no sources files, like pyinfra's
    # ``default = list``. A test seeds it directly; ``add_ppa``/``remove_ppa``
    # (the simulated ``apt.ppa``) append/drop the PPA's Launchpad source and
    # ``add_apt_repo_line``/``remove_apt_repo_line`` (the simulated
    # ``apt.repo``) append/drop the parsed source line, so ``AptSources``
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
    authorized_keys: dict[str, list[str]] = field(default_factory=dict)
    # Mounted block devices (device -> the ``BlockDevice`` entry the fact
    # returns -- {blocks, used, available, used_percent, mount}), backing
    # ``BlockDevices``. Each value is the parsed form the fact returns --
    # all five entries raw strings, as ``df`` prints them. Defaults to {}
    # -- a simulated machine with no devices listed, like pyinfra's
    # ``default = dict`` for a failed read.
    block_devices: dict[str, BlockDevice] = field(default_factory=dict)
    # CPU details (the parsed ``LANG=C lscpu`` rows), backing ``CpuInfo``:
    # each key maps to its stripped string value, except a ``flag``-bearing
    # key (e.g. ``Flags``) whose value is the list of whitespace-split tokens.
    # Defaults to {} -- a simulated machine reporting no lscpu rows; the real
    # transport reads None on a failed/empty read (pyinfra's ``invalid``
    # fixture), but the seam stays a non-None dict so it never falls through
    # to the shell.
    cpu_info: dict[str, str | list[str]] = field(default_factory=dict)
    # The number of online CPUs (the parsed ``getconf NPROCESSORS_ONLN``
    # answer), backing ``Cpus``. Defaults to pyinfra's own fact fixture value
    # (1) -- a real machine always answers ``getconf``, and a fixed default
    # keeps simulated reads deterministic.
    cpus: int = 1
    # The server's clock, backing ``Date``. Defaults to pyinfra's own fact
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
    # ``<dest>/.git`` so a repeat clone short-circuits. A test seeds any
    # other directory directly.
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
    # (``move_path``/``remove_tree``) relocates or drops a row so the ``File``
    # fact stays consistent across a simulated move.
    file_stats: dict[str, dict[str, Any] | bool] = field(default_factory=dict)
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
    # absent scope reads as [] (pyinfra's ``default = dict``, projected).
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
    # Group names present on the system (the first ``/etc/group`` field of
    # each row, in file order), backing ``Groups``. Named distinctly from
    # ``Host.group`` (the inventory classifier) so the two are never confused.
    # ``server.group`` mutates it: ``add_group`` appends a new name (file
    # order preserved) and ``remove_group`` drops it, so a repeat operation
    # is a no-op once the fresh per-step fact cache sees the change.
    os_groups: list[str] = field(default_factory=list)
    # Whether a known desktop-session binary is installed, backing
    # ``HasGui``. Defaults to False -- a simulated server is headless, like
    # pyinfra's gui_absent fixture (``ls /usr/bin/*session`` lists nothing).
    has_gui: bool = False
    # Home directories, backing ``Home``: username -> home directory path,
    # with "" keying the login user (the bare ``~``). An absent user reads as
    # None, like the shell failing to expand ``~user`` for an unknown user.
    home_directories: dict[str, str] = field(default_factory=dict)
    # The machine's hostname (``uname -n``), backing ``Hostname``. Named
    # ``os_hostname`` to stay distinct from ``Host.name`` (the inventory
    # label) and the inventory's ``connection.hostname`` address alias.
    # Defaults to pyinfra's own fact fixture hostname -- a real machine
    # always answers ``uname -n``, and a fixed default keeps simulated
    # reads deterministic. ``set_hostname`` (the simulated ``server.hostname``
    # effect) replaces it, so a repeat hostname set is a no-op.
    os_hostname: str = "my-host.net"
    # IPv4 addresses per network interface (the parsed `ip addr show` /
    # `ifconfig -a` listing), backing ``Ipv4Addrs``: interface name -> the
    # list of address strings the fact returns, primary first, with any
    # interfaces that carry no IPv4 already absent. Defaults to {} -- a
    # simulated machine with no interfaces listed, like pyinfra's
    # ``default = dict`` for a failed read.
    ipv4_addrs: dict[str, list[str]] = field(default_factory=dict)
    # IPv6 addresses per network interface (the parsed `ip addr show` /
    # `ifconfig -a` listing), backing ``Ipv6Addrs``: interface name -> the
    # list of address strings the fact returns, primary first, with any
    # interfaces that carry no IPv6 already absent. Defaults to {} -- a
    # simulated machine with no interfaces listed, like pyinfra's
    # ``default = dict`` for a failed read.
    ipv6_addrs: dict[str, list[str]] = field(default_factory=dict)
    # The kernel name (``uname -s``), backing ``Kernel``. Named ``os_kernel``
    # with the os_ prefix (like ``os_hostname``) so it stays clear of any
    # inventory field. Defaults to ``Linux`` -- a real machine always answers
    # ``uname -s``, and the simulated fleet is Linux servers (pyinfra's own
    # fixture value is the literal placeholder "os", not a real kernel name).
    os_kernel: str = "Linux"
    # Loaded kernel modules (module name -> info dict), backing
    # ``KernelModules``. Each value is the parsed form the fact returns: on
    # Linux {size, instances, state} plus an optional depends list, on
    # FreeBSD {id, refs, address, size} -- the seam holds whichever shape
    # matches the host's ``os_kernel``. Defaults to {} -- a simulated machine
    # with no modules listed, like pyinfra's ``default = dict`` for a failed
    # or absent /proc/modules read. ``server.modprobe`` mutates it:
    # ``load_modules`` adds a name with a minimal Linux-shaped info dict and
    # ``unload_modules`` drops it, so a repeat operation is a no-op once the
    # fresh per-step fact cache sees the change.
    kernel_modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The kernel release (``uname -r``), backing ``KernelVersion``. Named
    # ``os_kernel_version`` with the os_ prefix (like ``os_kernel``) so it
    # stays clear of any inventory field. Defaults to a real Fedora kernel
    # release -- a real machine always answers ``uname -r``, and the
    # simulated fleet is dnf-based Linux servers (pyinfra's own fixture
    # value is the literal placeholder "os_version", not a real release).
    os_kernel_version: str = "6.11.4-301.fc41.x86_64"
    # Symlink metadata (path -> the stat dict ``Link`` returns -- the same
    # shape as ``file_stats`` plus a ``link_target`` key -- or False for a
    # path that exists but is not a symlink), backing ``Link``. A separate
    # store from ``file_stats``/``directory_stats`` because a metadata dict
    # carries no type flag, so one store cannot answer the file-, directory-
    # and link-typed questions for the same path. Keys are the caller-supplied
    # path strings verbatim, ``~/...`` prefixes included; an absent key reads
    # as None, like a missing path. A read-only seam: no ``execute_command``
    # mutator maintains it (``make_link`` only fills the bare ``links`` store
    # ``FileLink`` reads), so a test sets it directly.
    link_stats: dict[str, dict[str, Any] | bool] = field(default_factory=dict)
    # Login records (the parsed ``last`` output), backing ``Last``. Each
    # entry is the dict the fact returns -- {user, tty, host, time} with
    # time the raw trailing string -- in ``last`` order. Defaults to [] --
    # a simulated machine with no recorded logins, like pyinfra's
    # ``default = list`` for an empty or failed ``last`` read.
    login_records: list[dict[str, str]] = field(default_factory=list)
    # The Linux distribution info (the parsed /etc/*-release dump), backing
    # ``LinuxDistribution``: {name, major, minor, release_meta} in the shape
    # the fact returns. ``LinuxName`` delegates to ``LinuxDistribution`` and
    # so reads this same seam -- there is deliberately no separate name
    # field, keeping the two facts consistent by construction. Defaults to
    # pyinfra's ``default()`` -- all None with an empty release_meta, the
    # "no release files matched" answer -- so a simulated machine makes no
    # distro claim until a test sets one.
    linux_distribution: dict[str, Any] = field(
        default_factory=lambda: {
            "name": None,
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
    # machine with no locales listed, like pyinfra's ``default = list`` for an
    # empty or failed ``locale -a`` read.
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
    # Mounted filesystems (mount point -> {device, type, options}), backing
    # ``Mounts``. Each value is the parsed form the fact returns, with the
    # options already combined and octal escapes decoded -- the seam holds
    # whichever kernel's listing the host's ``os_kernel`` implies. Defaults
    # to {} -- a simulated machine with no mounts listed, like pyinfra's
    # ``default = dict`` for a failed read. ``server.mount`` mutates it:
    # ``mount_filesystem`` records a new mount, ``remount_filesystem`` merges
    # the requested options into the entry and ``unmount_filesystem`` drops
    # the mount point, so the next step's fresh fact cache sees the change.
    mounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Network devices (interface -> the ``NetworkDevice`` entry the fact
    # returns -- {state, ether?, mtu?, ipv4?, ipv6?}), backing
    # ``NetworkDevices``. Each value is the parsed form the fact returns
    # -- an ipv4/ipv6 block holds the primary address plus its
    # mask/broadcast details and any ``additional_ips`` aliases. Defaults
    # to {} -- a simulated machine with no interfaces listed, like pyinfra's
    # ``default = dict`` for a failed read.
    network_devices: dict[str, NetworkDevice] = field(default_factory=dict)
    # The login user's PATH environment variable (the raw colon-separated
    # ``echo $PATH`` string), backing ``Path``. Named ``os_path`` with the
    # os_ prefix (like ``os_kernel``) so it stays clear of any inventory
    # field. Defaults to a standard Linux search path -- a real machine
    # always answers ``echo $PATH``, and a fixed default keeps simulated
    # reads deterministic (pyinfra ships no fixture for this fact).
    os_path: str = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin"
    # Running processes (PID -> {user, state, cpu_percent, mem_percent,
    # command, args}), backing ``Processes``. Each value is the parsed form
    # the fact returns -- the seam holds the joined record regardless of
    # which kernel's ps listing ``os_kernel`` implies. Defaults to {} -- a
    # simulated machine with no processes listed, like pyinfra's
    # ``default = dict`` for a failed ps read. ``server.kill`` mutates it:
    # ``kill_process`` drops the PID, so the next step's fresh fact cache
    # no longer lists the killed process.
    processes: dict[int, dict[str, Any]] = field(default_factory=dict)
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
    # pyinfra's own fact fixture timezone -- a real machine always resolves
    # ``/etc/localtime``, and a fixed default keeps simulated reads
    # deterministic. ``set_timezone`` (the simulated ``server.timezone``
    # effect) replaces it, so a repeat timezone set is a no-op.
    os_timezone: str = "Europe/Amsterdam"
    # The current user's name (the ``echo $USER`` answer), backing ``User``.
    # Named ``os_user`` with the os_ prefix (like ``os_path``) so it stays
    # clear of the inventory's ``connection.user`` login name. Defaults to
    # ``root`` -- a real machine always answers ``echo $USER``, and a fixed
    # default keeps simulated reads deterministic (pyinfra ships no fixture
    # for this fact).
    os_user: str = "root"
    # System users (username -> the details dict ``Users`` returns -- home,
    # comment, shell, group, groups, uid, gid, lastlog, login_time,
    # password), backing ``Users``. Named ``os_users`` with the os_ prefix
    # (like ``os_user``) so it stays clear of the inventory's
    # ``connection.user`` login name. Defaults to {} -- a simulated machine
    # with no users listed, like pyinfra's ``default = dict`` for a failed
    # or empty read.
    os_users: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The seconds since boot (the parsed ``cut -d. -f1 /proc/uptime`` answer,
    # or the BSD now-minus-boottime difference), backing ``Uptime``. Defaults
    # to pyinfra's own fact fixture uptime (86400 -- one day) -- a real
    # machine always answers with an uptime, and a fixed default keeps
    # simulated reads deterministic.
    uptime_seconds: int = 86400
    # Resolvable commands (command name -> the path ``command -v`` prints),
    # backing ``Which``. An absent command reads as None, like the swallowed
    # non-zero exit of ``command -v nosuchcommand || true``. Defaults to {} --
    # a simulated machine resolves no commands until a test names them.
    command_paths: dict[str, str] = field(default_factory=dict)
    # Sysctl settings (key -> the parsed value ``Sysctl`` returns -- an int,
    # a string, or a list of ints/strings), backing ``Sysctl``. A keys filter
    # selects the named settings; an absent key reads as missing, like the
    # swallowed ``sysctl nosuchkey`` error. Defaults to {} -- a simulated
    # machine with no settings listed, like pyinfra's ``default = dict`` for
    # an empty or failed read. A read-only seam: the demo-side ``sysctl``
    # step is a raw ``StringCommand`` with no simulated effect, so a test
    # mutates it directly to emulate a live value change.
    sysctls: dict[str, Any] = field(default_factory=dict)
    # Side effects of installing a package: package -> {link src: target}
    # and package -> {file path}. This is what lets the demo reproduce the
    # design doc section 9 trace: installing a package creates the default
    # site's link or ships files (e.g. httpd's welcome.conf), which the
    # *next* operation then sees because the fact cache is recreated per
    # step.
    installs_links: dict[str, dict[str, str]] = field(default_factory=dict)
    installs_files: dict[str, set[str]] = field(default_factory=dict)

    # Command seam: reemote5.drivers.send_command forwards here. The match
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
            case ApkUpgradeCommand():
                pass  # the simulation tracks no newer versions to upgrade to
            case ApkVirtualCommand(tag=tag, packages=packages, present=False):
                self.remove_apk_virtual(tag, packages)
            case ApkVirtualCommand(tag=tag, packages=packages):
                self.add_apk_virtual(tag, packages)
            case DnfInstallCommand(packages=packages):
                self.install_packages(packages)
            case DnfRemoveCommand(packages=packages):
                self.remove_packages(packages)
            case DnfUpdateCommand() | DnfCleanCommand():
                pass  # the simulation tracks no versions/metadata to update
            case DnfRepoUploadCommand(content=content, dest=dest):
                self.put_file(io.BytesIO(content.encode()), dest)
            case DnfRepoInstallCommand(spool=spool, dest=dest):
                self.install_repo_file(spool, dest)
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
            case AptRepoAddCommand(path=path, line=line):
                self.add_apt_repo_line(path, line)
            case AptRepoRemoveCommand(path=path, line=line):
                self.remove_apt_repo_line(path, line)
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
            case AptKeyUploadCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case AptKeyInstallCommand(spool=spool, dest=dest):
                self.install_apt_key(spool, dest)
            case DnfModuleEnableCommand(module=module, stream=stream):
                self.enable_module(module, stream)
            case DnfModuleDisableCommand(module=module):
                self.disable_module(module)
            case LinkCommand(src=src, target=None):
                self.remove_link(src)
            case LinkCommand(src=src, target=target):
                self.make_link(src, target)
            case FileCommand(src=src, present=False):
                self.remove_file(src)
            case FileCommand(src=src):
                self.create_file(src)
            case PutCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case GetCommand(src=src, dest=dest):
                self.get_file(src, dest)
            case MoveCommand(src=src, dest=dest):
                self.move_path(src, dest)
            case RemoveTreeCommand(src=src):
                self.remove_tree(src)
            case CopyCommand(src=src, dest=dest):
                self.copy_path(src, dest)
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
            case SystemctlCommand(action="start" | "restart", unit=unit):
                self.services[unit] = "active"
            case SystemctlCommand(action="stop", unit=unit):
                self.services[unit] = "inactive"
            case SystemctlCommand(action="enable", unit=unit):
                self.enabled_services.add(unit)
            case SystemctlCommand(action="disable", unit=unit):
                self.enabled_services.discard(unit)
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

    def install_packages(self, packages: tuple[str, ...]) -> None:
        for package in packages:
            # A version pin (``pkg=version``) records exactly that version in
            # the name -> versions store; an unpinned name carries no
            # repository metadata to resolve a version from, so it gets the
            # [""] placeholder (the ``install_apt_packages`` precedent). The
            # dnf.packages operation itself still compares names only.
            name, sep, version = package.rpartition("=")
            if not sep:
                name, version = package, ""
            if name in self.packages:
                continue
            self.packages[name] = [version]
            for src, target in self.installs_links.get(name, {}).items():
                self.links[src] = target
            for path in self.installs_files.get(name, set()):
                self.existing_files.add(path)

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
        gets the ``[""]`` placeholder pyinfra's own operation fixtures use
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
        exit non-zero, but the operation is non-idempotent and always yields).
        """
        url = self._ppa_url(src)
        self.apt_sources[:] = [s for s in self.apt_sources if s["url"] != url]

    def add_apt_repo_line(self, path: str, line: str) -> None:
        """Record a simulated ``echo <line> >> <path>``: append a source line.

        ``apt.repo``'s ``present=True`` mutation. The line (plus its
        newline) is appended to the ``remote_files`` content at ``path``,
        creating the file when absent, and the path is recorded in
        ``existing_files`` with a ``file_stats`` size refresh, so the
        content-read, existence and metadata facts all see the appended
        file consistently. The parsed ``AptSource`` (``AptSources``' legacy
        line parser) also lands in ``apt_sources`` unless an equal entry is
        already there, so a repeat ``apt.repo`` is a no-op once the fresh
        per-step fact cache sees the change. An unparseable line still
        appends to the file but contributes no sources entry -- the fact's
        parser skips it too.
        """
        data = self.remote_files.get(path, b"") + f"{line}\n".encode()
        self.remote_files[path] = data
        self.existing_files.add(path)
        stats = self.file_stats.get(path)
        if isinstance(stats, dict):
            stats["size"] = len(data)
        else:
            self.file_stats[path] = {
                "user": "root",
                "group": "root",
                "mode": 644,
                "size": len(data),
            }
        parsed = AptSources._parse_list_line(line)
        if parsed is not None and parsed not in self.apt_sources:
            self.apt_sources.append(parsed)

    def remove_apt_repo_line(self, path: str, line: str) -> None:
        """Record a simulated ``sed -i '/<pattern>/d'``: drop source lines.

        ``apt.repo``'s ``present=False`` mutation. Deletes every line
        matching the same anchored, escaped whole-line pattern the sed
        descriptor renders (``_repo_line_pattern``) from the
        ``remote_files`` content at ``path`` and refreshes the
        ``file_stats`` size; the parsed entry is also dropped from
        ``apt_sources`` so ``AptSources`` no longer reports the repo (the
        simulation does not track which file an entry came from, so an
        equal entry goes regardless of its file). An absent path or a
        line matching nothing is tolerated -- sed exits 0 when it deletes
        nothing.
        """
        parsed = AptSources._parse_list_line(line)
        if parsed is not None:
            self.apt_sources[:] = [s for s in self.apt_sources if s != parsed]
        data = self.remote_files.get(path)
        if data is None:
            return
        regex = re.compile(_repo_line_pattern(line))
        text = data.decode("utf-8")
        kept = [row for row in text.splitlines() if regex.match(row) is None]
        new = "\n".join(kept)
        if kept and text.endswith("\n"):
            new += "\n"
        self.remote_files[path] = new.encode()
        stats = self.file_stats.get(path)
        if isinstance(stats, dict):
            stats["size"] = len(self.remote_files[path])

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
        (``remove_apt_repo_line``'s tolerance). An absent spool path
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
        An absent spool path imports nothing.
        """
        data = self.remote_files.pop(path, None)
        if data is not None:
            self.imported_keys.add(data)

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

    def make_link(self, src: str, target: str) -> None:
        self.links[src] = target

    def remove_link(self, src: str) -> None:
        self.links.pop(src, None)

    def create_file(self, src: str) -> None:
        self.existing_files.add(src)

    def remove_file(self, src: str) -> None:
        self.existing_files.discard(src)

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

    def install_repo_file(self, spool: str, dest: str) -> None:
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
        mode 644 -- the real command's ``chmod 0644``). An absent spool path
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

        The overwrite branch of ``files.move`` removes the target before the
        move lands, so the content bytes (``remote_files``), the existence
        marker (``existing_files``) and any seeded ``file_stats`` row all go.
        """
        self.remote_files.pop(src, None)
        self.existing_files.discard(src)
        self.file_stats.pop(src, None)

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
        the freshly cloned repository (matching pyinfra's own fixture shape,
        a one-element list), so the origin-reconcile check reads equal on the
        second pass. ``git_branches[dest]`` becomes the requested ``branch``,
        or ``"main"`` when none was named -- the modern ``init.defaultBranch``
        a real clone resolves HEAD to -- so the branch-match check reads
        equal too. The clone's commit SHAs are deliberately not seeded:
        without a real remote there is nothing sensible to record in
        ``git_local_commits``/``git_remote_commits``, and pyinfra's own
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
        it), matching pyinfra, whose reset command only rewrites the fetch.
        """
        config = self.git_configs.setdefault(dest, {})
        config["remote.origin.url"] = [url]

    def checkout_branch(self, dest: str, branch: str) -> None:
        """Record a simulated ``git checkout``: switch the repository's HEAD.

        ``git.repo``'s branch-switch (and tag-detach) mutation. Writes
        ``branch`` into ``git_branches[dest]`` -- the value ``GitBranch``
        reads back -- so a repeat ``git.repo`` for the same target sees the
        current branch already equal and skips the fetch/checkout pair. A tag
        checkout writes the tag name into the same seam: pyinfra's
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
        else:
            raise TypeError(f"unsupported simulated wait fact: {cmd.fact_cls!r}")
        if not cmd.predicate(value):
            raise WaitTimeoutError(
                f"host {self.name}: {cmd} -- condition not met within "
                f"{cmd.timeout}s (last value: {value!r})"
            )


__all__ = ["SimulatedHost"]
