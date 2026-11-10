# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the dnf module: its facts and the operations layer.

Covers the read-only state discovery in ``reemote5.modules.dnf.dnf``:
``DnfRepositories``, ``DnfRepoList``, ``DnfEnabledModules``,
``DnfDisabledModules``, ``DnfInstalledModules``, ``DnfGroups``,
``DnfAvailableGroups``, ``RpmPackage`` and ``RpmGpgKeys``. Each fact is
exercised both over the real-transport path (parsing canned shell output) and
through the ``SimulatedHost`` seam.

Also covers the properties from the design document: request descriptor
semantics (4.1), fact caching (4.4), sequential "act, then read"
semantics in apply (8.2/9), cross-host lockstep (8.2), --dry behavior
(8.1), determinism checking (11.1) and change reporting (10).

The apply path (``send_command`` and the drivers calling it) is async in
preparation for an asyncssh transport, and so is fact gathering: a fact's
``process`` is a coroutine, making ``FactCache.get`` and the operations that
await it async too. ``asyncio_mode = "auto"`` lets the async tests below run
without decorators.
"""

import asyncio
import copy
import io
import shlex
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest

from reemote5.core import FactCache, OpContext, OperationRequest, Privilege, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import (
    CommandError,
    CommandNotFoundError,
    Host,
    InventoryError,
    validate_inventory,
)
from reemote5.modules import dnf, files
from reemote5.modules.dnf.dnf import (
    _KEY_IMPORT_PATH,
    _REPO_SPOOL_PATH,
    DnfAvailableGroups,
    DnfCleanCommand,
    DnfDisabledModules,
    DnfEnabledModules,
    DnfGroups,
    DnfInstallCommand,
    DnfInstalledModules,
    DnfKeyImportCommand,
    DnfKeyRemoveCommand,
    DnfKeyUploadCommand,
    DnfMakecacheCommand,
    DnfModuleDisableCommand,
    DnfModuleEnableCommand,
    DnfPackages,
    DnfRemoveCommand,
    DnfRepoFile,
    DnfRepoInstallCommand,
    DnfRepoList,
    DnfRepoRemoveCommand,
    DnfRepositories,
    DnfRepoUploadCommand,
    DnfUpdateCommand,
    RpmGpgKeys,
    RpmInstallCommand,
    RpmPackage,
)
from reemote5.modules.files.files import FileCommand, FileLink, LinkCommand
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout

    async def run_shell(self, shell: str) -> str:
        return self._stdout


def _module_entries(modules: dict[str, str]) -> list[dict[str, str]]:
    """Adapt a documented name->stream mapping to sorted ``DnfModule`` entries.

    Keeps the baseline's documented dict fixtures verbatim while the facts return
    the shared TypedDict entry list.
    """
    return [
        {"name": name, "stream": stream} for name, stream in sorted(modules.items())
    ]


def _group_entries(names: list[str]) -> list[dict[str, str]]:
    """Adapt documented group names to sorted ``DnfGroup`` entries."""
    return [{"name": name} for name in sorted(names)]


# --- DnfPackages fact ---------------------------------------------------------


async def test_dnf_packages_parses_rpm_rows_sorted_and_unique():
    """Over the real-transport path, ``DnfPackages`` parses ``rpm -qa``
    name-and-version rows into sorted ``DnfPackageInfo`` entries: blank lines
    dropped, surrounding whitespace stripped, duplicate rows collapsed,
    versions grouped per name and sorted."""
    host = CannedShellHost(
        "vim 9.0-1.el9\n"
        "nginx 1.24.0-1.el9\n"
        "\n"
        "nginx 1.24.0-1.el9\n"
        "  coreutils 8.32-1.el9  \n"
        "kernel 5.14.0-2.el9\n"
        "kernel 5.14.0-1.el9\n"
    )
    assert await DnfPackages.process(host) == [
        {"name": "coreutils", "versions": ["8.32-1.el9"]},
        {"name": "kernel", "versions": ["5.14.0-1.el9", "5.14.0-2.el9"]},
        {"name": "nginx", "versions": ["1.24.0-1.el9"]},
        {"name": "vim", "versions": ["9.0-1.el9"]},
    ]


def test_dnf_packages_parse_empty_output_is_an_empty_list():
    """A host with no packages parses to an empty list, not None."""
    assert DnfPackages.parse([]) == []


async def test_dnf_packages_seam_read_is_a_sorted_copy():
    """The seam answer is sorted and copy-on-read: mutating the returned
    entries (or their versions lists) never touches simulated state."""
    host = SimulatedHost(
        name="h", packages={"vim": ["9.0"], "nginx": ["1.24.0", "1.20.0"]}
    )
    result = await DnfPackages.process(host)
    assert result == [
        {"name": "nginx", "versions": ["1.20.0", "1.24.0"]},
        {"name": "vim", "versions": ["9.0"]},
    ]
    result[0]["versions"].append("mutated")
    result.append({"name": "mutated", "versions": []})
    assert host.packages == {"vim": ["9.0"], "nginx": ["1.24.0", "1.20.0"]}


# --- DnfRepositories fact (baseline parity) -----------------------------------

# The example result from the baseline ``dnf.DnfRepositories`` documentation: a
# single AlmaLinux BaseOS repository parsed out of its ``.repo`` file. The baseline
# flattens every option into the entry; reemote5 keeps the identifying fields
# fixed and moves the open-ended options into the ``options`` sub-dict.
ALMALINUX_BASEOS_REPO = {
    "repoid": "baseos",
    "name": "AlmaLinux $releasever - BaseOS",
    "filename": "/etc/yum.repos.d/almalinux.repo",
    "options": {
        "mirrorlist": "https://mirrors.almalinux.org/mirrorlist/$releasever/baseos",
        "enabled": "1",
        "gpgcheck": "1",
        "countme": "1",
        "gpgkey": "file:///etc/pki/rpm-gpg/RPM-GPG-KEY-AlmaLinux-9",
        "metadata_expire": "86400",
        "enabled_metadata": "1",
    },
}


async def test_dnf_repositories_parses_the_documented_example():
    """Over the real-transport path, ``DnfRepositories`` parses a cat'd
    ``.repo`` file into the baseline's documented example result."""
    marker = DnfRepositories.FILENAME_MARKER
    stdout = "\n".join(
        [
            f"{marker} /etc/yum.repos.d/almalinux.repo",
            "[baseos]",
            "name=AlmaLinux $releasever - BaseOS",
            "mirrorlist=https://mirrors.almalinux.org/mirrorlist/$releasever/baseos",
            "enabled=1",
            "gpgcheck=1",
            "countme=1",
            "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-AlmaLinux-9",
            "metadata_expire=86400",
            "enabled_metadata=1",
        ]
    )
    host = CannedShellHost(stdout)
    assert await DnfRepositories.process(host) == [ALMALINUX_BASEOS_REPO]


async def test_dnf_repositories_reads_the_simulated_seam():
    """A SimulatedHost exposes ``repositories`` directly, bypassing the shell."""
    host = SimulatedHost(name="h", repositories=[ALMALINUX_BASEOS_REPO])
    assert await DnfRepositories.process(host) == [ALMALINUX_BASEOS_REPO]


# --- DnfRepoFile fact ---------------------------------------------------------

# The ``.repo`` file dnf.repo constructs for a DockerCE repo, reused across the
# fact and operation tests below. ``$basearch`` must survive verbatim: the write
# shell-quotes the content, so the remote shell never expands it.
DOCKER_BASEURL = "https://download.docker.com/linux/centos/7/$basearch/stable"
DOCKER_REPO_PATH = "/etc/yum.repos.d/DockerCE.repo"
DOCKER_REPO_CONTENT = (
    "[DockerCE]\n"
    "name=DockerCE\n"
    f"baseurl={DOCKER_BASEURL}\n"
    "enabled=1\n"
    "gpgcheck=1\n"
)


async def test_dnf_repofile_reads_the_simulated_seam():
    """A SimulatedHost stores repo-file content in ``remote_files``; the fact
    decodes it back, and an absent path reads as None."""
    host = SimulatedHost(
        name="h", remote_files={DOCKER_REPO_PATH: DOCKER_REPO_CONTENT.encode()}
    )
    assert await DnfRepoFile.process(host, DOCKER_REPO_PATH) == DOCKER_REPO_CONTENT
    assert await DnfRepoFile.process(host, "/etc/yum.repos.d/absent.repo") is None


async def test_dnf_repofile_parses_the_marked_real_transport_output():
    """Over the real transport the fact strips the marker line and returns the
    file content verbatim (including the literal ``$basearch``)."""
    host = CannedShellHost(f"{DnfRepoFile.MARKER}\n{DOCKER_REPO_CONTENT}")
    assert await DnfRepoFile.process(host, DOCKER_REPO_PATH) == DOCKER_REPO_CONTENT


async def test_dnf_repofile_absent_without_the_marker():
    """A missing file produces no marker, so the fact reports None rather than
    empty content -- the distinction ``present=False`` removal relies on."""
    host = CannedShellHost("")
    assert await DnfRepoFile.process(host, DOCKER_REPO_PATH) is None


async def test_dnf_repofile_present_but_empty_returns_empty_string():
    """An existing empty file leads with the marker and no content, so the fact
    returns ``""`` (not None), distinguishing it from an absent file."""
    host = CannedShellHost(f"{DnfRepoFile.MARKER}\n")
    assert await DnfRepoFile.process(host, DOCKER_REPO_PATH) == ""


def test_dnf_repofile_command_guards_on_existence():
    """The real-transport command cats the file only behind a ``test -e`` guard
    and leads with the marker, so absence is distinguishable from empty."""
    command = DnfRepoFile.command(DOCKER_REPO_PATH)
    assert command.startswith(f"test -e {DOCKER_REPO_PATH}")
    assert DnfRepoFile.MARKER in command
    assert f"cat -- {DOCKER_REPO_PATH}" in command


# --- DnfEnabledModules fact (baseline parity) -----------------------------------

# The example result from the baseline ``dnf.DnfEnabledModules`` documentation,
# produced by parsing the ``dnf module list --enabled`` table below (the
# ``multiple_modules`` case from the baseline's own fact tests).
DOCUMENTED_ENABLED_MODULES = {"postgresql": "16", "nodejs": "20"}

DNF_MODULE_LIST_ENABLED_OUTPUT = """\
Name         Stream        Profiles                                  Summary
nodejs       20 [e]        common [d], development, minimal, s2i     Javascript runtime
postgresql   16 [e]        client, server [d]                        PostgreSQL server and client module

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled"""


async def test_dnf_enabled_modules_parses_the_documented_example():
    """Over the real-transport path, ``DnfEnabledModules`` parses a
    ``dnf module list --enabled`` table into the baseline's documented example
    result (as sorted ``DnfModule`` entries), skipping the header, blank
    lines and the trailing hint line."""
    host = CannedShellHost(DNF_MODULE_LIST_ENABLED_OUTPUT)
    assert await DnfEnabledModules.process(host) == _module_entries(
        DOCUMENTED_ENABLED_MODULES
    )


async def test_dnf_enabled_modules_reads_the_simulated_seam():
    """A SimulatedHost exposes the ``enabled_modules`` name->stream store
    directly, bypassing the shell; the fact projects it into entries."""
    host = SimulatedHost(name="h", enabled_modules=DOCUMENTED_ENABLED_MODULES)
    assert await DnfEnabledModules.process(host) == _module_entries(
        DOCUMENTED_ENABLED_MODULES
    )


# --- DnfDisabledModules fact (baseline parity) ---------------------------------

# The example result from the baseline ``dnf.DnfDisabledModules`` documentation
# lists ``["ruby", "php"]``; the fact returns ``DnfModule`` entries *sorted* by
# name, each keeping the stream its ``[x]`` row carries.
DOCUMENTED_DISABLED_MODULES = ["php", "ruby"]

DNF_MODULE_LIST_DISABLED_OUTPUT = """\
Name         Stream        Profiles              Summary
php          8.2 [x]       common [d]            PHP scripting language
ruby         3.1 [x]       common [d]            An interpreter of object-oriented scripting language

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled"""


async def test_dnf_disabled_modules_parses_the_documented_example():
    """Over the real-transport path, ``DnfDisabledModules`` parses a
    ``dnf module list --disabled`` table into the baseline's documented example
    modules (sorted entries keeping each row's stream), skipping the header,
    blank lines and the trailing hint line."""
    host = CannedShellHost(DNF_MODULE_LIST_DISABLED_OUTPUT)
    assert await DnfDisabledModules.process(host) == [
        {"name": "php", "stream": "8.2"},
        {"name": "ruby", "stream": "3.1"},
    ]


async def test_dnf_disabled_modules_keeps_each_flagged_stream():
    """A module disabled on several streams contributes one entry per flagged
    row, sorted by name then stream (the ``multi_stream_dedup`` case from
    the baseline's fact tests, which collapsed the streams into one name)."""
    stdout = """\
Name         Stream        Profiles              Summary
postgresql   16 [x]        client, server [d]    PostgreSQL server and client module
postgresql   15 [x]        client, server [d]    PostgreSQL server and client module
ruby         3.1 [x]       common [d]            An interpreter of object-oriented scripting language

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled"""
    host = CannedShellHost(stdout)
    assert await DnfDisabledModules.process(host) == [
        {"name": "postgresql", "stream": "15"},
        {"name": "postgresql", "stream": "16"},
        {"name": "ruby", "stream": "3.1"},
    ]


async def test_dnf_disabled_modules_reads_the_simulated_seam():
    """A SimulatedHost exposes the ``disabled_modules`` name store directly,
    bypassing the shell; the fact projects it into entries with stream ""
    (a simulated disable names no stream)."""
    host = SimulatedHost(name="h", disabled_modules=DOCUMENTED_DISABLED_MODULES)
    assert await DnfDisabledModules.process(host) == [
        {"name": "php", "stream": ""},
        {"name": "ruby", "stream": ""},
    ]


# --- DnfInstalledModules fact -------------------------------------------------

# ``dnf module list --installed`` marks installed streams with ``[i]``; the
# fact turns each such row into a ``DnfModule`` entry, ignoring the header,
# blank lines and the trailing ``Hint:`` line (whose ``[i]nstalled`` word must
# not be mistaken for a flag).
DOCUMENTED_INSTALLED_MODULES = {"postgresql": "16", "nodejs": "20"}

DNF_MODULE_LIST_INSTALLED_OUTPUT = """\
Name         Stream        Profiles                                  Summary
nodejs       20 [i]        common [d], development, minimal, s2i     Javascript runtime
postgresql   16 [i]        client, server [d]                        PostgreSQL server and client module

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled"""


async def test_dnf_installed_modules_parses_the_installed_table():
    """Over the real-transport path, ``DnfInstalledModules`` parses a
    ``dnf module list --installed`` table into sorted ``DnfModule`` entries,
    keeping only the ``[i]``-flagged rows and skipping the header and hint
    line."""
    host = CannedShellHost(DNF_MODULE_LIST_INSTALLED_OUTPUT)
    assert await DnfInstalledModules.process(host) == _module_entries(
        DOCUMENTED_INSTALLED_MODULES
    )


async def test_dnf_installed_modules_ignores_the_hint_and_headers():
    """The trailing ``Hint:`` line's ``[i]nstalled`` word carries no bare ``[i]``
    flag, so it is not parsed as a module row."""
    host = CannedShellHost(DNF_MODULE_LIST_INSTALLED_OUTPUT)
    modules = await DnfInstalledModules.process(host)
    assert all(entry["name"] != "Hint:" for entry in modules)
    assert all(entry["stream"].isdigit() for entry in modules)


async def test_dnf_installed_modules_reads_the_simulated_seam():
    """A SimulatedHost exposes the ``installed_modules`` name->stream store
    directly, bypassing the shell; the fact projects it into entries."""
    host = SimulatedHost(name="h", installed_modules=DOCUMENTED_INSTALLED_MODULES)
    assert await DnfInstalledModules.process(host) == _module_entries(
        DOCUMENTED_INSTALLED_MODULES
    )


# --- DnfGroups / DnfAvailableGroups facts ------------------------------------

# ``dnf group list --installed`` prints section headers at column 0 and lists
# each group name on its own indented line beneath. Environment groups and
# regular groups are gathered together.
DNF_GROUP_LIST_INSTALLED_OUTPUT = """\
Installed Environment Groups:
   Server with GUI
   Minimal Install
Installed Groups:
   Core
   Development Tools
   Container Management"""

