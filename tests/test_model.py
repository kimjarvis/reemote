"""Tests for the generator-based two-phase execution model.

Covers the properties from the design document: request descriptor
semantics (4.1), fact caching (4.4), sequential "act, then read"
semantics in apply (8.2/9), cross-host lockstep (8.2), --dry behavior
(8.1), determinism checking (11.1) and change reporting (10).

The apply path (``send_command`` and the drivers calling it) is async in
preparation for an asyncssh transport, and so is fact gathering: a fact's
``process`` is a coroutine, making ``FactCache.get`` and the operations that
await it async too. ``asyncio_mode = "auto"`` lets the async tests below run
without decorators.
"""

import copy
from collections.abc import Generator
from pathlib import Path

import pytest

from reemote5.core import FactCache, OpContext, OperationRequest, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.facts import DnfEnabledModules, DnfPackages, DnfRepositories, FileLink
from reemote5.hosts import Host, InventoryError, validate_inventory
from reemote5.operations import dnf, files
from reemote5.operations.dnf import (
    DnfCleanCommand,
    DnfInstallCommand,
    DnfRemoveCommand,
    DnfUpdateCommand,
)
from reemote5.operations.files import FileCommand, LinkCommand
from tests.simulated_host import SimulatedHost

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
        "session": {"term_type": "xterm-color"},
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "session": {"term_type": "xterm-color", "term_size": 80},
    },
]

