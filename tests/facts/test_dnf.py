"""Tests for the dnf facts (pyinfra parity).

Covers the read-only state discovery in ``reemote5.facts.dnf``:
``DnfRepositories``, ``DnfEnabledModules`` and ``DnfDisabledModules``. Each
fact is exercised both over the real-transport path (parsing canned shell
output) and through the ``SimulatedHost`` seam.

Fact gathering is async -- a fact's ``process`` is a coroutine -- and
``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

from reemote5.facts import DnfDisabledModules, DnfEnabledModules, DnfRepositories
from reemote5.hosts import Host
from tests.simulated_host import SimulatedHost

# --- DnfRepositories fact (pyinfra parity) -----------------------------------

# The example result from the pyinfra ``dnf.DnfRepositories`` documentation: a
# single AlmaLinux BaseOS repository parsed out of its ``.repo`` file.
ALMALINUX_BASEOS_REPO = {
    "repoid": "baseos",
    "name": "AlmaLinux $releasever - BaseOS",
    "mirrorlist": "https://mirrors.almalinux.org/mirrorlist/$releasever/baseos",
    "enabled": "1",
    "gpgcheck": "1",
    "countme": "1",
    "gpgkey": "file:///etc/pki/rpm-gpg/RPM-GPG-KEY-AlmaLinux-9",
    "metadata_expire": "86400",
    "enabled_metadata": "1",
    "filename": "/etc/yum.repos.d/almalinux.repo",
}


async def test_dnf_repositories_parses_the_documented_example():
    """Over the real-transport path, ``DnfRepositories`` parses a cat'd
    ``.repo`` file into pyinfra's documented example result."""

    class CannedShellHost(Host):
        """Returns fixed stdout from ``run_shell`` instead of connecting."""

        def __init__(self, stdout: str):
            super().__init__(name="canned")
            self._stdout = stdout

        async def run_shell(self, shell: str) -> str:
            return self._stdout

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


# --- DnfEnabledModules fact (pyinfra parity) -----------------------------------

# The example result from the pyinfra ``dnf.DnfEnabledModules`` documentation,
# produced by parsing the ``dnf module list --enabled`` table below (the
# ``multiple_modules`` case from pyinfra's own fact tests).
DOCUMENTED_ENABLED_MODULES = {"postgresql": "16", "nodejs": "20"}

DNF_MODULE_LIST_ENABLED_OUTPUT = """\
Name         Stream        Profiles                                  Summary
nodejs       20 [e]        common [d], development, minimal, s2i     Javascript runtime
postgresql   16 [e]        client, server [d]                        PostgreSQL server and client module

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled"""


async def test_dnf_enabled_modules_parses_the_documented_example():
    """Over the real-transport path, ``DnfEnabledModules`` parses a
    ``dnf module list --enabled`` table into pyinfra's documented example
    result, skipping the header, blank lines and the trailing hint line."""

    class CannedShellHost(Host):
        """Returns fixed stdout from ``run_shell`` instead of connecting."""

        def __init__(self, stdout: str):
            super().__init__(name="canned")
            self._stdout = stdout

        async def run_shell(self, shell: str) -> str:
            return self._stdout

    host = CannedShellHost(DNF_MODULE_LIST_ENABLED_OUTPUT)
    assert await DnfEnabledModules.process(host) == DOCUMENTED_ENABLED_MODULES


async def test_dnf_enabled_modules_reads_the_simulated_seam():
    """A SimulatedHost exposes ``enabled_modules`` directly, bypassing the
    shell."""
    host = SimulatedHost(name="h", enabled_modules=DOCUMENTED_ENABLED_MODULES)
    assert await DnfEnabledModules.process(host) == DOCUMENTED_ENABLED_MODULES


# --- DnfDisabledModules fact (pyinfra parity) ---------------------------------

# The example result from the pyinfra ``dnf.DnfDisabledModules`` documentation
# lists ``["ruby", "php"]``; the fact returns the names *sorted*, so the same
# two modules come back as ``["php", "ruby"]``.
DOCUMENTED_DISABLED_MODULES = ["php", "ruby"]

DNF_MODULE_LIST_DISABLED_OUTPUT = """\
Name         Stream        Profiles              Summary
php          8.2 [x]       common [d]            PHP scripting language
ruby         3.1 [x]       common [d]            An interpreter of object-oriented scripting language

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled"""


async def test_dnf_disabled_modules_parses_the_documented_example():
    """Over the real-transport path, ``DnfDisabledModules`` parses a
    ``dnf module list --disabled`` table into pyinfra's documented example
    module names (sorted), skipping the header, blank lines and the trailing
    hint line."""

    class CannedShellHost(Host):
        """Returns fixed stdout from ``run_shell`` instead of connecting."""

        def __init__(self, stdout: str):
            super().__init__(name="canned")
            self._stdout = stdout

        async def run_shell(self, shell: str) -> str:
            return self._stdout

    host = CannedShellHost(DNF_MODULE_LIST_DISABLED_OUTPUT)
    assert await DnfDisabledModules.process(host) == DOCUMENTED_DISABLED_MODULES


async def test_dnf_disabled_modules_dedups_across_streams():
    """A module disabled on several streams is listed once, and the names come
    back sorted (the ``multi_stream_dedup`` case from pyinfra's fact tests)."""

    class CannedShellHost(Host):
        """Returns fixed stdout from ``run_shell`` instead of connecting."""

        def __init__(self, stdout: str):
            super().__init__(name="canned")
            self._stdout = stdout

        async def run_shell(self, shell: str) -> str:
            return self._stdout

    stdout = """\
Name         Stream        Profiles              Summary
postgresql   15 [x]        client, server [d]    PostgreSQL server and client module
postgresql   16 [x]        client, server [d]    PostgreSQL server and client module
ruby         3.1 [x]       common [d]            An interpreter of object-oriented scripting language

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled"""
    host = CannedShellHost(stdout)
    assert await DnfDisabledModules.process(host) == ["postgresql", "ruby"]


async def test_dnf_disabled_modules_reads_the_simulated_seam():
    """A SimulatedHost exposes ``disabled_modules`` directly, bypassing the
    shell."""
    host = SimulatedHost(name="h", disabled_modules=DOCUMENTED_DISABLED_MODULES)
    assert await DnfDisabledModules.process(host) == DOCUMENTED_DISABLED_MODULES