DOCUMENTED_INSTALLED_GROUPS = [
    "Container Management",
    "Core",
    "Development Tools",
    "Minimal Install",
    "Server with GUI",
]

# An unfiltered ``dnf group list`` interleaves Available and Installed
# sections; each fact must keep only its own section's names.
DNF_GROUP_LIST_MIXED_OUTPUT = """\
Available Environment Groups:
   KDE Plasma Workspaces
Available Groups:
   3D Printing
   Audio Production
Installed Environment Groups:
   Server with GUI
Installed Groups:
   Core"""


async def test_dnf_groups_parses_the_installed_table():
    """Over the real-transport path, ``DnfGroups`` collects every indented name
    under an ``Installed ... Groups:`` header, environment groups included."""
    host = CannedShellHost(DNF_GROUP_LIST_INSTALLED_OUTPUT)
    assert await DnfGroups.process(host) == _group_entries(DOCUMENTED_INSTALLED_GROUPS)


async def test_dnf_groups_ignores_available_sections():
    """In a mixed listing, ``DnfGroups`` keeps only the ``Installed`` names."""
    host = CannedShellHost(DNF_GROUP_LIST_MIXED_OUTPUT)
    assert await DnfGroups.process(host) == _group_entries(["Core", "Server with GUI"])


async def test_dnf_groups_reads_the_simulated_seam():
    """A SimulatedHost exposes the ``installed_groups`` name store directly,
    bypassing the shell; the fact projects it into sorted entries."""
    host = SimulatedHost(name="h", installed_groups=set(DOCUMENTED_INSTALLED_GROUPS))
    assert await DnfGroups.process(host) == _group_entries(DOCUMENTED_INSTALLED_GROUPS)


async def test_dnf_available_groups_parses_the_available_table():
    """``DnfAvailableGroups`` collects the names under ``Available ... Groups:``
    headers from a ``dnf group list --available`` listing."""
    stdout = """\
Available Environment Groups:
   Fedora Custom Operating System
   Minimal Install
Available Groups:
   3D Printing
   Administration Tools
   Authoring and Publishing"""
    host = CannedShellHost(stdout)
    assert await DnfAvailableGroups.process(host) == _group_entries(
        [
            "3D Printing",
            "Administration Tools",
            "Authoring and Publishing",
            "Fedora Custom Operating System",
            "Minimal Install",
        ]
    )


async def test_dnf_available_groups_ignores_installed_sections():
    """In a mixed listing, ``DnfAvailableGroups`` keeps only the ``Available``
    names."""
    host = CannedShellHost(DNF_GROUP_LIST_MIXED_OUTPUT)
    assert await DnfAvailableGroups.process(host) == _group_entries(
        [
            "3D Printing",
            "Audio Production",
            "KDE Plasma Workspaces",
        ]
    )


async def test_dnf_available_groups_reads_the_simulated_seam():
    """A SimulatedHost exposes the ``available_groups`` name store directly,
    bypassing the shell; the fact projects it into sorted entries."""
    host = SimulatedHost(name="h", available_groups={"3D Printing", "Core"})
    assert await DnfAvailableGroups.process(host) == _group_entries(
        ["3D Printing", "Core"]
    )


# --- DnfRepoList fact ---------------------------------------------------------

# ``dnf repolist --all`` prints three columns -- repo id, a possibly multi-word
# repo name, and an enabled/disabled status -- under a ``repo id`` header row.
DNF_REPOLIST_ALL_OUTPUT = """\
repo id                 repo name                                            status
baseos                  AlmaLinux 9 - BaseOS                                 enabled
appstream               AlmaLinux 9 - AppStream                              enabled
epel                    Extra Packages for Enterprise Linux 9 - x86_64       disabled"""

DOCUMENTED_REPO_LIST = [
    {"repoid": "baseos", "name": "AlmaLinux 9 - BaseOS", "status": "enabled"},
    {"repoid": "appstream", "name": "AlmaLinux 9 - AppStream", "status": "enabled"},
    {
        "repoid": "epel",
        "name": "Extra Packages for Enterprise Linux 9 - x86_64",
        "status": "disabled",
    },
]


async def test_dnf_repolist_parses_the_all_table():
    """Over the real-transport path, ``DnfRepoList`` splits each row into
    ``repoid``, the multi-word ``name`` and the trailing ``status``, skipping
    the header row."""
    host = CannedShellHost(DNF_REPOLIST_ALL_OUTPUT)
    assert await DnfRepoList.process(host) == DOCUMENTED_REPO_LIST


async def test_dnf_repolist_parses_a_two_column_listing_without_status():
    """A plain ``dnf repolist`` (no ``--all``) has no status column; the fact
    still parses it, simply omitting the ``status`` key."""
    stdout = """\
repo id            repo name
baseos             AlmaLinux 9 - BaseOS
appstream          AlmaLinux 9 - AppStream"""
    host = CannedShellHost(stdout)
    assert await DnfRepoList.process(host) == [
        {"repoid": "baseos", "name": "AlmaLinux 9 - BaseOS"},
        {"repoid": "appstream", "name": "AlmaLinux 9 - AppStream"},
    ]


async def test_dnf_repolist_reads_the_simulated_seam():
    """A SimulatedHost exposes ``repo_list`` directly, bypassing the shell."""
    host = SimulatedHost(name="h", repo_list=DOCUMENTED_REPO_LIST)
    assert await DnfRepoList.process(host) == DOCUMENTED_REPO_LIST


async def test_dnf_repolist_seam_read_copies_each_entry():
    """The seam answer copies every entry dict, so mutating the result never
    touches simulated state."""
    host = SimulatedHost(name="h", repo_list=DOCUMENTED_REPO_LIST)
    result = await DnfRepoList.process(host)
    result[0]["repoid"] = "mutated"
    assert host.repo_list == DOCUMENTED_REPO_LIST


# --- RpmPackage fact (baseline parity) ----------------------------------------

# A ``.rpm`` file on the target host and the package it provides, reused across
# the RpmPackage fact and dnf.rpm operation tests below. ``RPM_FILE_VERSION`` is
# rpm's ``VERSION-RELEASE`` (what ``%{VERSION}-%{RELEASE}`` prints).
RPM_FILE_PATH = "/tmp/foo-1.0-1.el9.x86_64.rpm"
RPM_FILE_NAME = "foo"
RPM_FILE_VERSION = "1.0-1.el9"
RPM_FILE_INFO = {"name": RPM_FILE_NAME, "version": RPM_FILE_VERSION}


async def test_rpm_package_parses_the_queryformat_line():
    """Over the real-transport path, ``RpmPackage`` parses rpm's
    ``NAME VERSION-RELEASE`` line into the baseline's documented dict."""
    host = CannedShellHost(f"{RPM_FILE_NAME} {RPM_FILE_VERSION}\n")
    assert await RpmPackage.process(host, RPM_FILE_NAME) == RPM_FILE_INFO


async def test_rpm_package_absent_name_parses_to_none():
    """rpm's ``package <name> is not installed`` message has more than two
    fields, so the two-token regex rejects it and the fact reports None."""
    host = CannedShellHost(f"package {RPM_FILE_NAME} is not installed\n")
    assert await RpmPackage.process(host, RPM_FILE_NAME) is None


def test_rpm_package_command_queries_installed_then_file():
    """The real-transport command queries the installed package first, then --
    behind a ``test -e`` guard -- the ``.rpm`` file, mirroring the baseline's
    ``rpm -q ... || rpm -qp ...`` chain."""
    command = RpmPackage.command(RPM_FILE_PATH)
    assert "--queryformat" in command
    assert f"-q {RPM_FILE_PATH}" in command
    assert f"! test -e {RPM_FILE_PATH}" in command
    assert f"-qp {RPM_FILE_PATH}" in command


async def test_rpm_package_reads_the_simulated_seam():
    """A SimulatedHost exposes ``rpm_packages`` (installed name -> version) and
    ``rpm_files`` (path -> metadata); both are readable and an unknown key
    reads as None."""
    host = SimulatedHost(
        name="h",
        rpm_packages={RPM_FILE_NAME: RPM_FILE_VERSION},
        rpm_files={RPM_FILE_PATH: RPM_FILE_INFO},
    )
    assert await RpmPackage.process(host, RPM_FILE_NAME) == RPM_FILE_INFO
    assert await RpmPackage.process(host, RPM_FILE_PATH) == RPM_FILE_INFO
    assert await RpmPackage.process(host, "absent") is None


NGINX_DEFAULT_SITE = "/etc/nginx/sites-enabled/default"
DEFAULT_TARGET = "/etc/nginx/sites-available/default"
HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"


# --- Test setup: example inventory, deploy script and host factory ----------
#
# The library provides the execution model (drivers, core, operations,
# facts, Host) and accepts the inventory as a parameter. SimulatedHost is
# part of the test framework: it lives in tests/simulated_host.py (never in
# src/reemote5/) and is injected via ``run``'s ``host_factory`` argument,
# never constructed by the library. The specific nginx deploy script and
# the web1/web2 inventory and initial state below are test fixtures, not
# part of the library.

WEB_INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

# Simulated pre-deployment state per host name (test-framework data, NOT
# inventory): web1 fresh, web2 already configured (design doc section 9).
WEB_INITIAL_STATE = {
    "web1": {"installs_links": {"nginx": {NGINX_DEFAULT_SITE: DEFAULT_TARGET}}},
    "web2": {
        "packages": {"nginx": [""]},
        "links": {NGINX_DEFAULT_SITE: DEFAULT_TARGET},
        # ``files.link`` now stats the path through the ``Link`` fact, so
        # the pre-deployment link needs its stat row alongside the target.
        "link_stats": {
            NGINX_DEFAULT_SITE: {
                "mode": 777,
                "user": "root",
                "group": "root",
                "link_target": DEFAULT_TARGET,
            }
        },
    },
}


def simulated_web_factory(entry: dict) -> SimulatedHost:
    """``host_factory`` building SimulatedHosts with the section-9 state."""
    host = SimulatedHost.from_inventory(entry)
    # Deep-copy: send_command mutates the host's dicts in place, which must
    # not leak back into the shared fixture.
    for attr, value in WEB_INITIAL_STATE.get(host.name, {}).items():
        setattr(host, attr, copy.deepcopy(value))
    return host


def deploy(host: Host) -> Generator[OperationRequest]:
    """Example deploy script (design doc section 7/9).

    Install nginx, then remove its default site. Branch-free, so it is
    deterministic across the prepare and apply phases.
    """
    yield op(dnf.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        path=NGINX_DEFAULT_SITE,
        present=False,
    )


# --- Inventory / Host (library input) ----------------------------------------


def test_host_from_inventory_maps_known_and_extra_keys():
    host = Host.from_inventory(
        {
            "host": "web1",
            "connection": {"key_path": "~/.ssh/id_ed25519"},
            "privilege": {"sudo_user": "deploy"},
            "groups": ["web"],  # unrecognised key -> preserved in extra
        }
    )
    assert host.name == "web1"
    assert host.connection == {"key_path": "~/.ssh/id_ed25519"}
    assert host.privilege == {"sudo_user": "deploy"}
    assert host.extra == {"groups": ["web"]}


def test_host_from_inventory_maps_group_and_tags():
    host = Host.from_inventory(
        {
            "host": "web1",
            "connection": {},
            "group": "webservers",
            "tags": ["ubuntu_host", "web"],
        }
    )
    assert host.group == "webservers"
    assert host.tags == ["ubuntu_host", "web"]


def test_host_group_and_tags_default_when_absent():
    host = Host.from_inventory({"host": "web1", "connection": {}})
    assert host.group is None
    assert host.tags == []


def test_from_inventory_rejects_non_string_tags():
    with pytest.raises(InventoryError):
        Host.from_inventory({"host": "web1", "connection": {}, "tags": [1, 2]})


def test_deploy_branches_on_group_and_tags():
    """A deploy generator can select work from the host's static classifiers."""
    host = Host.from_inventory(
        {
            "host": "web1",
            "connection": {},
            "group": "webservers",
            "tags": ["ubuntu_host", "web"],
        }
    )

    def deploy(h: Host):
        if h.group == "webservers":
            yield op(dnf.packages, name="group role", packages=("nginx",))
        if "ubuntu_host" in h.tags:
            yield op(dnf.packages, name="tag role", packages=("vim",))

    names = [request.name for request in deploy(host)]
    assert names == ["group role", "tag role"]


async def test_inventory_host_reaches_the_real_transport_boundary():
    """A plain Host carries its connection data into facts and operations,
    where the missing real SSH transport is reported explicitly."""
    host = Host.from_inventory(WEB_INVENTORY[0])
    assert host.connection == {"key_path": "~/.ssh/id_ed25519"}
    with pytest.raises(NotImplementedError):
        await DnfPackages.process(host)
    with pytest.raises(NotImplementedError):
        await send_command(host, DnfInstallCommand(packages=("nginx",)))


def test_validate_inventory_accepts_a_wellformed_inventory():
    entries = validate_inventory(WEB_INVENTORY)
    assert [entry.host for entry in entries] == ["web1", "web2"]
    assert entries[0].connection == {"key_path": "~/.ssh/id_ed25519"}


def test_validate_inventory_requires_a_list():
    with pytest.raises(InventoryError, match="must be a list"):
        validate_inventory({"host": "web1", "connection": {}})


def test_validate_inventory_reports_the_offending_entry_index():
    # The TypeAdapter reports every bad entry by its list index in the error
    # ``loc`` (here entry 1 is missing ``connection``).
    with pytest.raises(InventoryError, match=r"1\.connection"):
        validate_inventory([{"host": "web1", "connection": {}}, {"host": "web2"}])


def test_from_inventory_requires_a_connection_key():
    with pytest.raises(InventoryError, match="connection"):
        Host.from_inventory({"host": "web1"})


def test_from_inventory_requires_a_host_key():
    with pytest.raises(InventoryError, match="host"):
        Host.from_inventory({"connection": {}})


def test_from_inventory_rejects_a_non_dict_entry():
    with pytest.raises(InventoryError, match="must be a dictionary"):
        Host.from_inventory("web1")


def test_connect_options_forwards_asyncssh_named_options():
    """Recognised asyncssh key names pass through verbatim; path options expand."""
    host = Host(
        name="web1",
        connection={
            "address": "10.0.0.1",
            "user": "kjarvis",
            "port": 2222,
            "key_path": "~/keys/id_ed25519",
            "proxy_command": "ssh -W %h:%p bastion",
            "known_hosts": None,
            "keepalive_interval": 30,
            "preferred_auth": ["publickey", "password"],
        },
    )
    address, kwargs = host._connect_options()
    assert address == "10.0.0.1"
    assert kwargs["username"] == "kjarvis"
    assert kwargs["port"] == 2222
    assert kwargs["proxy_command"] == "ssh -W %h:%p bastion"
    assert kwargs["keepalive_interval"] == 30
    assert kwargs["preferred_auth"] == ["publickey", "password"]
    # ``key_path`` is a single-key shorthand, expanded to an absolute path.
    assert kwargs["client_keys"] == [str(Path("~/keys/id_ed25519").expanduser())]
    # A key present but set to ``None`` is forwarded verbatim, so an explicit
    # ``known_hosts=None`` reaches asyncssh and disables host key checking.
    assert kwargs["known_hosts"] is None


def test_connect_options_merges_passthrough_last():
    """``asyncssh_connect_kwargs`` reaches asyncssh and may override options."""
    host = Host(
        name="web1",
        connection={
            "address": "10.0.0.1",
            "port": 22,
            "asyncssh_connect_kwargs": {"port": 2222, "family": 0},
        },
    )
    _address, kwargs = host._connect_options()
    assert kwargs["port"] == 2222  # passthrough wins
    assert kwargs["family"] == 0  # non-first-class option still forwarded


def test_connect_options_rejects_forbidden_kbdint_auth():
    """``kbdint_auth`` is expressly forbidden in the connection mapping."""
    host = Host(
        name="web1",
        connection={"address": "10.0.0.1", "kbdint_auth": True},
    )
    with pytest.raises(InventoryError, match="kbdint_auth"):
        host._connect_options()