# Simulated pre-deployment state per host name (test-framework data, NOT
# inventory): web1 fresh, web2 already configured (design doc section 9).
WEB_INITIAL_STATE = {
    "web1": {"installs_links": {"nginx": {NGINX_DEFAULT_SITE: DEFAULT_TARGET}}},
    "web2": {
        "packages": {"nginx"},
        "links": {NGINX_DEFAULT_SITE: DEFAULT_TARGET},
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
        src=NGINX_DEFAULT_SITE,
        present=False,
    )


# --- Inventory / Host (library input) ----------------------------------------


def test_host_from_inventory_maps_known_and_extra_keys():
    host = Host.from_inventory(
        {
            "host": "web1",
            "connection": {"key_path": "~/.ssh/id_ed25519"},
            "session": {"term_type": "xterm-color"},
            "sudo": {"password": "secret"},
            "groups": ["web"],  # unrecognised key -> preserved in extra
        }
    )
    assert host.name == "web1"
    assert host.connection == {"key_path": "~/.ssh/id_ed25519"}
    assert host.session == {"term_type": "xterm-color"}
    assert host.sudo == {"password": "secret"}
    assert host.extra == {"groups": ["web"]}


async def test_inventory_host_reaches_the_real_transport_boundary():
    """A plain Host carries connection/session into facts and operations,
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
    assert request.sudo is True
    assert "sudo" not in request.kwargs
    assert request.kwargs == {"packages": ("nginx",)}
    # Unprivileged by default.
    assert op(dnf.packages, packages=("nginx",)).sudo is False


def test_sudo_participates_in_request_equality_and_hashing():
    plain = op(dnf.packages, name="Install nginx", packages=("nginx",))
    elevated = op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)
    other_elevated = op(
        dnf.packages, name="Install nginx", packages=("nginx",), sudo=True
    )
    assert plain != elevated  # a sudo difference must not pair in lockstep
    assert elevated == other_elevated
    assert hash(elevated) == hash(other_elevated)


# --- sudo elevation on the command path --------------------------------------


def test_command_shell_applies_the_sudo_prefix():
    cmd = DnfInstallCommand(packages=("nginx",))
    assert command_shell(cmd) == "dnf install -y nginx"
    assert command_shell(cmd, sudo=True) == "sudo dnf install -y nginx"


async def test_send_command_prefixes_sudo_on_the_real_transport_path():
    """A plain Host (no ``execute_command`` seam) gets the elevated shell."""

    class RecordingHost(Host):
        """Records ``run_shell`` instead of opening a real asyncssh connection."""

        def __init__(self):
            super().__init__(name="rec")
            self.sent: list[str] = []

        async def run_shell(self, shell: str) -> str:
            self.sent.append(shell)
            return ""

    host = RecordingHost()
    await send_command(host, DnfInstallCommand(packages=("nginx",)), sudo=True)
    await send_command(host, DnfInstallCommand(packages=("nginx",)))
    assert host.sent == ["sudo dnf install -y nginx", "dnf install -y nginx"]


# --- FactCache (4.4) ---------------------------------------------------------


async def test_fact_cache_caches_per_key():
    host = SimulatedHost(name="h", packages={"nginx"})
    cache = FactCache(host, phase="prepare")
    assert await cache.get(DnfPackages) == {"nginx"}

    host.packages.add("vim")
    # Cached: the mutation is not visible through the same cache...
    assert await cache.get(DnfPackages) == {"nginx"}
    # ...but a fresh cache sees it, which is exactly the per-step
    # recreation the apply driver relies on.
    assert await FactCache(host, phase="apply").get(DnfPackages) == {"nginx", "vim"}


async def test_fact_cache_keys_include_args():
    host = SimulatedHost(name="h", links={"/a": "/target-a"})
    cache = FactCache(host, phase="prepare")
    assert await cache.get(FileLink, "/a") == "/target-a"
    assert await cache.get(FileLink, "/b") is None


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


# --- Operations (6) ----------------------------------------------------------


async def test_dnf_packages_sets_changed_and_yields_command():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx, packages=("nginx", "vim"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [DnfInstallCommand(packages=("nginx", "vim"))]


async def test_dnf_packages_no_change_reason_recorded():
    host = SimulatedHost(name="h", packages={"nginx"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in dnf.packages(ctx, packages=("nginx",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged (10)
    assert commands == []


# --- dnf.packages options (pyinfra parity) ------------------------------------


async def test_dnf_packages_accepts_a_bare_string():
    """pyinfra accepts ``packages="nginx"`` as shorthand for a one-item list."""
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
    """``update=True`` alone mirrors pyinfra's stateless ``dnf.update``."""
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
    host = SimulatedHost(name="h", packages={"nginx"})
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
    host = SimulatedHost(name="h", packages={"nginx"})
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
    name-only fact treats any installed version as present."""
    host = SimulatedHost(name="h", packages={"nginx"})
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


async def test_dnf_packages_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
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
    assert host.packages == {"nginx"}
    log = format_log(report)
    assert "$ dnf clean all" in log
    assert "$ dnf update -y" in log
    assert "$ dnf install -y nginx" in log


async def test_dnf_version_pin_install_simulates_by_name():
    """The simulated seam strips version pins, so the name-only state and
    the fresh-fact-cache property hold for pinned installs too."""

    def pinned_deploy(host):
        yield op(dnf.packages, name="Install pinned nginx", packages=("nginx=1.23.0",))

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], pinned_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert report.log[0].changed
    assert host.packages == {"nginx"}
    # A second prepare sees the name as installed: no change.
    _runtimes, plans2 = await run_prepare([host], pinned_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_files_link_remove_when_present():
    host = SimulatedHost(name="h", links={NGINX_DEFAULT_SITE: DEFAULT_TARGET})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in files.link(ctx, src=NGINX_DEFAULT_SITE, present=False)]
    assert ctx.changed
    assert ctx.reason == "link present, will remove"
    assert commands == [LinkCommand(src=NGINX_DEFAULT_SITE, target=None)]


async def test_files_file_remove_when_present():
    host = SimulatedHost(name="h", existing_files={HTTPD_WELCOME_CONF})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in files.file(ctx, src=HTTPD_WELCOME_CONF, present=False)]
    assert ctx.changed
    assert ctx.reason == "file present, will remove"
    assert commands == [FileCommand(src=HTTPD_WELCOME_CONF, present=False)]


async def test_files_file_no_change_when_already_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in files.file(ctx, src=HTTPD_WELCOME_CONF, present=False)]
    assert not ctx.changed
    assert ctx.reason == "file already absent"  # recorded even when unchanged (10)
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
    assert w1_link.changed and w1_link.reason == "link present, will remove"
    assert w1_link.commands == (LinkCommand(src=NGINX_DEFAULT_SITE, target=None),)

    # web2: nginx already present (no-op), but the default-site link
    # still exists pre-deployment, so its removal is a real change.
    w2_install, w2_link = by_host["web2"]
    assert not w2_install.changed and w2_install.reason == "all packages present"
    assert w2_link.changed and w2_link.reason == "link present, will remove"

    # Final state: nginx installed on both, default site gone on both.
    for host in (web1, web2):
        assert host.packages == {"nginx"}
        assert host.links == {}


async def test_apply_sequential_facts_with_file_side_effects():
    """The httpd scenario: installing the package drops welcome.conf, which
    the next operation's fresh fact cache sees and removes."""

    def httpd_deploy(host):
        yield op(dnf.packages, name="Install httpd", packages=("httpd",))
        yield op(
            files.file,
            name="Remove welcome configuration",
            src=HTTPD_WELCOME_CONF,
            present=False,
        )

    host = SimulatedHost(name="h", installs_files={"httpd": {HTTPD_WELCOME_CONF}})
    runtimes, plans = await run_prepare([host], httpd_deploy)
    # During prepare nothing has run, so the file is not there yet.
    assert not plans["h"][1].changed
    assert plans["h"][1].reason == "file already absent"

    report = await run_apply(runtimes, plans=plans)
    install, remove = report.log
    assert install.changed
    assert remove.changed and remove.reason == "file present, will remove"
    assert remove.commands == (FileCommand(src=HTTPD_WELCOME_CONF, present=False),)
    # Final state: httpd installed, welcome.conf gone.
    assert host.packages == {"httpd"}
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
    assert a.packages == {"nginx"}
    assert b.packages == {"nginx", "vim"}


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
    assert _hosts[0].packages == {"nginx"}
    assert "sudo dnf install -y nginx" in format_log(report)


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
    assert plans["web1"][1].reason == "link already absent"
    # web2: nginx present (no change), link present (would be removed).
    assert [p.changed for p in plans["web2"]] == [False, True]
    assert plans["web2"][1].reason == "link present, will remove"
    # Hosts untouched.
    assert web1.packages == set()
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