def test_connect_options_rejects_forbidden_option_in_passthrough():
    """A forbidden option cannot be smuggled through ``asyncssh_connect_kwargs``."""
    host = Host(
        name="web1",
        connection={
            "address": "10.0.0.1",
            "asyncssh_connect_kwargs": {"kbdint_auth": True},
        },
    )
    with pytest.raises(InventoryError, match="kbdint_auth"):
        host._connect_options()


# --- OperationRequest (4.1) -------------------------------------------------


def test_operation_request_kwargs_are_copied():
    kwargs = {"packages": ("nginx",)}
    request = OperationRequest(func=dnf.packages, kwargs=kwargs, name="Install nginx")
    kwargs["packages"] = ("vim",)  # mutating the caller's dict...
    assert request.kwargs["packages"] == ("nginx",)  # ...must not leak in


def test_operation_request_value_equality_and_hashing():
    a = op(dnf.packages, name="Install nginx", packages=("nginx",))
    b = op(dnf.packages, name="Install nginx", packages=("nginx",))
    assert a == b
    assert hash(a) == hash(b)
    assert a != op(dnf.packages, name="Install nginx", packages=("vim",))


def test_op_sudo_is_a_meta_parameter_not_forwarded_to_the_operation():
    request = op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)
    # ``sudo`` is captured on the request, never mixed into the operation's
    # kwargs (dnf.packages has no ``sudo`` parameter).
    assert request.sudo is Privilege.ON
    assert "sudo" not in request.kwargs
    assert request.kwargs == {"packages": ("nginx",)}
    # Unflagged by default: "no opinion", not a deliberate opt-out.
    assert op(dnf.packages, packages=("nginx",)).sudo is Privilege.UNSET


def test_sudo_participates_in_request_equality_and_hashing():
    plain = op(dnf.packages, name="Install nginx", packages=("nginx",))
    elevated = op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)
    other_elevated = op(
        dnf.packages, name="Install nginx", packages=("nginx",), sudo=True
    )
    assert plain != elevated  # a sudo difference must not pair in lockstep
    assert elevated == other_elevated
    assert hash(elevated) == hash(other_elevated)


def test_op_su_is_a_meta_parameter_not_forwarded_to_the_operation():
    request = op(dnf.packages, name="Install nginx", packages=("nginx",), su=True)
    # ``su`` is captured on the request, never mixed into the operation's
    # kwargs (dnf.packages has no ``su`` parameter).
    assert request.su is Privilege.ON
    assert "su" not in request.kwargs
    assert request.kwargs == {"packages": ("nginx",)}
    assert op(dnf.packages, packages=("nginx",)).su is Privilege.UNSET


def test_su_participates_in_request_equality_and_hashing():
    plain = op(dnf.packages, name="Install nginx", packages=("nginx",))
    elevated = op(dnf.packages, name="Install nginx", packages=("nginx",), su=True)
    other = op(dnf.packages, name="Install nginx", packages=("nginx",), su=True)
    assert plain != elevated  # a su difference must not pair in lockstep
    assert elevated == other
    assert hash(elevated) == hash(other)
    # sudo and su are independent flags: a request differs on either.
    sudo_only = op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)
    assert sudo_only != elevated


def test_op_doas_is_a_meta_parameter_not_forwarded_to_the_operation():
    request = op(dnf.packages, name="Install nginx", packages=("nginx",), doas=True)
    # ``doas`` is captured on the request, never mixed into the operation's
    # kwargs (dnf.packages has no ``doas`` parameter).
    assert request.doas is Privilege.ON
    assert "doas" not in request.kwargs
    assert request.kwargs == {"packages": ("nginx",)}
    assert op(dnf.packages, packages=("nginx",)).doas is Privilege.UNSET


def test_doas_participates_in_request_equality_and_hashing():
    plain = op(dnf.packages, name="Install nginx", packages=("nginx",))
    elevated = op(dnf.packages, name="Install nginx", packages=("nginx",), doas=True)
    other = op(dnf.packages, name="Install nginx", packages=("nginx",), doas=True)
    assert plain != elevated  # a doas difference must not pair in lockstep
    assert elevated == other
    assert hash(elevated) == hash(other)
    # doas is independent of sudo and su: a request differs on any of them.
    sudo_only = op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)
    su_only = op(dnf.packages, name="Install nginx", packages=("nginx",), su=True)
    assert sudo_only != elevated
    assert su_only != elevated


# --- sudo elevation on the command path --------------------------------------


def test_command_shell_applies_the_sudo_prefix():
    cmd = DnfInstallCommand(packages=("nginx",))
    assert command_shell(cmd) == "dnf install -y nginx"
    assert command_shell(cmd, sudo=True) == "sudo dnf install -y nginx"
    # A run-as user is emitted as ``sudo -u <user>`` (shell-quoted)...
    assert (
        command_shell(cmd, sudo=True, sudo_user="deploy")
        == "sudo -u deploy dnf install -y nginx"
    )
    # ``root`` is sudo's own default target, so it collapses to a bare ``sudo``.
    assert (
        command_shell(cmd, sudo=True, sudo_user="root")
        == "sudo dnf install -y nginx"
    )
    # ...but only when elevation was actually requested.
    assert command_shell(cmd, sudo_user="deploy") == "dnf install -y nginx"


async def test_send_command_prefixes_sudo_on_the_real_transport_path():
    """A plain Host (no ``execute_command`` seam) gets the elevated shell."""

    class RecordingHost(Host):
        """Records the transport seam instead of opening a real connection."""

        def __init__(self):
            super().__init__(name="rec")
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost()
    await send_command(host, DnfInstallCommand(packages=("nginx",)), sudo=True)
    await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert host.sent == ["sudo dnf install -y nginx", "dnf install -y nginx"]


async def test_send_command_sources_sudo_user_from_host_privilege():
    """The run-as user comes from the inventory ``privilege`` section, never
    from the operation -- the op only carries ``sudo=True``."""

    class RecordingHost(Host):
        def __init__(self, privilege):
            super().__init__(name="rec", privilege=privilege)
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost({"sudo_user": "deploy"})
    await send_command(host, DnfInstallCommand(packages=("nginx",)), sudo=True)
    # Unprivileged: no elevation even though a sudo_user is configured.
    await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert host.sent == [
        "sudo -u deploy dnf install -y nginx",
        "dnf install -y nginx",
    ]


class _AskpassRecordingHost(Host):
    """Records the transport seam and fakes the askpass-setup round-trip.

    Stands in for the real transport so the askpass command construction can be
    asserted without an SSH connection: the ``_ASKPASS_SETUP`` call (detected by
    its ``mktemp``) returns a canned helper path, everything else empty stdout.
    """

    ASKPASS_PATH = "/tmp/reemote5-askpass-abc123"

    def __init__(self, privilege):
        super().__init__(name="rec", privilege=privilege)
        self.sent: list[str] = []

    async def _run_shell(self, shell: str):
        self.sent.append(shell)
        stdout = f"{self.ASKPASS_PATH}\n" if "mktemp" in shell else ""
        return SimpleNamespace(exit_status=0, stdout=stdout, stderr="")


async def test_run_shell_sudo_uses_askpass_when_password_configured():
    """A ``sudo_password`` in privilege runs the command under ``sudo -A`` with
    the generated askpass helper, the password supplied via the environment (no
    TTY), and the run-as user still applied."""
    host = _AskpassRecordingHost({"sudo_user": "deploy", "sudo_password": "s3cr3t"})
    await send_command(host, DnfInstallCommand(packages=("nginx",)), sudo=True)
    # First round-trip writes the helper, second runs the elevated command.
    setup, elevated = host.sent
    assert "mktemp" in setup
    assert elevated == (
        "env SUDO_ASKPASS=/tmp/reemote5-askpass-abc123 "
        "REEMOTE5_SUDO_PASSWORD=s3cr3t "
        "sudo -A -k -u deploy dnf install -y nginx"
    )


async def test_run_shell_sudo_collapses_the_root_run_as_user_to_a_bare_sudo():
    """``sudo_user: root`` is ``sudo``'s own default target, so it collapses to
    a bare ``sudo`` (no ``-u root``): the login user is already in sudoers and
    ``sudo`` elevates to root on its own."""
    host = _AskpassRecordingHost({"sudo_user": "root", "sudo_password": "s3cr3t"})
    await send_command(host, DnfInstallCommand(packages=("nginx",)), sudo=True)
    _setup, elevated = host.sent
    assert elevated == (
        "env SUDO_ASKPASS=/tmp/reemote5-askpass-abc123 "
        "REEMOTE5_SUDO_PASSWORD=s3cr3t "
        "sudo -A -k dnf install -y nginx"
    )
    assert "-u root" not in elevated


async def test_run_shell_sudo_creates_the_askpass_helper_once():
    """The helper is cached per host: two elevated commands write it once."""
    host = _AskpassRecordingHost({"sudo_password": "pw"})
    await send_command(host, DnfInstallCommand(packages=("a",)), sudo=True)
    await send_command(host, DnfInstallCommand(packages=("b",)), sudo=True)
    assert sum("mktemp" in s for s in host.sent) == 1


async def test_run_shell_sudo_shell_quotes_the_password():
    """A password with shell metacharacters is quoted, not interpolated raw."""
    host = _AskpassRecordingHost({"sudo_password": "p@ss w0rd';rm"})
    await send_command(host, DnfInstallCommand(packages=("a",)), sudo=True)
    elevated = host.sent[-1]
    assert shlex.quote("p@ss w0rd';rm") in elevated
    assert elevated.endswith("sudo -A -k dnf install -y a")


# --- su elevation on the command path ----------------------------------------


def test_command_shell_applies_the_su_prefix():
    cmd = DnfInstallCommand(packages=("nginx",))
    # Default su runs ``su -c``; su's own default target (root) collapses to a
    # bare ``su -c`` rather than naming root.
    assert command_shell(cmd, su=True) == "su -c 'dnf install -y nginx'"
    assert command_shell(cmd, su=True, su_user="root") == "su -c 'dnf install -y nginx'"
    # A non-root target is named.
    assert (
        command_shell(cmd, su=True, su_user="deploy")
        == "su deploy -c 'dnf install -y nginx'"
    )
    # ...but only when su was actually requested.
    assert command_shell(cmd, su_user="deploy") == "dnf install -y nginx"


def test_command_shell_combines_sudo_then_su_in_order():
    """When both flags are set the order is sudo -> su: sudo is the outer
    prefix and su runs inside it."""
    cmd = DnfInstallCommand(packages=("nginx",))
    assert command_shell(cmd, sudo=True, su=True) == "sudo su -c 'dnf install -y nginx'"
    assert (
        command_shell(cmd, sudo=True, sudo_user="deploy", su=True, su_user="app")
        == "sudo -u deploy su app -c 'dnf install -y nginx'"
    )


async def test_send_command_prefixes_su_on_the_real_transport_path():
    class RecordingHost(Host):
        def __init__(self, privilege=None):
            super().__init__(name="rec", privilege=privilege or {})
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost()
    await send_command(host, DnfInstallCommand(packages=("nginx",)), su=True)
    await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert host.sent == [
        "su -c 'dnf install -y nginx'",
        "dnf install -y nginx",
    ]


async def test_send_command_sources_su_user_from_host_privilege():
    """The su target comes from the inventory ``privilege`` section, never from
    the operation -- the op only carries ``su=True``."""

    class RecordingHost(Host):
        def __init__(self, privilege):
            super().__init__(name="rec", privilege=privilege)
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost({"su_user": "deploy"})
    await send_command(host, DnfInstallCommand(packages=("nginx",)), su=True)
    # Unprivileged: no su even though a su_user is configured.
    await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert host.sent == [
        "su deploy -c 'dnf install -y nginx'",
        "dnf install -y nginx",
    ]


async def test_send_command_combines_sudo_then_su_on_the_real_transport():
    """sudo is the outer escalation, running su inside it (order sudo -> su)."""

    class RecordingHost(Host):
        def __init__(self, privilege):
            super().__init__(name="rec", privilege=privilege)
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost({"sudo_user": "root", "su_user": "deploy"})
    await send_command(
        host, DnfInstallCommand(packages=("nginx",)), sudo=True, su=True
    )
    assert host.sent == ["sudo su deploy -c 'dnf install -y nginx'"]


async def test_run_shell_su_uses_askpass_when_password_configured():
    """A ``su_password`` pipes the password to su's stdin via the helper, since
    su has no ``-A`` askpass mechanism; the target user is still applied."""
    host = _AskpassRecordingHost({"su_user": "deploy", "su_password": "s3cr3t"})
    await send_command(host, DnfInstallCommand(packages=("nginx",)), su=True)
    setup, elevated = host.sent
    assert "mktemp" in setup
    assert elevated == (
        "env REEMOTE5_SU_PASSWORD=s3cr3t /tmp/reemote5-askpass-abc123 "
        "| su deploy -c 'dnf install -y nginx'"
    )


async def test_run_shell_su_creates_the_askpass_helper_once():
    host = _AskpassRecordingHost({"su_password": "pw"})
    await send_command(host, DnfInstallCommand(packages=("a",)), su=True)
    await send_command(host, DnfInstallCommand(packages=("b",)), su=True)
    assert sum("mktemp" in s for s in host.sent) == 1


async def test_run_shell_su_shell_quotes_the_password():
    host = _AskpassRecordingHost({"su_password": "p@ss w0rd';rm"})
    await send_command(host, DnfInstallCommand(packages=("a",)), su=True)
    elevated = host.sent[-1]
    assert shlex.quote("p@ss w0rd';rm") in elevated
    assert elevated.endswith("| su -c 'dnf install -y a'")


async def test_send_command_sudo_su_with_su_password_wraps_the_pipe_in_sh():
    """When su carries a password its stdin pipe must stay intact under sudo,
    so sudo runs the pipeline through ``sh -c`` (order sudo -> su)."""
    host = _AskpassRecordingHost(
        {"sudo_user": "root", "su_user": "app", "su_password": "s3cr3t"}
    )
    await send_command(
        host, DnfInstallCommand(packages=("nginx",)), sudo=True, su=True
    )
    elevated = host.sent[-1]
    assert elevated.startswith("sudo ")
    assert "sh -c " in elevated
    assert "su app -c" in elevated
    assert "REEMOTE5_SU_PASSWORD=s3cr3t" in elevated


# --- doas elevation on the command path --------------------------------------


def test_command_shell_applies_the_doas_prefix():
    cmd = DnfInstallCommand(packages=("nginx",))
    # doas is passwordless and always non-interactive: ``doas -n``. Its own
    # default target (root) collapses to a bare ``doas -n``.
    assert command_shell(cmd, doas=True) == "doas -n dnf install -y nginx"
    assert (
        command_shell(cmd, doas=True, doas_user="root")
        == "doas -n dnf install -y nginx"
    )
    # A non-root target is named.
    assert (
        command_shell(cmd, doas=True, doas_user="deploy")
        == "doas -n -u deploy dnf install -y nginx"
    )
    # ...but only when doas was actually requested.
    assert command_shell(cmd, doas_user="deploy") == "dnf install -y nginx"


def test_command_shell_combines_doas_then_sudo_then_su_in_order():
    """With every flag set the order is doas -> sudo -> su: doas is the
    outermost prefix, su the innermost payload."""
    cmd = DnfInstallCommand(packages=("nginx",))
    assert (
        command_shell(cmd, doas=True, sudo=True, su=True)
        == "doas -n sudo su -c 'dnf install -y nginx'"
    )
    assert (
        command_shell(
            cmd,
            doas=True,
            doas_user="ops",
            sudo=True,
            sudo_user="deploy",
            su=True,
            su_user="app",
        )
        == "doas -n -u ops sudo -u deploy su app -c 'dnf install -y nginx'"
    )
    # doas wrapping sudo alone: doas stays outermost.
    assert (
        command_shell(cmd, doas=True, sudo=True)
        == "doas -n sudo dnf install -y nginx"
    )


async def test_send_command_prefixes_doas_on_the_real_transport_path():
    class RecordingHost(Host):
        def __init__(self, privilege=None):
            super().__init__(name="rec", privilege=privilege or {})
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost()
    await send_command(host, DnfInstallCommand(packages=("nginx",)), doas=True)
    await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert host.sent == [
        "doas -n dnf install -y nginx",
        "dnf install -y nginx",
    ]


async def test_send_command_sources_doas_user_from_host_privilege():
    """The doas target comes from the inventory ``privilege`` section, never
    from the operation -- the op only carries ``doas=True``."""

    class RecordingHost(Host):
        def __init__(self, privilege):
            super().__init__(name="rec", privilege=privilege)
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost({"doas_user": "deploy"})
    await send_command(host, DnfInstallCommand(packages=("nginx",)), doas=True)
    # Unprivileged: no doas even though a doas_user is configured.
    await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert host.sent == [
        "doas -n -u deploy dnf install -y nginx",
        "dnf install -y nginx",
    ]


async def test_send_command_combines_doas_then_sudo_then_su_on_the_real_transport():
    """doas is the outermost escalation, running sudo (and su inside it)."""

    class RecordingHost(Host):
        def __init__(self, privilege):
            super().__init__(name="rec", privilege=privilege)
            self.sent: list[str] = []

        async def _run_shell(self, shell: str):
            self.sent.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost(
        {"doas_user": "root", "sudo_user": "root", "su_user": "deploy"}
    )
    await send_command(
        host, DnfInstallCommand(packages=("nginx",)), doas=True, sudo=True, su=True
    )
    assert host.sent == ["doas -n sudo su deploy -c 'dnf install -y nginx'"]


async def test_send_command_doas_su_with_su_password_wraps_the_pipe_in_sh():
    """When doas is the outermost wrapper around an su-password pipeline -- no
    sudo layer in between -- doas runs the pipeline through ``sh -c`` so the
    ``... | su ...`` pipe stays intact (doas execs its command directly)."""
    host = _AskpassRecordingHost(
        {"doas_user": "root", "su_user": "app", "su_password": "s3cr3t"}
    )
    await send_command(
        host, DnfInstallCommand(packages=("nginx",)), doas=True, su=True
    )
    elevated = host.sent[-1]
    assert elevated.startswith("doas -n ")
    assert "sh -c " in elevated
    assert "su app -c" in elevated
    assert "REEMOTE5_SU_PASSWORD=s3cr3t" in elevated


async def test_run_shell_doas_is_passwordless_and_needs_no_askpass_helper():
    """doas has no askpass mechanism: an elevated doas command is a single
    round-trip (``doas -n ...``) with no helper-setup call, since the privilege
    section carries no password."""
    host = _AskpassRecordingHost({"doas_user": "deploy"})
    await send_command(host, DnfInstallCommand(packages=("nginx",)), doas=True)
    # No ``mktemp`` setup round-trip: doas runs the command directly.
    assert sum("mktemp" in s for s in host.sent) == 0
    assert host.sent == ["doas -n -u deploy dnf install -y nginx"]


# --- fail-fast on a non-zero exit (CommandError) -----------------------------


async def test_send_command_raises_command_error_on_nonzero_exit():
    """The apply path is fail-fast: a command that exits non-zero raises
    ``CommandError`` instead of being silently reported as applied."""

    class FailingHost(Host):
        async def _run_shell(self, shell: str):
            return SimpleNamespace(exit_status=1, stdout="", stderr="dnf: boom")

    host = FailingHost(name="f")
    with pytest.raises(CommandError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert excinfo.value.exit_status == 1
    assert excinfo.value.command == "dnf install -y nginx"
    assert "dnf: boom" in excinfo.value.stderr


async def test_run_shell_sudo_failure_reports_a_credential_free_command():
    """A failed elevated command reports the credential-free ``sudo``
    rendering, never the askpass command that carries the password."""

    class FailingSudoHost(Host):
        def __init__(self):
            super().__init__(
                name="f",
                privilege={"sudo_user": "root", "sudo_password": "s3cr3t"},
            )
            self._askpass_path = "/tmp/reemote5-askpass-cached"  # skip the setup

        async def _run_shell(self, shell: str):
            return SimpleNamespace(exit_status=1, stdout="", stderr="sudo: failed")

    host = FailingSudoHost()
    with pytest.raises(CommandError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("nginx",)), sudo=True)
    assert excinfo.value.command == "sudo dnf install -y nginx"
    assert "s3cr3t" not in excinfo.value.command
    assert "s3cr3t" not in str(excinfo.value)


async def test_fact_gathering_tolerates_a_nonzero_exit():
    """``run_shell`` without ``check`` (the fact path) returns stdout and does
    not raise on a non-zero exit -- reads like ``readlink`` on an absent link
    exit non-zero legitimately."""

    class NonZeroHost(Host):
        async def _run_shell(self, shell: str):
            return SimpleNamespace(exit_status=1, stdout="", stderr="")

    host = NonZeroHost(name="f")
    assert await host.run_shell("readlink /absent") == ""


# --- fail-fast on a missing command (CommandNotFoundError, exit 127) ----------


class MissingCommandHost(Host):
    """Answers every shell with the exit status of a command not found."""

    async def _run_shell(self, shell: str):
        return SimpleNamespace(
            exit_status=127,
            stdout="",
            stderr="-bash: lsb_release: command not found",
        )


async def test_run_shell_raises_command_not_found_even_without_check():
    """Exit 127 is the shell's command-not-found status: the fact path
    (``check`` off) must raise instead of parsing the empty stdout into a
    fact default."""
    host = MissingCommandHost(name="f")
    with pytest.raises(CommandNotFoundError) as excinfo:
        await host.run_shell("lsb_release -ca")
    assert isinstance(excinfo.value, CommandError)
    assert excinfo.value.exit_status == 127
    assert excinfo.value.command == "lsb_release -ca"
    assert "command not found on f" in str(excinfo.value)
    assert "lsb_release: command not found" in str(excinfo.value)


async def test_send_command_raises_command_not_found_for_a_missing_tool():
    """The apply path reports a missing tool as the same ``CommandError``
    subclass, so existing fail-fast handling catches it unchanged."""
    host = MissingCommandHost(name="f")
    with pytest.raises(CommandError) as excinfo:
        await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert isinstance(excinfo.value, CommandNotFoundError)
    assert excinfo.value.command == "dnf install -y nginx"


async def test_run_shell_tolerates_a_shell_guarded_missing_command():
    """A fact that deliberately tolerates an absent tool guards its command
    with ``|| true`` (``Which``, ``Last``), so the shell exits 0 and the read
    stays tolerant -- the 127 never reaches the transport."""

    class GuardedHost(Host):
        async def _run_shell(self, shell: str):
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = GuardedHost(name="f")
    assert await host.run_shell("command -v nosuchcommand || true") == ""


# --- FactCache (4.4) ---------------------------------------------------------


async def test_fact_cache_caches_per_key():
    host = SimulatedHost(name="h", packages={"nginx": [""]})
    cache = FactCache(host, phase="prepare")
    assert await cache.get(DnfPackages) == [{"name": "nginx", "versions": [""]}]

    host.packages["vim"] = [""]
    # Cached: the mutation is not visible through the same cache...
    assert await cache.get(DnfPackages) == [{"name": "nginx", "versions": [""]}]
    # ...but a fresh cache sees it, which is exactly the per-step
    # recreation the apply driver relies on.
    assert await FactCache(host, phase="apply").get(DnfPackages) == [
        {"name": "nginx", "versions": [""]},
        {"name": "vim", "versions": [""]},
    ]


async def test_fact_cache_keys_include_args():
    host = SimulatedHost(name="h", links={"/a": "/target-a"})
    cache = FactCache(host, phase="prepare")
    assert await cache.get(FileLink, "/a") == "/target-a"
    assert await cache.get(FileLink, "/b") is None


# --- gather_fact: the multi-host fan-out of FactCache.get --------------------


async def test_gather_fact_returns_a_host_aligned_mapping():
    """gather_fact runs one fact across all hosts and keys the results by host
    name, so a single await yields every host's value."""
    hosts = [
        SimulatedHost(name="web1", packages={"nginx": [""]}),
        SimulatedHost(name="web2", packages={"nginx": [""], "vim": [""]}),
        SimulatedHost(name="web3", packages={}),
    ]
    assert await gather_fact(hosts, DnfPackages) == {
        "web1": [{"name": "nginx", "versions": [""]}],
        "web2": [
            {"name": "nginx", "versions": [""]},
            {"name": "vim", "versions": [""]},
        ],
        "web3": [],
    }


async def test_gather_fact_forwards_args_and_kwargs():
    """Positional/keyword args reach each fact's process, exactly as
    FactCache.get forwards them (here FileLink's src path)."""
    hosts = [
        SimulatedHost(name="web1", links={"/a": "/target-a"}),
        SimulatedHost(name="web2", links={"/a": "/target-b"}),
    ]
    assert await gather_fact(hosts, FileLink, "/a") == {
        "web1": "/target-a",
        "web2": "/target-b",
    }
    # A path no host has a link for gathers to None everywhere.
    assert await gather_fact(hosts, FileLink, "/missing") == {
        "web1": None,
        "web2": None,
    }


async def test_gather_fact_runs_hosts_concurrently():
    """The per-host reads overlap rather than running one host at a time: a
    fact tracking the in-flight high-water mark sees both hosts running
    before either returns (a serial loop would peak at 1)."""
    in_flight = 0
    peak = 0

    class SlowFact:
        @classmethod
        async def process(cls, host):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)  # yield so the other host's process starts
            in_flight -= 1
            return host.name

    hosts = [SimulatedHost(name="web1"), SimulatedHost(name="web2")]
    assert await gather_fact(hosts, SlowFact) == {"web1": "web1", "web2": "web2"}
    assert peak == 2


# --- Operations (6) ----------------------------------------------------------


async def test_dnf_packages_sets_changed_and_yields_command():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx, packages=("nginx", "vim"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [DnfInstallCommand(packages=("nginx", "vim"))]


async def test_dnf_packages_no_change_reason_recorded():
    host = SimulatedHost(name="h", packages={"nginx": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx, packages=("nginx",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged (10)
    assert commands == []


# --- dnf.packages options (baseline parity) ------------------------------------


async def test_dnf_packages_accepts_a_bare_string():
    """The baseline accepts ``packages="nginx"`` as shorthand for a one-item list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx, packages="nginx")]
    assert commands == [DnfInstallCommand(packages=("nginx",))]


async def test_dnf_packages_none_without_flags_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_dnf_packages_clean_and_update_yield_before_install():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.packages(ctx, packages=("nginx",), clean=True, update=True)
    ]
    assert commands == [
        DnfCleanCommand(),
        DnfUpdateCommand(),
        DnfInstallCommand(packages=("nginx",)),
    ]
    assert commands[0].shell() == "dnf clean all"
    assert commands[1].shell() == "dnf update -y"
    assert ctx.changed
    assert ctx.reason == "dnf clean; dnf update; 1 package(s) to install"


async def test_dnf_packages_update_only_without_packages():
    """``update=True`` alone mirrors the baseline's stateless ``dnf.update``."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx, update=True)]
    assert commands == [DnfUpdateCommand()]
    assert ctx.changed
    assert ctx.reason == "dnf update"


async def test_dnf_packages_nobest_and_extra_install_args():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.packages(
            ctx,
            packages=("nginx",),
            nobest=True,
            extra_install_args="--skip-broken",
        )
    ]
    assert commands == [
        DnfInstallCommand(packages=("nginx",), nobest=True, extra_args="--skip-broken")
    ]
    assert commands[0].shell() == "dnf install -y --nobest --skip-broken nginx"


async def test_dnf_packages_extra_uninstall_args():
    host = SimulatedHost(name="h", packages={"nginx": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.packages(
            ctx,
            packages=("nginx",),
            present=False,
            extra_uninstall_args="--autoremove",
        )
    ]
    assert commands == [
        DnfRemoveCommand(packages=("nginx",), extra_args="--autoremove")
    ]
    assert commands[0].shell() == "dnf remove -y --autoremove nginx"


async def test_dnf_packages_latest_upgrades_installed_unpinned_packages():
    """``latest`` upgrades present, unpinned packages; missing ones install."""
    host = SimulatedHost(name="h", packages={"nginx": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.packages(ctx, packages=("nginx", "vim"), latest=True)
    ]
    assert commands == [
        DnfInstallCommand(packages=("vim",)),
        DnfUpdateCommand(packages=("nginx",)),
    ]
    assert commands[1].shell() == "dnf update -y nginx"
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install; 1 package(s) to upgrade"


async def test_dnf_packages_latest_skips_version_pins():
    """A pinned ``<pkg>=<version>`` is never an upgrade candidate, and the
    name-only comparison treats any installed version as present."""
    host = SimulatedHost(name="h", packages={"nginx": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.packages(ctx, packages=("nginx=1.23.0",), latest=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_dnf_packages_version_pin_installs_when_name_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx, packages=("nginx=1.23.0",))]
    assert commands == [DnfInstallCommand(packages=("nginx=1.23.0",))]
    assert commands[0].shell() == "dnf install -y nginx=1.23.0"


async def test_dnf_packages_version_pin_splits_on_the_last_equals():
    """The pin splits on the *last* ``=`` (the baseline's ``rsplit`` join, apt's
    ``rpartition``), so an ``=`` inside the version never cuts the name short:
    the installed name ``foo=1.0`` matches the pin ``foo=1.0=2``."""
    host = SimulatedHost(name="h", packages={"foo=1.0": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.packages(ctx, packages=("foo=1.0=2",), latest=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


def test_dnf_package_commands_shell_quote_the_names():
    """Package names are shell-quoted like apt's, so a metacharacter in a name
    can never reach the remote shell unescaped."""
    package = "nginx;touch /tmp/pwned"
    quoted = shlex.quote(package)
    assert quoted != package
    assert DnfInstallCommand(packages=(package,)).shell() == f"dnf install -y {quoted}"
    assert DnfRemoveCommand(packages=(package,)).shell() == f"dnf remove -y {quoted}"
    assert DnfUpdateCommand(packages=(package,)).shell() == f"dnf update -y {quoted}"


async def test_dnf_packages_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.packages(ctx, packages=("nginx",), future_flag=True)
    ]
    assert commands == [DnfInstallCommand(packages=("nginx",))]


async def test_dnf_options_flow_through_run_and_format_log():
    """End-to-end: clean/update/install all execute and render in the log."""

    def opts_deploy(host):
        yield op(
            dnf.packages,
            name="Update and install nginx",
            packages=("nginx",),
            clean=True,
            update=True,
        )

    _plans, report, (host,) = await run(
        [{"host": "a", "connection": {}}],
        opts_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        DnfCleanCommand(),
        DnfUpdateCommand(),
        DnfInstallCommand(packages=("nginx",)),
    )
    assert host.packages == {"nginx": [""]}
    log = format_log(report)
    assert "$ dnf clean all" in log
    assert "$ dnf update -y" in log
    assert "$ dnf install -y nginx" in log


async def test_dnf_version_pin_install_simulates_by_name():
    """The simulated seam records the pin's version under the bare name, so
    the name-only comparison and the fresh-fact-cache property hold for
    pinned installs too."""

    def pinned_deploy(host):
        yield op(dnf.packages, name="Install pinned nginx", packages=("nginx=1.23.0",))

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], pinned_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert report.log[0].changed
    assert host.packages == {"nginx": ["1.23.0"]}
    # A second prepare sees the name as installed: no change.
    _runtimes, plans2 = await run_prepare([host], pinned_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


# --- dnf.repo operation (baseline parity) --------------------------------------


async def test_dnf_repo_writes_the_constructed_file():
    """present=True with a baseurl builds the ``.repo`` content and yields the
    spool-transfer + elevated-install pair when the file is absent."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.repo(ctx, src="DockerCE", baseurl=DOCKER_BASEURL)]
    assert ctx.changed
    assert ctx.reason == (
        f"repo file {DOCKER_REPO_PATH} missing or differs from the desired content"
    )
    assert commands == [
        DnfRepoUploadCommand(content=DOCKER_REPO_CONTENT, dest=_REPO_SPOOL_PATH),
        DnfRepoInstallCommand(spool=_REPO_SPOOL_PATH, dest=DOCKER_REPO_PATH),
    ]


async def test_dnf_repo_no_change_when_content_matches():
    """A repo file already holding exactly the constructed content is a no-op."""
    host = SimulatedHost(
        name="h", remote_files={DOCKER_REPO_PATH: DOCKER_REPO_CONTENT.encode()}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.repo(ctx, src="DockerCE", baseurl=DOCKER_BASEURL)]
    assert not ctx.changed
    assert ctx.reason == f"repo file already up to date ({DOCKER_REPO_PATH})"
    assert commands == []


async def test_dnf_repo_options_render_into_the_content():
    """description/enabled/gpgcheck/gpgkey all land in the constructed file."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.repo(
            ctx,
            src="DockerCE",
            baseurl=DOCKER_BASEURL,
            description="Docker CE for CentOS",
            enabled=False,
            gpgcheck=False,
            gpgkey="https://download.docker.com/linux/centos/gpg",
        )
    ]
    (upload, _install) = commands
    assert upload.content == (
        "[DockerCE]\n"
        "name=Docker CE for CentOS\n"
        f"baseurl={DOCKER_BASEURL}\n"
        "enabled=0\n"
        "gpgcheck=0\n"
        "gpgkey=https://download.docker.com/linux/centos/gpg\n"
    )


async def test_dnf_repo_removes_an_existing_file():
    host = SimulatedHost(
        name="h", remote_files={DOCKER_REPO_PATH: DOCKER_REPO_CONTENT.encode()}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.repo(ctx, src="DockerCE", present=False)]
    assert ctx.changed
    assert ctx.reason == f"repo file {DOCKER_REPO_PATH} present, will remove"
    assert commands == [DnfRepoRemoveCommand(path=DOCKER_REPO_PATH)]


async def test_dnf_repo_removal_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.repo(ctx, src="DockerCE", present=False)]
    assert not ctx.changed
    assert ctx.reason == f"repo file already absent ({DOCKER_REPO_PATH})"
    assert commands == []


async def test_dnf_repo_rejects_a_url_src():
    """The baseline downloads a URL ``.repo`` file; reemote5 has no download
    operation, so a URL src raises instead of silently doing nothing."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not support a URL src"):
        _ = [
            c
            async for c in dnf.repo(
                ctx, src="https://download.docker.com/linux/centos/docker-ce.repo"
            )
        ]


async def test_dnf_repo_present_requires_baseurl():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="requires baseurl"):
        _ = [c async for c in dnf.repo(ctx, src="DockerCE")]


async def test_dnf_repo_rejects_a_relative_gpgkey():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="gpgkey must be a valid URL"):
        _ = [
            c
            async for c in dnf.repo(
                ctx, src="DockerCE", baseurl=DOCKER_BASEURL, gpgkey="gpg/key"
            )
        ]


async def test_dnf_repo_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot, like every other
    dnf operation; op() already strips the meta-parameters."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.repo(
            ctx, src="DockerCE", baseurl=DOCKER_BASEURL, future_flag=True
        )
    ]
    assert commands == [
        DnfRepoUploadCommand(content=DOCKER_REPO_CONTENT, dest=_REPO_SPOOL_PATH),
        DnfRepoInstallCommand(spool=_REPO_SPOOL_PATH, dest=DOCKER_REPO_PATH),
    ]


def test_dnf_repo_upload_command_renders_as_an_unprivileged_scp():
    """The spool transfer has no shell(); it renders via __str__ and -- being a
    TransferCommand -- is never elevated, even though dnf.repo's request is
    sudo=True for the install half. The content rides as data, so the literal
    ``$basearch`` can never be shell-expanded."""
    cmd = DnfRepoUploadCommand(content=DOCKER_REPO_CONTENT, dest=_REPO_SPOOL_PATH)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <repo file> -> {_REPO_SPOOL_PATH}"
    assert command_shell(cmd) == f"scp <repo file> -> {_REPO_SPOOL_PATH}"
    assert command_shell(cmd, sudo=True) == f"scp <repo file> -> {_REPO_SPOOL_PATH}"


def test_dnf_repo_install_command_shell_and_elevation():
    """The install wraps the mkdir/cp/chmod/rm chain in ``sh -c`` so every step
    runs under the elevated shell (a bare ``sudo ... && rm`` would leave the
    cleanup unprivileged), and shell-quotes every path."""
    cmd = DnfRepoInstallCommand(spool=_REPO_SPOOL_PATH, dest=DOCKER_REPO_PATH)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    # The chain is a single ``sh -c`` argument; shlex.split recovers the inner
    # script (the raw ``shell`` re-quotes it).
    inner = shlex.split(shell)[2]
    assert inner == (
        "mkdir -p /etc/yum.repos.d && "
        f"cp {_REPO_SPOOL_PATH} {DOCKER_REPO_PATH} && "
        f"chmod 0644 {DOCKER_REPO_PATH} && rm -f {_REPO_SPOOL_PATH}"
    )
    # Under sudo the whole sh -c chain runs as root.
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")


def test_dnf_repo_remove_command_shell_quotes_the_path():
    """Removal renders as a plain ``rm -f`` with a shell-quoted path."""
    assert DnfRepoRemoveCommand(path=DOCKER_REPO_PATH).shell() == (
        f"rm -f {DOCKER_REPO_PATH}"
    )
    path = "/etc/yum.repos.d/odd name.repo"
    assert DnfRepoRemoveCommand(path=path).shell() == f"rm -f {shlex.quote(path)}"


async def test_dnf_repo_simulated_seam_spools_then_installs():
    """send_command maps the transfer onto remote_files (via put_file) and the
    install onto the dest path with a root-owned 0644 file_stats row while
    clearing the spool file -- the two-step effect of one dnf.repo request."""
    host = SimulatedHost(name="h")
    await send_command(
        host,
        DnfRepoUploadCommand(content=DOCKER_REPO_CONTENT, dest=_REPO_SPOOL_PATH),
    )
    assert host.remote_files[_REPO_SPOOL_PATH] == DOCKER_REPO_CONTENT.encode()
    await send_command(
        host, DnfRepoInstallCommand(spool=_REPO_SPOOL_PATH, dest=DOCKER_REPO_PATH)
    )
    assert host.remote_files[DOCKER_REPO_PATH] == DOCKER_REPO_CONTENT.encode()
    assert _REPO_SPOOL_PATH not in host.remote_files
    assert DOCKER_REPO_PATH in host.existing_files
    assert host.file_stats[DOCKER_REPO_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(DOCKER_REPO_CONTENT.encode()),
    }


async def test_dnf_repo_end_to_end_write_then_noop():
    """Through run(): the first apply writes the file into the simulated remote
    filesystem; a second run sees the matching content and is a no-op. The
    request kwargs are plain strings, so the determinism guard holds with no
    module-level constant (unlike files.put's identity-compared IO source)."""

    def repo_deploy(host):
        yield op(
            dnf.repo,
            name="Add DockerCE repo",
            src="DockerCE",
            baseurl=DOCKER_BASEURL,
            sudo=True,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's write

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], repo_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        DnfRepoUploadCommand(content=DOCKER_REPO_CONTENT, dest=_REPO_SPOOL_PATH),
        DnfRepoInstallCommand(spool=_REPO_SPOOL_PATH, dest=DOCKER_REPO_PATH),
    )
    assert host.remote_files[DOCKER_REPO_PATH] == DOCKER_REPO_CONTENT.encode()
    assert _REPO_SPOOL_PATH not in host.remote_files  # install cleaned the spool
    log = format_log(report)
    assert f"    $ scp <repo file> -> {_REPO_SPOOL_PATH}" in log
    assert "    $ sudo sh -c " in log

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], repo_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == f"repo file already up to date ({DOCKER_REPO_PATH})"


async def test_dnf_repo_remove_after_write_in_one_deploy():
    """The apply phase's fresh per-step fact cache sees the write, so a following
    present=False in the same deploy removes it -- while the prepare plan (which
    never sends) still shows the removal as a no-op."""

    def repo_deploy(host):
        yield op(dnf.repo, name="add", src="DockerCE", baseurl=DOCKER_BASEURL)
        yield op(dnf.repo, name="remove", src="DockerCE", present=False)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], repo_deploy)
    assert plans["h"][0].changed  # write: absent -> differs
    assert not plans["h"][1].changed  # remove: still absent during prepare
    report = await run_apply(runtimes, plans=plans)
    add, remove = report.log
    assert add.changed
    assert remove.changed
    assert remove.reason == f"repo file {DOCKER_REPO_PATH} present, will remove"
    assert host.remote_files == {}


# --- dnf.repo ansible-datadog extensions ---------------------------------------


async def test_dnf_repo_datadog_options_render_into_the_content():
    """includepkgs/repo_gpgcheck/proxy/proxy_username append after gpgkey in a
    fixed order (the ansible-datadog role's yum_repository fields); without a
    proxy_password the install keeps the baseline's 0644 mode."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.repo(
            ctx,
            src="datadog",
            baseurl="https://yum.datadoghq.com/stable/7/x86_64/",
            description="Datadog, Inc.",
            gpgkey="https://keys.datadoghq.com/DATADOG_RPM_KEY_CURRENT.public",
            includepkgs="datadog-agent",
            repo_gpgcheck=True,
            proxy="http://proxy.example.com:3128",
            proxy_username="proxyuser",
        )
    ]
    (upload, install) = commands
    assert upload.content == (
        "[datadog]\n"
        "name=Datadog, Inc.\n"
        "baseurl=https://yum.datadoghq.com/stable/7/x86_64/\n"
        "enabled=1\n"
        "gpgcheck=1\n"
        "gpgkey=https://keys.datadoghq.com/DATADOG_RPM_KEY_CURRENT.public\n"
        "repo_gpgcheck=1\n"
        "includepkgs=datadog-agent\n"
        "proxy=http://proxy.example.com:3128\n"
        "proxy_username=proxyuser\n"
    )
    assert install.mode == "0644"


async def test_dnf_repo_repo_gpgcheck_false_renders_zero_and_none_omits():
    """repo_gpgcheck=False writes ``repo_gpgcheck=0``; leaving it None omits
    the line entirely, so the constructed content stays the baseline-parity
    file (a no-op against a file an older release wrote)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.repo(
            ctx, src="DockerCE", baseurl=DOCKER_BASEURL, repo_gpgcheck=False
        )
    ]
    (upload, _install) = commands
    assert upload.content == DOCKER_REPO_CONTENT + "repo_gpgcheck=0\n"

    host2 = SimulatedHost(
        name="h2", remote_files={DOCKER_REPO_PATH: DOCKER_REPO_CONTENT.encode()}
    )
    ctx2 = OpContext(host=host2, facts=FactCache(host2, phase="prepare"))
    assert [c async for c in dnf.repo(ctx2, src="DockerCE", baseurl=DOCKER_BASEURL)] == []
    assert not ctx2.changed


async def test_dnf_repo_proxy_password_installs_0600():
    """A proxy_password lands in the content and tightens the install mode to
    0600 so the password is not world-readable; the simulated install records
    the same mode in file_stats."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.repo(
            ctx,
            src="DockerCE",
            baseurl=DOCKER_BASEURL,
            proxy="http://proxy.example.com:3128",
            proxy_password="s3cret",
        )
    ]
    (upload, install) = commands
    assert upload.content == (
        DOCKER_REPO_CONTENT
        + "proxy=http://proxy.example.com:3128\n"
        + "proxy_password=s3cret\n"
    )
    assert install.mode == "0600"
    for cmd in commands:
        await send_command(host, cmd)
    assert host.file_stats[DOCKER_REPO_PATH]["mode"] == 600


async def test_dnf_repo_proxy_none_token_is_accepted():
    """dnf's ``_none_`` disable token turns off a proxy inherited from
    dnf.conf, so it passes the URL validation."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.repo(
            ctx, src="DockerCE", baseurl=DOCKER_BASEURL, proxy="_none_"
        )
    ]
    (upload, _install) = commands
    assert upload.content == DOCKER_REPO_CONTENT + "proxy=_none_\n"


async def test_dnf_repo_rejects_a_non_url_proxy():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="proxy must be a URL or '_none_'"):
        _ = [
            c
            async for c in dnf.repo(
                ctx, src="DockerCE", baseurl=DOCKER_BASEURL, proxy="proxy.example.com"
            )
        ]


def test_dnf_repo_install_command_renders_the_requested_mode():
    """The chmod step uses the descriptor's mode field, inside the elevated
    sh -c chain."""
    cmd = DnfRepoInstallCommand(
        spool=_REPO_SPOOL_PATH, dest=DOCKER_REPO_PATH, mode="0600"
    )
    inner = shlex.split(cmd.shell())[2]
    assert f"chmod 0600 {DOCKER_REPO_PATH}" in inner


async def test_dnf_repo_datadog_options_end_to_end_write_then_noop():
    """Through run(): the extended options are plain hashable kwargs, so the
    determinism guard holds; the first apply writes the file and a second run
    with identical arguments is a no-op."""

    def repo_deploy(host):
        yield op(
            dnf.repo,
            name="Add restricted repo",
            src="DockerCE",
            baseurl=DOCKER_BASEURL,
            includepkgs="docker-ce docker-ce-cli containerd.io",
            repo_gpgcheck=False,
            sudo=True,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], repo_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    content = host.remote_files[DOCKER_REPO_PATH].decode()
    assert "repo_gpgcheck=0\n" in content
    assert content.endswith("includepkgs=docker-ce docker-ce-cli containerd.io\n")

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], repo_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == f"repo file already up to date ({DOCKER_REPO_PATH})"


# --- dnf.makecache / dnf.clean operations (reemote5 originals) ------------------


async def test_dnf_makecache_is_stateless_and_unscoped():
    """No fact is read: the operation always reports a change and yields the
    bare ``dnf -y makecache``; unknown kwargs are absorbed."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.makecache(ctx, future_flag=True)]
    assert ctx.changed
    assert ctx.reason == "dnf makecache"
    assert commands == [DnfMakecacheCommand()]
    assert commands[0].shell() == "dnf -y makecache"


async def test_dnf_makecache_scopes_to_named_repos():
    """Scoped repos render the role's --disablerepo='*' --enablerepo=<id>
    flags; the disable flag is quoted whole so the shell never globs the *."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.makecache(ctx, repos=("datadog",))]
    assert ctx.reason == "dnf makecache (datadog)"
    assert commands == [DnfMakecacheCommand(repos=("datadog",))]
    assert commands[0].shell() == (
        "dnf -y makecache '--disablerepo=*' --enablerepo=datadog"
    )


async def test_dnf_makecache_normalizes_str_and_list_repos():
    """A bare string becomes a one-tuple and a list a tuple (direct generator
    calls; op() requests must pass tuples for hashability)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    (from_str,) = [c async for c in dnf.makecache(ctx, repos="datadog")]
    (from_list,) = [c async for c in dnf.makecache(ctx, repos=["a", "b"])]
    assert from_str.repos == ("datadog",)
    assert from_list.repos == ("a", "b")
    assert from_list.shell() == (
        "dnf -y makecache '--disablerepo=*' --enablerepo=a --enablerepo=b"
    )


def test_dnf_makecache_elevates_as_one_command():
    """A plain shell command (no sh -c wrapper), so sudo elevates the whole
    refresh."""
    cmd = DnfMakecacheCommand(repos=("datadog",))
    assert command_shell(cmd, sudo=True) == (
        "sudo dnf -y makecache '--disablerepo=*' --enablerepo=datadog"
    )


async def test_dnf_clean_defaults_to_unscoped_clean_all():
    """The default request is exactly what dnf.packages(clean=True) yields."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.clean(ctx)]
    assert ctx.changed
    assert ctx.reason == "dnf clean all"
    assert commands == [DnfCleanCommand()]
    assert commands[0].shell() == "dnf clean all"


async def test_dnf_clean_scoped_metadata_matches_the_role():
    """what=metadata plus a repo scope renders the ansible-datadog role's
    ``dnf clean metadata --disablerepo="*" --enablerepo=datadog`` step."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.clean(ctx, what="metadata", repos=("datadog",))
    ]
    assert ctx.changed
    assert ctx.reason == "dnf clean metadata (datadog)"
    assert commands == [DnfCleanCommand(what="metadata", repos=("datadog",))]
    assert commands[0].shell() == (
        "dnf clean metadata '--disablerepo=*' --enablerepo=datadog"
    )
    assert command_shell(commands[0], sudo=True).startswith("sudo dnf clean metadata")


async def test_dnf_clean_rejects_an_unknown_kind():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="what must be one of"):
        _ = [c async for c in dnf.clean(ctx, what="everything")]


def test_dnf_clean_quotes_odd_repo_ids():
    """A repo id with shell metacharacters rides shlex.quote like every other
    descriptor argument."""
    repo = "odd repo"
    cmd = DnfCleanCommand(what="metadata", repos=(repo,))
    assert cmd.shell().endswith(shlex.quote(f"--enablerepo={repo}"))


async def test_dnf_cache_ops_after_repo_write_in_one_deploy():
    """The role's sequence in ONE deploy: write the repo file, then the scoped
    clean-metadata and makecache refreshes. All three apply; on a second run
    the repo write is a no-op while the stateless cache ops still report a
    change (unconditional refresh -- no cross-phase fact gating)."""

    def cache_deploy(host):
        yield op(
            dnf.repo,
            name="Add DockerCE repo",
            src="DockerCE",
            baseurl=DOCKER_BASEURL,
            sudo=True,
        )
        yield op(
            dnf.clean,
            name="Drop stale DockerCE metadata",
            what="metadata",
            repos=("DockerCE",),
            sudo=True,
        )
        yield op(
            dnf.makecache,
            name="Refresh DockerCE metadata",
            repos=("DockerCE",),
            sudo=True,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], cache_deploy, host_factory=factory
    )
    write, clean_entry, makecache_entry = report.log
    assert write.changed
    assert clean_entry.changed
    assert clean_entry.commands == (
        DnfCleanCommand(what="metadata", repos=("DockerCE",)),
    )
    assert makecache_entry.changed
    assert makecache_entry.commands == (DnfMakecacheCommand(repos=("DockerCE",)),)
    assert host.remote_files[DOCKER_REPO_PATH] == DOCKER_REPO_CONTENT.encode()
    log = format_log(report)
    assert "    $ sudo dnf clean metadata '--disablerepo=*' --enablerepo=DockerCE" in log
    assert "    $ sudo dnf -y makecache '--disablerepo=*' --enablerepo=DockerCE" in log

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], cache_deploy, host_factory=factory
    )
    write2, clean2, makecache2 = report2.log
    assert not write2.changed
    assert clean2.changed
    assert makecache2.changed


# --- dnf.rpm operation (baseline parity) --------------------------------------


async def test_dnf_rpm_installs_an_absent_package():
    """present=True with a ``.rpm`` whose package is not installed yields the
    ``rpm -i`` install and records the change."""
    host = SimulatedHost(name="h", rpm_files={RPM_FILE_PATH: RPM_FILE_INFO})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.rpm(ctx, src=RPM_FILE_PATH)]
    assert ctx.changed
    assert ctx.reason == f"rpm {RPM_FILE_PATH} not installed, will install"
    assert commands == [RpmInstallCommand(src=RPM_FILE_PATH)]


async def test_dnf_rpm_no_change_when_installed_version_matches():
    """The file's package installed at the file's exact version is a no-op."""
    host = SimulatedHost(
        name="h",
        rpm_files={RPM_FILE_PATH: RPM_FILE_INFO},
        rpm_packages={RPM_FILE_NAME: RPM_FILE_VERSION},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.rpm(ctx, src=RPM_FILE_PATH)]
    assert not ctx.changed
    assert ctx.reason == f"rpm {RPM_FILE_PATH} is installed"
    assert commands == []


async def test_dnf_rpm_reinstalls_when_installed_version_differs():
    """A different installed version does not match the file, so present=True
    installs again -- the version-comparison idempotence the baseline's ``ensure_rpm``
    performs."""
    host = SimulatedHost(
        name="h",
        rpm_files={RPM_FILE_PATH: RPM_FILE_INFO},
        rpm_packages={RPM_FILE_NAME: "0.9-1.el9"},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.rpm(ctx, src=RPM_FILE_PATH)]
    assert ctx.changed
    assert commands == [RpmInstallCommand(src=RPM_FILE_PATH)]


async def test_dnf_rpm_removes_an_installed_package():
    """present=False removes the file's package by name (reusing the dnf remove
    descriptor) when that exact version is installed."""
    host = SimulatedHost(
        name="h",
        rpm_files={RPM_FILE_PATH: RPM_FILE_INFO},
        rpm_packages={RPM_FILE_NAME: RPM_FILE_VERSION},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.rpm(ctx, src=RPM_FILE_PATH, present=False)]
    assert ctx.changed
    assert ctx.reason == f"rpm {RPM_FILE_PATH} installed, will remove"
    assert commands == [DnfRemoveCommand(packages=(RPM_FILE_NAME,))]


async def test_dnf_rpm_removal_noop_when_not_installed():
    """present=False with the file present but its package not installed is a
    no-op."""
    host = SimulatedHost(name="h", rpm_files={RPM_FILE_PATH: RPM_FILE_INFO})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.rpm(ctx, src=RPM_FILE_PATH, present=False)]
    assert not ctx.changed
    assert ctx.reason == f"rpm {RPM_FILE_PATH} is not installed"
    assert commands == []


async def test_dnf_rpm_removal_noop_when_file_absent():
    """A present=False whose ``.rpm`` is absent cannot resolve a package name, so
    it is a no-op -- the limitation the docstring records for an un-downloadable
    source."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.rpm(ctx, src=RPM_FILE_PATH, present=False)]
    assert not ctx.changed
    assert ctx.reason == f"rpm {RPM_FILE_PATH} is not installed"
    assert commands == []


async def test_dnf_rpm_rejects_a_url_src():
    """The baseline downloads a URL ``.rpm``; reemote5 has no download operation, so a
    URL src raises instead of silently doing nothing (mirroring dnf.repo)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not support a URL src"):
        _ = [
            c
            async for c in dnf.rpm(
                ctx, src="https://example.com/foo-1.0-1.el9.x86_64.rpm"
            )
        ]


async def test_dnf_rpm_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot; op() already strips
    the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h", rpm_files={RPM_FILE_PATH: RPM_FILE_INFO})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.rpm(ctx, src=RPM_FILE_PATH, future_flag=True)]
    assert commands == [RpmInstallCommand(src=RPM_FILE_PATH)]


def test_rpm_install_command_shell_and_elevation():
    """The descriptor renders ``rpm -i <quoted src>`` and elevates as a whole
    under sudo -- a plain command, not the baseline's ``|| ...`` runtime-check chain,
    whose fallback arm a ``sudo`` prefix would leave unprivileged."""
    cmd = RpmInstallCommand(src=RPM_FILE_PATH)
    assert cmd.shell() == f"rpm -i {RPM_FILE_PATH}"
    assert command_shell(cmd, sudo=True) == f"sudo rpm -i {RPM_FILE_PATH}"
    # A path with a space is shell-quoted.
    assert RpmInstallCommand(src="/tmp/my pkg.rpm").shell() == "rpm -i '/tmp/my pkg.rpm'"


async def test_dnf_rpm_simulated_seam_mutates_state():
    """``send_command`` maps the install descriptor onto the simulated state: the
    file's name/version land in ``rpm_packages`` and the name in ``packages`` (so
    ``DnfPackages`` sees it too)."""
    host = SimulatedHost(name="h", rpm_files={RPM_FILE_PATH: RPM_FILE_INFO})
    await send_command(host, RpmInstallCommand(src=RPM_FILE_PATH))
    assert host.rpm_packages == {RPM_FILE_NAME: RPM_FILE_VERSION}
    assert RPM_FILE_NAME in host.packages


async def test_dnf_rpm_end_to_end_install_then_noop():
    """Through run(): the first apply installs the ``.rpm`` into the simulated
    state; a second run sees the matching installed version and is a no-op. The
    request kwargs are plain strings, so the determinism guard holds with no
    module-level constant."""

    def rpm_deploy(host):
        yield op(
            dnf.rpm,
            name="Install foo from a .rpm",
            src=RPM_FILE_PATH,
            sudo=True,
        )

    host = SimulatedHost(name="h", rpm_files={RPM_FILE_PATH: RPM_FILE_INFO})

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's install

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], rpm_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (RpmInstallCommand(src=RPM_FILE_PATH),)
    assert host.rpm_packages == {RPM_FILE_NAME: RPM_FILE_VERSION}
    assert f"sudo rpm -i {RPM_FILE_PATH}" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], rpm_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == f"rpm {RPM_FILE_PATH} is installed"


async def test_dnf_rpm_remove_after_install_in_one_deploy():
    """The apply phase's fresh per-step fact cache sees the install, so a following
    present=False in the same deploy removes it -- while the prepare plan (which
    never sends) still shows the removal as a no-op."""

    def rpm_deploy(host):
        yield op(dnf.rpm, name="install", src=RPM_FILE_PATH)
        yield op(dnf.rpm, name="remove", src=RPM_FILE_PATH, present=False)

    host = SimulatedHost(name="h", rpm_files={RPM_FILE_PATH: RPM_FILE_INFO})
    runtimes, plans = await run_prepare([host], rpm_deploy)
    assert plans["h"][0].changed  # install: file present, package absent
    assert not plans["h"][1].changed  # remove: still absent during prepare
    report = await run_apply(runtimes, plans=plans)
    install, remove = report.log
    assert install.changed
    assert remove.changed
    assert remove.reason == f"rpm {RPM_FILE_PATH} installed, will remove"
    assert host.rpm_packages == {}
    assert RPM_FILE_NAME not in host.packages


# --- RpmGpgKeys fact (reemote5 original) --------------------------------------

# Canned ``rpm -qa gpg-pubkey`` rows: the legacy Datadog key (the one the
# ansible-datadog role removes, short id 4172A230) and the Docker CE rpm key.
# rpm stores the key id lowercased as VERSION and the hex creation timestamp
# as RELEASE; the SUMMARY is rpm's gpg(<uid>) description and carries spaces.
RPM_GPG_KEY_ROWS = (
    "4172a230 53a0b1c2 gpg(Datadog, Inc. (Datadog RPM key) <package@datadoghq.com>)\n"
    "060a6254 572bde81 gpg(Docker Release (CE rpm) <docker@docker.com>)\n"
)


async def test_rpm_gpg_keys_parses_rows_sorted_unique_lowercased():
    """Over the real-transport path, ``RpmGpgKeys`` parses the query rows into
    sorted ``RpmGpgKey`` entries: key ids lowercased, blank lines dropped,
    duplicate rows collapsed, summaries (spaces included) kept verbatim."""
    host = CannedShellHost(
        "060A6254 572bde81 gpg(Docker Release (CE rpm) <docker@docker.com>)\n"
        "\n"
        "4172a230 53a0b1c2 gpg(Datadog, Inc. (Datadog RPM key) <package@datadoghq.com>)\n"
        "4172a230 53a0b1c2 gpg(Datadog, Inc. (Datadog RPM key) <package@datadoghq.com>)\n"
        "  060a6254 572bde81 gpg(Docker Release (CE rpm) <docker@docker.com>)  \n"
    )
    assert await RpmGpgKeys.process(host) == [
        {
            "keyid": "060a6254",
            "release": "572bde81",
            "summary": "gpg(Docker Release (CE rpm) <docker@docker.com>)",
        },
        {
            "keyid": "4172a230",
            "release": "53a0b1c2",
            "summary": "gpg(Datadog, Inc. (Datadog RPM key) <package@datadoghq.com>)",
        },
    ]


def test_rpm_gpg_keys_parse_empty_output_is_an_empty_list():
    """A host with no imported keys parses to an empty list, not None."""
    assert RpmGpgKeys.parse([]) == []


def test_rpm_gpg_keys_parse_skips_malformed_rows():
    """A row with fewer than two tokens is skipped; a two-token row (no
    summary) keeps an empty summary rather than being dropped."""
    assert RpmGpgKeys.parse(["", "orphan", "4172a230 53a0b1c2"]) == [
        {"keyid": "4172a230", "release": "53a0b1c2", "summary": ""}
    ]


def test_rpm_gpg_keys_command_queries_the_pseudo_packages():
    """The query selects the gpg-pubkey pseudo-package name and prints
    VERSION (key id), RELEASE and SUMMARY -- the rpmdb view of the keyring,
    which ``dnf list installed`` never shows."""
    assert RpmGpgKeys.COMMAND == (
        "rpm -qa gpg-pubkey --queryformat '%{VERSION} %{RELEASE} %{SUMMARY}\n'"
    )


async def test_rpm_gpg_keys_seam_read_is_a_sorted_copy():
    """The seam answer sorts by keyid then release and is copy-on-read:
    mutating the returned entries never touches simulated state."""
    host = SimulatedHost(
        name="h",
        rpm_gpg_keys=[
            {"keyid": "4172a230", "release": "53a0b1c2", "summary": "gpg(datadog)"},
            {"keyid": "060a6254", "release": "572bde81", "summary": "gpg(docker)"},
        ],
    )
    result = await RpmGpgKeys.process(host)
    assert [entry["keyid"] for entry in result] == ["060a6254", "4172a230"]
    result[0]["summary"] = "mutated"
    result.append({"keyid": "ffffffff", "release": "0", "summary": ""})
    assert host.rpm_gpg_keys == [
        {"keyid": "4172a230", "release": "53a0b1c2", "summary": "gpg(datadog)"},
        {"keyid": "060a6254", "release": "572bde81", "summary": "gpg(docker)"},
    ]


# --- dnf.key operation (baseline parity) --------------------------------------

# A stand-in ASCII-armored GPG public key. dnf.key imports the *content* of an
# IO stream (never a path), so the tests only need opaque, stable bytes; the
# simulated host records them verbatim.
GPG_KEY_BYTES = (
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"mQIN reemote5 test key material\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)


async def test_dnf_key_yields_upload_then_import():
    """dnf.key is non-idempotent: it always reports a change and yields the
    spool transfer followed by the elevated import, reading no fact."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.key(ctx, src=stream)]
    assert ctx.changed
    assert ctx.reason == "import GPG key from <stream>"
    assert commands == [
        DnfKeyUploadCommand(src=stream, dest=_KEY_IMPORT_PATH),
        DnfKeyImportCommand(path=_KEY_IMPORT_PATH),
    ]


async def test_dnf_key_rejects_a_str_src():
    """The baseline's dnf.key takes a filename/URL the target host reads; reemote5
    takes an IO stream, so a str src raises rather than being treated as a
    controller-side path."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not support a filename/URL src"):
        _ = [c async for c in dnf.key(ctx, src="https://example.com/RPM-GPG-KEY")]


async def test_dnf_key_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot; op() already strips
    the name/sudo meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.key(ctx, src=stream, future_flag=True)]
    assert commands == [
        DnfKeyUploadCommand(src=stream, dest=_KEY_IMPORT_PATH),
        DnfKeyImportCommand(path=_KEY_IMPORT_PATH),
    ]


def test_dnf_key_upload_command_renders_as_an_unprivileged_scp():
    """The spool transfer has no shell(); it renders via __str__ and -- being a
    TransferCommand -- is never elevated, even though dnf.key's request is
    sudo=True for the import half. This is the command_shell guard: the log
    shows a plain ``scp ...``, never a misleading ``sudo scp ...``."""
    stream = io.BytesIO(GPG_KEY_BYTES)
    cmd = DnfKeyUploadCommand(src=stream, dest=_KEY_IMPORT_PATH)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <stream> -> {_KEY_IMPORT_PATH}"
    assert command_shell(cmd) == f"scp <stream> -> {_KEY_IMPORT_PATH}"
    assert command_shell(cmd, sudo=True) == f"scp <stream> -> {_KEY_IMPORT_PATH}"


def test_dnf_key_import_command_shell_and_elevation():
    """The import wraps ``rpm --import && rm`` in ``sh -c`` so both steps run
    under the elevated shell (a bare ``sudo ... && rm`` would leave the rm
    unprivileged), and shell-quotes the spool path."""
    cmd = DnfKeyImportCommand(path=_KEY_IMPORT_PATH)
    shell = cmd.shell()
    assert shell.startswith("sh -c ")
    inner = shlex.split(shell)[2]
    assert inner == f"rpm --import {_KEY_IMPORT_PATH} && rm -f {_KEY_IMPORT_PATH}"
    assert command_shell(cmd, sudo=True).startswith("sudo sh -c ")


async def test_dnf_key_simulated_seam_spools_then_imports():
    """send_command maps the transfer onto remote_files (via put_file) and the
    import onto imported_keys while clearing the spool file -- the two-step
    effect of one dnf.key request."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    await send_command(host, DnfKeyUploadCommand(src=stream, dest=_KEY_IMPORT_PATH))
    assert host.remote_files[_KEY_IMPORT_PATH] == GPG_KEY_BYTES
    await send_command(host, DnfKeyImportCommand(path=_KEY_IMPORT_PATH))
    assert host.imported_keys == {GPG_KEY_BYTES}
    assert _KEY_IMPORT_PATH not in host.remote_files


async def test_dnf_key_end_to_end_imports_over_run():
    """Through run(): the apply phase spools the stream by scp then imports it,
    so the simulated host records the key and the spool file is gone. The stream
    is one object captured by the deploy closure, so both phases pass the
    identical object and the determinism guard holds. The log shows the transfer
    unprivileged and only the import elevated."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def key_deploy(host):
        yield op(dnf.key, name="Import GPG key", src=stream, sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], key_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        DnfKeyUploadCommand(src=stream, dest=_KEY_IMPORT_PATH),
        DnfKeyImportCommand(path=_KEY_IMPORT_PATH),
    )
    assert host.imported_keys == {GPG_KEY_BYTES}
    assert _KEY_IMPORT_PATH not in host.remote_files
    log = format_log(report)
    assert f"    $ scp <stream> -> {_KEY_IMPORT_PATH}" in log
    assert "    $ sudo sh -c " in log


async def test_dnf_key_dry_run_plans_without_importing():
    """dry=True materializes the plan (non-idempotent -> changed, two commands)
    but sends nothing, so no key is imported and no spool file is written."""
    stream = io.BytesIO(GPG_KEY_BYTES)

    def key_deploy(host):
        yield op(dnf.key, name="Import GPG key", src=stream, sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        key_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert len(planned.commands) == 2
    assert host.imported_keys == set()
    assert host.remote_files == {}


async def test_dnf_key_fresh_stream_per_phase_trips_determinism():
    """The stream is compared by identity, so a deploy that builds a new one per
    invocation diverges between the prepare and apply phases and the guard
    raises -- the reason the example hoists its stream to a module constant."""

    def key_deploy(host):
        yield op(dnf.key, name="Import GPG key", src=io.BytesIO(GPG_KEY_BYTES))

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], key_deploy)
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- dnf.key present=False (reemote5 extension, Ansible rpm_key parity) -------

# The legacy Datadog signing key the ansible-datadog role removes by default
# (datadog_rpm_remove_keys: [4172A230]), as rpm stores it: lowercased short id
# plus the hex creation-timestamp release of the gpg-pubkey pseudo-package.
LEGACY_KEY_ENTRY = {
    "keyid": "4172a230",
    "release": "53a0b1c2",
    "summary": "gpg(Datadog, Inc. (Datadog RPM key) <package@datadoghq.com>)",
}


async def test_dnf_key_remove_present_yields_erase():
    """present=False with the key in the rpm keyring reports a change and
    yields the elevated erase of its gpg-pubkey pseudo-package; the uppercase
    key id the Ansible role passes is normalized to rpm's lowercase form."""
    host = SimulatedHost(name="h", rpm_gpg_keys=[dict(LEGACY_KEY_ENTRY)])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.key(ctx, keyid="4172A230", present=False)]
    assert ctx.changed
    assert ctx.reason == "GPG key 4172a230 present in the rpm keyring, will remove"
    assert commands == [DnfKeyRemoveCommand(keyid="4172a230", release="53a0b1c2")]


async def test_dnf_key_remove_absent_is_noop():
    """present=False with the key already gone is the idempotent no-op:
    no change, no commands, and the reason says so (Ansible rpm_key
    state=absent parity)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.key(ctx, keyid="4172A230", present=False)]
    assert not ctx.changed
    assert ctx.reason == "GPG key 4172a230 not in the rpm keyring"
    assert commands == []


async def test_dnf_key_remove_accepts_a_long_keyid():
    """A 16-digit long key id matches by its last 8 digits -- the short form
    rpm records as the pseudo-package's VERSION."""
    host = SimulatedHost(name="h", rpm_gpg_keys=[dict(LEGACY_KEY_ENTRY)])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.key(ctx, keyid="A1B2C3D44172A230", present=False)
    ]
    assert ctx.changed
    assert commands == [DnfKeyRemoveCommand(keyid="4172a230", release="53a0b1c2")]


async def test_dnf_key_remove_erases_every_release_of_a_keyid():
    """One short key id can name several imported pseudo-packages (distinct
    releases); each is erased, in the fact's sorted order."""
    second = {"keyid": "4172a230", "release": "60000000", "summary": "gpg(renewed)"}
    host = SimulatedHost(
        name="h", rpm_gpg_keys=[dict(second), dict(LEGACY_KEY_ENTRY)]
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.key(ctx, keyid="4172a230", present=False)]
    assert ctx.changed
    assert commands == [
        DnfKeyRemoveCommand(keyid="4172a230", release="53a0b1c2"),
        DnfKeyRemoveCommand(keyid="4172a230", release="60000000"),
    ]


async def test_dnf_key_remove_requires_keyid():
    """present=False without a keyid (or a blank one) raises -- there is no
    whole-keyring removal the way gpg.key has one."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="requires keyid when present=False"):
        _ = [c async for c in dnf.key(ctx, present=False)]
    with pytest.raises(ValueError, match="requires keyid when present=False"):
        _ = [c async for c in dnf.key(ctx, keyid="   ", present=False)]


async def test_dnf_key_remove_rejects_src():
    """The removal direction names a keyid, not key content: passing a stream
    alongside present=False raises rather than being silently ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="does not use src when present=False"):
        _ = [
            c
            async for c in dnf.key(
                ctx, src=io.BytesIO(GPG_KEY_BYTES), keyid="4172a230", present=False
            )
        ]


async def test_dnf_key_import_requires_src():
    """src became optional so present=False can omit it, but the import
    direction still requires the key content stream."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="requires src"):
        _ = [c async for c in dnf.key(ctx)]


async def test_dnf_key_remove_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot on the removal
    direction too; op() already strips the name/sudo meta-parameters."""
    host = SimulatedHost(name="h", rpm_gpg_keys=[dict(LEGACY_KEY_ENTRY)])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.key(
            ctx, keyid="4172a230", present=False, future_flag=True
        )
    ]
    assert commands == [DnfKeyRemoveCommand(keyid="4172a230", release="53a0b1c2")]


def test_dnf_key_remove_command_shell_and_elevation():
    """The erase renders ``rpm -e gpg-pubkey-<keyid>-<release>`` as a single
    command, so the driver's sudo prefix elevates the whole shell (no sh -c
    wrapper needed), and the pseudo-package name is shell-quoted."""
    cmd = DnfKeyRemoveCommand(keyid="4172a230", release="53a0b1c2")
    assert cmd.shell() == "rpm -e gpg-pubkey-4172a230-53a0b1c2"
    assert command_shell(cmd, sudo=True) == "sudo rpm -e gpg-pubkey-4172a230-53a0b1c2"
    assert DnfKeyRemoveCommand(keyid="a b", release="c d").shell() == (
        "rpm -e 'gpg-pubkey-a b-c d'"
    )


async def test_dnf_key_remove_simulated_seam_mutates_state():
    """send_command maps the erase onto rpm_gpg_keys: the named entry goes,
    the others stay, and imported_keys (the import-event observation store)
    is untouched."""
    other = {"keyid": "060a6254", "release": "572bde81", "summary": "gpg(docker)"}
    host = SimulatedHost(
        name="h", rpm_gpg_keys=[dict(LEGACY_KEY_ENTRY), dict(other)]
    )
    host.imported_keys.add(GPG_KEY_BYTES)
    await send_command(
        host, DnfKeyRemoveCommand(keyid="4172a230", release="53a0b1c2")
    )
    assert host.rpm_gpg_keys == [other]
    assert host.imported_keys == {GPG_KEY_BYTES}


async def test_dnf_key_import_seam_registers_an_rpmdb_entry():
    """The simulated import also records a stub RpmGpgKey entry (keyid and
    release derived from the content hash -- no OpenPGP parsing is modeled),
    so RpmGpgKeys reports the import and a re-import of the same bytes adds
    no duplicate."""
    host = SimulatedHost(name="h")
    stream = io.BytesIO(GPG_KEY_BYTES)
    await send_command(host, DnfKeyUploadCommand(src=stream, dest=_KEY_IMPORT_PATH))
    await send_command(host, DnfKeyImportCommand(path=_KEY_IMPORT_PATH))
    assert len(host.rpm_gpg_keys) == 1
    entry = host.rpm_gpg_keys[0]
    assert entry["summary"] == "gpg(reemote5 simulated key)"
    assert await RpmGpgKeys.process(host) == [entry]
    stream.seek(0)
    await send_command(host, DnfKeyUploadCommand(src=stream, dest=_KEY_IMPORT_PATH))
    await send_command(host, DnfKeyImportCommand(path=_KEY_IMPORT_PATH))
    assert len(host.rpm_gpg_keys) == 1


async def test_dnf_key_remove_end_to_end_over_run():
    """Through run(): the first removal request erases the seeded key (the
    log shows the elevated rpm -e), and the identical second request is a
    no-op in the apply phase because the fresh per-step fact cache sees the
    erase -- the idempotence the import direction deliberately lacks."""

    def key_deploy(host):
        yield op(
            dnf.key,
            name="Remove legacy GPG key",
            keyid="4172A230",
            present=False,
            sudo=True,
        )
        yield op(
            dnf.key,
            name="Remove legacy GPG key again",
            keyid="4172A230",
            present=False,
            sudo=True,
        )

    host = SimulatedHost(name="h", rpm_gpg_keys=[dict(LEGACY_KEY_ENTRY)])
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        key_deploy,
        host_factory=lambda entry: host,
    )
    first, second = report.log
    assert first.changed
    assert first.commands == (
        DnfKeyRemoveCommand(keyid="4172a230", release="53a0b1c2"),
    )
    assert not second.changed
    assert second.commands == ()
    assert host.rpm_gpg_keys == []
    # In the prepare phase nothing is sent, so both requests plan as changes.
    assert all(planned.changed for planned in plans["h"])
    log = format_log(report)
    assert "    $ sudo rpm -e gpg-pubkey-4172a230-53a0b1c2" in log


async def test_dnf_key_remove_dry_run_plans_without_removing():
    """dry=True materializes the removal plan (changed, one command) but
    sends nothing, so the seeded key stays in the simulated rpm keyring."""

    def key_deploy(host):
        yield op(
            dnf.key,
            name="Remove legacy GPG key",
            keyid="4172A230",
            present=False,
            sudo=True,
        )

    host = SimulatedHost(name="h", rpm_gpg_keys=[dict(LEGACY_KEY_ENTRY)])
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        key_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert len(planned.commands) == 1
    assert host.rpm_gpg_keys == [LEGACY_KEY_ENTRY]


# --- dnf.module operation (baseline parity) -----------------------------------

# The module/stream the baseline's own dnf.module operation tests use: enabling
# postgresql:16 and disabling ruby. Reused for command/reason parity below.
MODULE_NAME = "postgresql"
MODULE_STREAM = "16"
DISABLE_MODULE_NAME = "ruby"


async def test_dnf_module_enable_new_yields_command():
    """enabled=True with a module not yet enabled on the stream yields the
    ``dnf module enable`` command (the baseline's ``enable_new`` case)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.module(ctx, module=MODULE_NAME, stream=MODULE_STREAM)
    ]
    assert ctx.changed
    assert ctx.reason == f"dnf module {MODULE_NAME}:{MODULE_STREAM} not enabled, will enable"
    assert commands == [
        DnfModuleEnableCommand(module=MODULE_NAME, stream=MODULE_STREAM)
    ]


async def test_dnf_module_enable_already_enabled_noop():
    """A module already enabled on exactly the requested stream is a no-op
    (the baseline's ``enable_already_enabled`` case, reason matches its noop)."""
    host = SimulatedHost(name="h", enabled_modules={MODULE_NAME: MODULE_STREAM})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.module(ctx, module=MODULE_NAME, stream=MODULE_STREAM)
    ]
    assert not ctx.changed
    assert ctx.reason == f"dnf module {MODULE_NAME}:{MODULE_STREAM} is already enabled"
    assert commands == []


async def test_dnf_module_enable_switches_stream():
    """A module enabled on a *different* stream re-enables onto the requested
    one (the baseline's ``enable_different_stream`` case); the reason names the
    switch."""
    host = SimulatedHost(name="h", enabled_modules={MODULE_NAME: "15"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.module(ctx, module=MODULE_NAME, stream=MODULE_STREAM)
    ]
    assert ctx.changed
    assert ctx.reason == (
        f"dnf module {MODULE_NAME} is enabled on stream 15, will switch to "
        f"{MODULE_STREAM}"
    )
    assert commands == [
        DnfModuleEnableCommand(module=MODULE_NAME, stream=MODULE_STREAM)
    ]


async def test_dnf_module_enable_requires_stream():
    """enabled=True with no stream raises -- the baseline's ``OperationValueError``
    (a ValueError subclass) maps to reemote5's plain ValueError."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="stream is required when enabled=True"):
        _ = [c async for c in dnf.module(ctx, module=MODULE_NAME)]


async def test_dnf_module_disable_new_yields_command():
    """enabled=False with a module not yet disabled yields the ``dnf module
    disable`` command (the baseline's ``disable_new`` case)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.module(ctx, module=DISABLE_MODULE_NAME, enabled=False)
    ]
    assert ctx.changed
    assert ctx.reason == f"dnf module {DISABLE_MODULE_NAME} not disabled, will disable"
    assert commands == [DnfModuleDisableCommand(module=DISABLE_MODULE_NAME)]


async def test_dnf_module_disable_already_disabled_noop():
    """A module already disabled is a no-op (the baseline's ``disable_already_disabled``
    case, reason matches its noop)."""
    host = SimulatedHost(name="h", disabled_modules=[DISABLE_MODULE_NAME])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in dnf.module(ctx, module=DISABLE_MODULE_NAME, enabled=False)
    ]
    assert not ctx.changed
    assert ctx.reason == f"dnf module {DISABLE_MODULE_NAME} is already disabled"
    assert commands == []


async def test_dnf_module_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot; op() already strips
    the name/sudo meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in dnf.module(
            ctx, module=MODULE_NAME, stream=MODULE_STREAM, future_flag=True
        )
    ]
    assert commands == [
        DnfModuleEnableCommand(module=MODULE_NAME, stream=MODULE_STREAM)
    ]


def test_dnf_module_enable_command_shell_and_elevation():
    """The enable descriptor renders ``dnf module enable -y <module>:<stream>``,
    elevates as a whole under sudo, and shell-quotes the joined argument."""
    cmd = DnfModuleEnableCommand(module=MODULE_NAME, stream=MODULE_STREAM)
    assert cmd.shell() == f"dnf module enable -y {MODULE_NAME}:{MODULE_STREAM}"
    assert command_shell(cmd, sudo=True) == (
        f"sudo dnf module enable -y {MODULE_NAME}:{MODULE_STREAM}"
    )
    # A module/stream with a space is shell-quoted as one argument.
    assert DnfModuleEnableCommand(module="my mod", stream="1 6").shell() == (
        "dnf module enable -y 'my mod:1 6'"
    )


def test_dnf_module_disable_command_shell_and_elevation():
    """The disable descriptor renders ``dnf module disable -y <module>``,
    elevates under sudo, and shell-quotes the module name."""
    cmd = DnfModuleDisableCommand(module=DISABLE_MODULE_NAME)
    assert cmd.shell() == f"dnf module disable -y {DISABLE_MODULE_NAME}"
    assert command_shell(cmd, sudo=True) == (
        f"sudo dnf module disable -y {DISABLE_MODULE_NAME}"
    )
    assert DnfModuleDisableCommand(module="my mod").shell() == (
        "dnf module disable -y 'my mod'"
    )


async def test_dnf_module_enable_simulated_seam_mutates_state():
    """``send_command`` maps the enable descriptor onto the simulated state: the
    stream lands in ``enabled_modules`` and any disabled marker is cleared, so
    the two module facts stay consistent."""
    host = SimulatedHost(name="h", disabled_modules=[MODULE_NAME])
    await send_command(
        host, DnfModuleEnableCommand(module=MODULE_NAME, stream=MODULE_STREAM)
    )
    assert host.enabled_modules == {MODULE_NAME: MODULE_STREAM}
    assert MODULE_NAME not in host.disabled_modules


async def test_dnf_module_disable_simulated_seam_mutates_state():
    """``send_command`` maps the disable descriptor onto the simulated state: the
    module lands in ``disabled_modules`` (sorted) and any enabled stream is
    cleared."""
    host = SimulatedHost(name="h", enabled_modules={MODULE_NAME: MODULE_STREAM})
    await send_command(host, DnfModuleDisableCommand(module=MODULE_NAME))
    assert host.enabled_modules == {}
    assert host.disabled_modules == [MODULE_NAME]


async def test_dnf_module_end_to_end_enable_then_noop():
    """Through run(): the first apply enables the stream into the simulated
    state (elevated, per the sudo flag); a second run sees it enabled and is a
    no-op. The request kwargs are plain strings, so the determinism guard holds
    with no module-level constant."""

    def module_deploy(host):
        yield op(
            dnf.module,
            name="Enable PostgreSQL 16",
            module=MODULE_NAME,
            stream=MODULE_STREAM,
            sudo=True,
        )

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's enable

    _plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], module_deploy, host_factory=factory
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        DnfModuleEnableCommand(module=MODULE_NAME, stream=MODULE_STREAM),
    )
    assert host.enabled_modules == {MODULE_NAME: MODULE_STREAM}
    assert (
        f"sudo dnf module enable -y {MODULE_NAME}:{MODULE_STREAM}" in format_log(report)
    )

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], module_deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == f"dnf module {MODULE_NAME}:{MODULE_STREAM} is already enabled"


async def test_dnf_module_enable_then_noop_in_one_deploy():
    """The apply phase's fresh per-step fact cache sees the first enable, so a
    second identical enable in the same deploy is a no-op -- while the prepare
    plan (which never sends) shows both as changes against the empty state."""

    def module_deploy(host):
        yield op(dnf.module, name="enable", module=MODULE_NAME, stream=MODULE_STREAM)
        yield op(dnf.module, name="re-enable", module=MODULE_NAME, stream=MODULE_STREAM)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], module_deploy)
    assert plans["h"][0].changed  # enable: empty state -> not enabled
    assert plans["h"][1].changed  # prepare never sends, so still not enabled
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == f"dnf module {MODULE_NAME}:{MODULE_STREAM} is already enabled"
    assert host.enabled_modules == {MODULE_NAME: MODULE_STREAM}


# --- dnf.update operation (baseline parity) -----------------------------------


async def test_dnf_update_always_yields_and_changes():
    """``dnf.update`` is non-idempotent: it reads no fact, always reports a
    change and yields one bare ``DnfUpdateCommand`` (the baseline's ``dnf update -y``)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.update(ctx)]
    assert ctx.changed
    assert ctx.reason == "dnf update"
    assert commands == [DnfUpdateCommand()]
    assert commands[0].shell() == "dnf update -y"


async def test_dnf_update_is_non_idempotent_with_packages_installed():
    """A host that already has packages still plans an update: there is no fact
    to compare against, so the operation never becomes a no-op -- matching
    the baseline's ``is_idempotent=False``."""
    host = SimulatedHost(name="h", packages={"nginx": [""], "vim": [""]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.update(ctx)]
    assert ctx.changed
    assert commands == [DnfUpdateCommand()]


async def test_dnf_update_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot; op() already strips
    the name/sudo meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.update(ctx, future_flag=True)]
    assert commands == [DnfUpdateCommand()]


def test_dnf_update_command_shell_and_elevation():
    """The bare descriptor renders ``dnf update -y`` and elevates as a whole
    under sudo/su (updating the system needs root)."""
    cmd = DnfUpdateCommand()
    assert cmd.shell() == "dnf update -y"
    assert command_shell(cmd, sudo=True) == "sudo dnf update -y"
    assert command_shell(cmd, su=True) == "su -c 'dnf update -y'"


async def test_dnf_update_simulated_seam_is_recognised_and_a_noop():
    """``send_command`` maps the descriptor onto the seam without error: the
    simulated update resolves no new versions, so it leaves the in-memory
    state untouched rather than raising ``TypeError`` for an unknown command."""
    host = SimulatedHost(name="h", packages={"nginx": [""]})
    await send_command(host, DnfUpdateCommand())
    assert host.packages == {"nginx": [""]}


async def test_dnf_update_end_to_end_over_run():
    """Through run(): the plan reports the change and the apply log shows the
    elevated ``dnf update -y`` (the request is flagged ``sudo=True``)."""

    def update_deploy(host):
        yield op(dnf.update, name="Update all packages", sudo=True)

    _plans, report, _hosts = await run(
        [{"host": "a", "connection": {}}],
        update_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (DnfUpdateCommand(),)
    assert "$ sudo dnf update -y" in format_log(report)


async def test_dnf_update_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the update is planned as a change but
    there is no apply report, so nothing runs on the host."""

    def update_deploy(host):
        yield op(dnf.update, name="Update all packages", sudo=True)

    host = SimulatedHost(name="h", packages={"nginx": [""]})
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        update_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.reason == "dnf update"
    assert planned.commands == (DnfUpdateCommand(),)


async def test_files_link_remove_when_present():
    # ``files.link`` stats the path through the ``Link`` fact, so the stat
    # row -- not just the ``FileLink`` target map -- has to be seeded.
    host = SimulatedHost(
        name="h",
        links={NGINX_DEFAULT_SITE: DEFAULT_TARGET},
        link_stats={
            NGINX_DEFAULT_SITE: {
                "mode": 777,
                "user": "root",
                "group": "root",
                "link_target": DEFAULT_TARGET,
            }
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in files.link(ctx, path=NGINX_DEFAULT_SITE, present=False)]
    assert ctx.changed
    assert ctx.reason == f"{NGINX_DEFAULT_SITE} present, will remove"
    assert commands == [LinkCommand(path=NGINX_DEFAULT_SITE, target=None)]


async def test_files_file_remove_when_present():
    # ``files.file`` stats the path through the ``File`` fact, so the row --
    # not just the ``FileExists`` marker -- has to be seeded.
    host = SimulatedHost(
        name="h",
        existing_files={HTTPD_WELCOME_CONF},
        file_stats={HTTPD_WELCOME_CONF: {"mode": 644, "user": "root", "group": "root"}},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in files.file(ctx, path=HTTPD_WELCOME_CONF, present=False)]
    assert ctx.changed
    assert ctx.reason == f"{HTTPD_WELCOME_CONF} present, will remove"
    assert commands == [FileCommand(path=HTTPD_WELCOME_CONF, present=False)]


async def test_files_file_no_change_when_already_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in files.file(ctx, path=HTTPD_WELCOME_CONF, present=False)]
    assert not ctx.changed
    # recorded even when unchanged (10)
    assert ctx.reason == f"file {HTTPD_WELCOME_CONF} does not exist"
    assert commands == []


# --- Apply phase: sequential facts and lockstep (8.2, 9) ---------------------


async def test_apply_sequential_facts_the_section_9_trace():
    _plans, report, (web1, web2) = await run(
        WEB_INVENTORY, deploy, host_factory=simulated_web_factory
    )

    by_host: dict[str, list] = {}
    for entry in report.log:
        by_host.setdefault(entry.host, []).append(entry)

    # web1: nginx installed, which creates the default-site link; the next
    # operation's fresh fact cache sees it and removes it.
    w1_install, w1_link = by_host["web1"]
    assert w1_install.changed and w1_install.commands == (
        DnfInstallCommand(packages=("nginx",)),
    )
    assert w1_link.changed and w1_link.reason == f"{NGINX_DEFAULT_SITE} present, will remove"
    assert w1_link.commands == (LinkCommand(path=NGINX_DEFAULT_SITE, target=None),)

    # web2: nginx already present (no-op), but the default-site link
    # still exists pre-deployment, so its removal is a real change.
    w2_install, w2_link = by_host["web2"]
    assert not w2_install.changed and w2_install.reason == "all packages present"
    assert (
        w2_link.changed
        and w2_link.reason == f"{NGINX_DEFAULT_SITE} present, will remove"
    )

    # Final state: nginx installed on both, default site gone on both.
    for host in (web1, web2):
        assert host.packages == {"nginx": [""]}
        assert host.links == {}


async def test_apply_sequential_facts_with_file_side_effects():
    """The httpd scenario: installing the package drops welcome.conf, which
    the next operation's fresh fact cache sees and removes."""

    def httpd_deploy(host):
        yield op(dnf.packages, name="Install httpd", packages=("httpd",))
        yield op(
            files.file,
            name="Remove welcome configuration",
            path=HTTPD_WELCOME_CONF,
            present=False,
        )

    host = SimulatedHost(name="h", installs_files={"httpd": {HTTPD_WELCOME_CONF}})
    runtimes, plans = await run_prepare([host], httpd_deploy)
    # During prepare nothing has run, so the file is not there yet.
    assert not plans["h"][1].changed
    assert plans["h"][1].reason == f"file {HTTPD_WELCOME_CONF} does not exist"

    report = await run_apply(runtimes, plans=plans)
    install, remove = report.log
    assert install.changed
    assert remove.changed and remove.reason == f"{HTTPD_WELCOME_CONF} present, will remove"
    assert remove.commands == (FileCommand(path=HTTPD_WELCOME_CONF, present=False),)
    # Final state: httpd installed, welcome.conf gone.
    assert host.packages == {"httpd": [""]}
    assert host.existing_files == set()


async def test_apply_is_lockstep_across_hosts():
    """All hosts execute their Nth request before any pulls request N+1."""
    hosts = [
        SimulatedHost(name=name, installs_links={"nginx": {"/l": "/t"}})
        for name in ("a", "b", "c")
    ]
    runtimes, plans = await run_prepare(hosts, deploy)
    report = await run_apply(runtimes, plans=plans)
    # The driver executes step by step over all hosts, so the log must be
    # grouped by operation index: three installs, then three link removals.
    assert [entry.name for entry in report.log] == [
        "Install nginx",
        "Install nginx",
        "Install nginx",
        "Remove default site",
        "Remove default site",
        "Remove default site",
    ]


async def test_prepare_and_apply_fan_out_across_hosts():
    """``run_prepare`` and ``run_apply`` step 2 gather per-host work with
    ``asyncio.gather``, so hosts overlap within a phase/step instead of
    running one at a time. A probe operation tracks the in-flight high-water
    mark across hosts; a serial loop would peak at 1, the gather peaks at 2.
    Ordering stays deterministic (input/host order) despite the overlap."""
    in_flight = 0
    peak = 0

    async def probe_op(ctx):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)  # yield so the other host's step starts
        in_flight -= 1
        # A pure probe: it changes nothing, so it yields no commands. The
        # conditional yield is what makes probe_op an async generator.
        ctx.changed = False
        ctx.reason = "concurrency probe"
        if ctx.changed:
            yield DnfInstallCommand(packages=())

    def probe_deploy(host):
        yield op(probe_op, name="probe")

    hosts = [SimulatedHost(name="a"), SimulatedHost(name="b")]

    runtimes, plans = await run_prepare(hosts, probe_deploy)
    assert peak == 2  # both hosts' prepares overlapped
    assert list(plans) == ["a", "b"]  # deterministic, input order

    peak = 0  # isolate the apply phase
    report = await run_apply(runtimes, plans=plans)
    assert peak == 2  # both hosts' step-2 execution overlapped
    assert [entry.host for entry in report.log] == ["a", "b"]


async def test_hosts_finishing_at_different_steps():
    """A host whose generator ends early does not stall the others."""

    def uneven_deploy(host):
        yield op(dnf.packages, name="Install nginx", packages=("nginx",))
        if host.name == "b":
            yield op(dnf.packages, name="Install vim", packages=("vim",))

    _plans, report, (a, b) = await run(
        [{"host": "a", "connection": {}}, {"host": "b", "connection": {}}],
        uneven_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    assert [entry.name for entry in report.log] == [
        "Install nginx",
        "Install nginx",
        "Install vim",
    ]
    assert a.packages == {"nginx": [""]}
    assert b.packages == {"nginx": [""], "vim": [""]}


async def test_sudo_flag_flows_into_the_apply_log():
    """The request's sudo flag is recorded on the log entry and rendered."""

    def sudo_deploy(host):
        yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)

    _plans, report, _hosts = await run(
        [{"host": "a", "connection": {}}],
        sudo_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    (entry,) = report.log
    assert entry.sudo is True
    # The simulated seam ignores privilege (nginx is still installed), but the
    # rendered log shows the elevated shell that would go over SSH.
    assert _hosts[0].packages == {"nginx": [""]}
    assert "sudo dnf install -y nginx" in format_log(report)


async def test_sudo_user_from_privilege_flows_into_the_apply_log():
    """The run-as user is read from the inventory ``privilege`` section and
    rendered in the log; the op itself only carries ``sudo=True``."""

    def sudo_deploy(host):
        yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)

    _plans, report, _hosts = await run(
        [{"host": "a", "connection": {}, "privilege": {"sudo_user": "deploy"}}],
        sudo_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    (entry,) = report.log
    assert entry.sudo_user == "deploy"
    assert "sudo -u deploy dnf install -y nginx" in format_log(report)


async def test_sudo_password_never_appears_in_the_apply_log():
    """The password lives only in the inventory and the execution string; the
    rendered log shows the credential-free elevated command, never the secret."""

    def sudo_deploy(host):
        yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)

    _plans, report, _hosts = await run(
        [
            {
                "host": "a",
                "connection": {},
                "privilege": {"sudo_user": "deploy", "sudo_password": "s3cr3t"},
            }
        ],
        sudo_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    rendered = format_log(report)
    assert "s3cr3t" not in rendered
    assert "sudo -u deploy dnf install -y nginx" in rendered


async def test_su_user_from_privilege_flows_into_the_apply_log():
    """The su target is read from the inventory ``privilege`` section and
    rendered in the log; the op itself only carries ``su=True``."""

    def su_deploy(host):
        yield op(dnf.packages, name="Install nginx", packages=("nginx",), su=True)

    _plans, report, _hosts = await run(
        [{"host": "a", "connection": {}, "privilege": {"su_user": "deploy"}}],
        su_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    (entry,) = report.log
    assert entry.su is True
    assert entry.su_user == "deploy"
    assert "su deploy -c 'dnf install -y nginx'" in format_log(report)


async def test_su_password_never_appears_in_the_apply_log():
    def su_deploy(host):
        yield op(dnf.packages, name="Install nginx", packages=("nginx",), su=True)

    _plans, report, _hosts = await run(
        [
            {
                "host": "a",
                "connection": {},
                "privilege": {"su_user": "deploy", "su_password": "s3cr3t"},
            }
        ],
        su_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    rendered = format_log(report)
    assert "s3cr3t" not in rendered
    assert "su deploy -c 'dnf install -y nginx'" in rendered


# --- Prepare phase / --dry (8.1, 10) ------------------------------------------


async def test_dry_run_reports_plan_without_touching_hosts():
    plans, report, (web1, _web2) = await run(
        WEB_INVENTORY, deploy, host_factory=simulated_web_factory, dry=True
    )

    assert report is None
    # The plan reflects pre-deployment state: on web1 the link does not
    # exist yet, so the removal is reported as a no-op (section 9 prepare).
    assert [p.changed for p in plans["web1"]] == [True, False]
    assert plans["web1"][0].reason == "1 package(s) to install"
    assert plans["web1"][1].reason == f"link {NGINX_DEFAULT_SITE} does not exist"
    # web2: nginx present (no change), link present (would be removed).
    assert [p.changed for p in plans["web2"]] == [False, True]
    assert plans["web2"][1].reason == f"{NGINX_DEFAULT_SITE} present, will remove"
    # Hosts untouched.
    assert web1.packages == {}
    assert web1.links == {}


async def test_prepare_materializes_commands_for_changed_ops():
    plans, _, _hosts = await run(
        WEB_INVENTORY[:1], deploy, host_factory=simulated_web_factory, dry=True
    )
    install = plans["web1"][0]
    assert install.commands == (DnfInstallCommand(packages=("nginx",)),)


# --- Determinism check (11.1) --------------------------------------------------


async def test_determinism_violation_is_a_hard_error():
    """Branching on a mutable fact diverges between phases -> hard error."""

    def nondeterministic_deploy(host):
        yield op(dnf.packages, name="Install nginx", packages=("nginx",))
        if "nginx" in host.packages:  # mutable-fact branch: forbidden
            yield op(dnf.packages, name="Install vim", packages=("vim",))
        else:
            yield op(dnf.packages, name="Install emacs", packages=("emacs",))

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], nondeterministic_deploy)
    with pytest.raises(DeterminismError, match="diverges"):
        await run_apply(runtimes, plans=plans)
