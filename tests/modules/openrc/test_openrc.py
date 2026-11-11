# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the openrc module: ``OpenrcEnabled``, ``OpenrcRunlevels``,
``OpenrcServices``, ``OpenrcStatus`` and ``OpenrcVersion``.

Covers the read-only state discovery in ``reemote.modules.openrc.openrc``:
``OpenrcStatus`` and ``OpenrcEnabled`` are the baseline ports, exercised over the
real-transport path with the baseline's own fact fixtures verbatim (the
``services.json`` and ``services_runlevel.json`` outputs and facts of both
``openrc.OpenrcStatus`` and ``openrc.OpenrcEnabled``, including the crashed
state, the dotted/hyphenated names and both uptime-suffix forms), plus the
``SimulatedHost`` seam projections and their copy semantics.
``OpenrcServices``, ``OpenrcRunlevels`` and ``OpenrcVersion`` -- reemote
originals with no baseline fixtures -- are fed realistic canned ``rc-status``,
``ls -R /etc/runlevels`` and ``openrc --version`` output respectively, plus
the raw-state preservation, the empty/stacked/deep-header runlevel edges, the
None-on-absent version, the seam copy semantics and the ``gather_fact``
fan-outs.

The ``openrc.service`` operation -- a baseline port reading ``OpenrcStatus``
and ``OpenrcEnabled`` -- is exercised over its ``RcServiceCommand`` /
``RcUpdateCommand`` descriptors, all twelve branches of the baseline's own
operation fixtures (start/stop, the running/stopped no-ops, enable/disable
and their no-ops, the boot-runlevel variants and both shell-quoting
injection fixtures), the ``restarted``/``reloaded`` running-only gates, the
``running=None`` leave-alone path, the deliberately dropped ``command``
option, the ``SimulatedHost`` seam dispatch, and end-to-end through ``run`` /
``run_prepare`` + ``run_apply`` (including the ``doas`` elevation Alpine
needs and a dry run).

The ``openrc.runlevel`` and ``openrc.custom_runlevel`` operations -- reemote
originals with no baseline counterpart -- are exercised over their descriptor
rendering and elevation, their fact-backed idempotence in both directions,
the stacking and unstack-before-removal paths, the sorted-target removal
order, the ``ValueError`` validations, the ``SimulatedHost`` seam dispatch,
and end-to-end through ``run_prepare`` + ``run_apply`` (create-then-noop,
create-stack-then-noop, a stacked removal, and a dry run).

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import shlex

import pytest

from reemote.core import FactCache, OpContext, op
from reemote.drivers import (
    command_shell,
    format_log,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import openrc
from reemote.modules.openrc.openrc import (
    OpenrcConfigFile,
    OpenrcConfigInstallCommand,
    OpenrcConfigUploadCommand,
    OpenrcEnabled,
    OpenrcRunlevels,
    OpenrcServices,
    OpenrcStatus,
    OpenrcVersion,
    RcServiceCommand,
    RcUpdateCommand,
    RunlevelCommand,
    _config_spool_path,
    _set_config_value,
    _unset_config_value,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


# --- OpenrcStatus / OpenrcServices: the baseline's services.json fixture -------------

# The ``output`` field of the baseline's ``openrc.OpenrcStatus/services.json``
# fixture verbatim: the runlevel header, five plain state rows (one crashed)
# and two started rows carrying the uptime suffix forms (with and without
# the day count).
RC_STATUS_OUTPUT = """\
Runlevel: default
  net-online                                    [  started  ]
  sshd                                    [  started  ]
  virtualbox-guest-additions              [  crashed  ]
  crond                                    [  started  ]
  nftables.default                         [  started  ]
  podman                                   [  started 00:34:14 (10) ]
  podman-long-running                      [  started 15 day(s) 00:34:14 (10) ]
"""

# The fixture's ``fact`` field -- only ``started`` reads True, so the crashed
# service reads False exactly as the baseline's ``group(2) == "started"`` does.
STATUS_FACT = {
    "net-online": True,
    "sshd": True,
    "virtualbox-guest-additions": False,
    "crond": True,
    "nftables.default": True,
    "podman": True,
    "podman-long-running": True,
}

# The same rows through ``OpenrcServices``: the state word kept raw, so the
# crashed service stays distinguishable from a cleanly stopped one.
SERVICES_FACT = {
    "net-online": "started",
    "sshd": "started",
    "virtualbox-guest-additions": "crashed",
    "crond": "started",
    "nftables.default": "started",
    "podman": "started",
    "podman-long-running": "started",
}

# The ``services_runlevel.json`` variant differs only in the header line.
RC_STATUS_BOOT_OUTPUT = RC_STATUS_OUTPUT.replace(
    "Runlevel: default", "Runlevel: boot", 1
)


def test_openrc_status_command_matches_baseline():
    """The shell is the baseline's fixture command verbatim for both fixture
    runlevels (the runlevel is shlex-quoted, a no-op for these names)."""
    assert OpenrcStatus.command() == "rc-status default"
    assert OpenrcStatus.command("boot") == "rc-status boot"


async def test_openrc_status_parses_the_services_fixture():
    """Over the real-transport path, ``OpenrcStatus`` parses the fixture's
    rows into the fixture fact's answer: the crashed service reads False,
    both uptime-suffix forms are matched and ignored, and the header line
    matches no row."""
    host = CannedShellHost(RC_STATUS_OUTPUT)
    assert await OpenrcStatus.process(host) == STATUS_FACT
    assert host.last_shell == "rc-status default"


async def test_openrc_status_parses_the_boot_runlevel_fixture():
    """The ``services_runlevel.json`` variant: the same rows gathered with
    ``rc-status boot`` produce the same fact (the header never contributes)."""
    host = CannedShellHost(RC_STATUS_BOOT_OUTPUT)
    assert await OpenrcStatus.process(host, "boot") == STATUS_FACT
    assert host.last_shell == "rc-status boot"


async def test_openrc_status_quotes_the_runlevel():
    """A runlevel carrying shell metacharacters is passed literally -- the
    deliberate deviation from the baseline's bare interpolation."""
    host = CannedShellHost("")
    assert await OpenrcStatus.process(host, "my level; reboot") == {}
    assert host.last_shell == "rc-status 'my level; reboot'"


async def test_openrc_status_empty_output_reads_as_empty_dict():
    """A runlevel with no services (or a host whose rc-status prints only a
    header) reads as ``{}`` -- the parser is total."""
    assert await OpenrcStatus.process(CannedShellHost("")) == {}
    assert await OpenrcStatus.process(CannedShellHost("Runlevel: default\n")) == {}


def test_openrc_services_command_matches_openrc_status():
    """The raw-state sibling issues the same per-runlevel shell."""
    assert OpenrcServices.command() == "rc-status default"
    assert OpenrcServices.command("boot") == "rc-status boot"


async def test_openrc_services_keeps_the_raw_state_words():
    """Over the real-transport path, ``OpenrcServices`` parses the same
    fixture rows but keeps the bracketed state word verbatim -- ``crashed``
    survives where ``OpenrcStatus`` collapses it to False."""
    host = CannedShellHost(RC_STATUS_OUTPUT)
    assert await OpenrcServices.process(host) == SERVICES_FACT
    assert host.last_shell == "rc-status default"


async def test_openrc_services_preserves_stopped_and_other_states():
    """Every state word is kept as printed, not just started/crashed: the
    fact is the lossless view the baseline's bool projection cannot offer."""
    stdout = """\
Runlevel: default
  local                                    [  stopped  ]
  nginx                                    [  starting  ]
  postgresql                               [  inactive  ]
"""
    assert OpenrcServices.parse(stdout.splitlines()) == {
        "local": "stopped",
        "nginx": "starting",
        "postgresql": "inactive",
    }


# --- OpenrcEnabled: the baseline's services.json fixture ---------------------------

# The ``output`` field of the baseline's ``openrc.OpenrcEnabled/services.json``
# fixture verbatim: six ``name | runlevels`` rows including the unassigned
# ``cgroups |`` row whose right-hand side is empty.
RC_UPDATE_OUTPUT = """\
             bootmisc | boot
              cgroups |
                crond |      default
     nftables.default | boot
            hwdrivers |                                 sysinit
 virtualbox-guest-additions |      default
"""

# The fixture's ``fact`` field for the default runlevel.
ENABLED_DEFAULT_FACT = {
    "bootmisc": False,
    "cgroups": False,
    "crond": True,
    "nftables.default": False,
    "hwdrivers": False,
    "virtualbox-guest-additions": True,
}

# The ``services_runlevel.json`` fact for boot: membership flips with the
# requested runlevel while the shell stays the one ``rc-update show -v``.
ENABLED_BOOT_FACT = {
    "bootmisc": True,
    "cgroups": False,
    "crond": False,
    "nftables.default": True,
    "hwdrivers": False,
    "virtualbox-guest-additions": False,
}


def test_openrc_enabled_command_matches_baseline():
    """The command is the baseline's fixture command verbatim -- one shell serves
    every runlevel, the argument selects only the membership test."""
    assert OpenrcEnabled.COMMAND == "rc-update show -v"


async def test_openrc_enabled_parses_the_services_fixture():
    """Over the real-transport path, ``OpenrcEnabled`` parses the fixture's
    rows into the fixture fact's default-runlevel answer, the unassigned
    ``cgroups`` row reading False."""
    host = CannedShellHost(RC_UPDATE_OUTPUT)
    assert await OpenrcEnabled.process(host) == ENABLED_DEFAULT_FACT
    assert host.last_shell == OpenrcEnabled.COMMAND


async def test_openrc_enabled_parses_the_boot_runlevel_fixture():
    """The ``services_runlevel.json`` variant: the same output with
    ``runlevel="boot"`` flips the memberships, and the shell is unchanged."""
    host = CannedShellHost(RC_UPDATE_OUTPUT)
    assert await OpenrcEnabled.process(host, "boot") == ENABLED_BOOT_FACT
    assert host.last_shell == OpenrcEnabled.COMMAND


def test_openrc_enabled_skips_lines_without_a_pipe():
    """The deliberate deviation from the baseline: a stray header or blank line
    (no ``|``) is skipped instead of crashing the split, while well-formed
    neighbors still parse."""
    output = ["Runlevels:", "", "  crond | default", "noise without a pipe"]
    assert OpenrcEnabled.parse(output) == {"crond": True}


# --- OpenrcRunlevels: original fact, canned ls -R output -----------------------

# A realistic ``ls -R /etc/runlevels`` listing: the top-level header names
# the runlevel directories, then one section per runlevel lists its assigned
# services (ls sorts); ``sysinit`` is empty, so its section has no entries.
RUNLEVELS_OUTPUT = """\
/etc/runlevels:
boot
default
shutdown
sysinit

/etc/runlevels/boot:
bootmisc
networking
sysctl

/etc/runlevels/default:
crond
sshd

/etc/runlevels/shutdown:
killprocs
savecache

/etc/runlevels/sysinit:
"""

RUNLEVELS_FACT = {
    "boot": ["bootmisc", "networking", "sysctl"],
    "default": ["crond", "sshd"],
    "shutdown": ["killprocs", "savecache"],
    "sysinit": [],
}


def test_openrc_runlevels_command_lists_the_tree_tolerantly():
    """The command is the guarded recursive listing: a host without OpenRC
    (no ``/etc/runlevels``) reads as ``{}`` instead of failing the gather."""
    assert OpenrcRunlevels.PATH == "/etc/runlevels"
    assert OpenrcRunlevels.COMMAND == "ls -R /etc/runlevels 2>/dev/null || true"


async def test_openrc_runlevels_parses_the_canned_listing():
    """Over the real-transport path, the top-level header seeds every
    runlevel (the empty ``sysinit`` included) and each section appends its
    entries in listing order."""
    host = CannedShellHost(RUNLEVELS_OUTPUT)
    assert await OpenrcRunlevels.process(host) == RUNLEVELS_FACT
    assert host.last_shell == OpenrcRunlevels.COMMAND


async def test_openrc_runlevels_reports_a_stacked_runlevel_as_an_entry():
    """A stacked runlevel is a symlink inside the target's directory, so it
    appears in the target's list while keeping its own (empty) section --
    ``ls -R`` does not follow symlinks."""
    stdout = """\
/etc/runlevels:
async
boot
default

/etc/runlevels/async:

/etc/runlevels/boot:
bootmisc

/etc/runlevels/default:
async
sshd
"""
    assert OpenrcRunlevels.parse(stdout.splitlines()) == {
        "async": [],
        "boot": ["bootmisc"],
        "default": ["async", "sshd"],
    }


async def test_openrc_runlevels_empty_output_reads_as_empty_dict():
    """Empty stdout -- what the ``|| true`` guard leaves behind on a host
    without OpenRC -- reads as ``{}``."""
    assert await OpenrcRunlevels.process(CannedShellHost("")) == {}


def test_openrc_runlevels_top_level_only_seeds_empty_lists():
    """A listing with only the top-level header (every runlevel empty)
    still reports each runlevel, seeded as an empty list."""
    output = ["/etc/runlevels:", "boot", "default"]
    assert OpenrcRunlevels.parse(output) == {"boot": [], "default": []}


def test_openrc_runlevels_ignores_deeper_headers():
    """A header deeper than one level (a stray subdirectory) opens no
    runlevel section -- its path is not a runlevel name."""
    output = [
        "/etc/runlevels:",
        "boot",
        "",
        "/etc/runlevels/boot:",
        "bootmisc",
        "",
        "/etc/runlevels/boot/stray:",
    ]
    assert OpenrcRunlevels.parse(output) == {"boot": ["bootmisc"]}


# --- OpenrcVersion: original fact, canned banner -------------------------------


def test_openrc_version_command_is_guarded():
    """The version banner sits behind ``2>/dev/null || true`` so a host
    without OpenRC reads as None instead of failing the gather."""
    assert OpenrcVersion.COMMAND == "openrc --version 2>/dev/null || true"


async def test_openrc_version_parses_the_banner():
    """Over the real-transport path, the banner's last token is the release
    -- ``openrc (OpenRC) 0.54.1`` reads as ``"0.54.1"``."""
    host = CannedShellHost("openrc (OpenRC) 0.54.1\n")
    assert await OpenrcVersion.process(host) == "0.54.1"
    assert host.last_shell == OpenrcVersion.COMMAND


async def test_openrc_version_absent_reads_as_none():
    """No output at all (the guard swallowed the 127) reads as None, and so
    does whitespace-only output."""
    assert await OpenrcVersion.process(CannedShellHost("")) is None
    assert await OpenrcVersion.process(CannedShellHost("\n  \n")) is None


# --- Facts: SimulatedHost seams ------------------------------------------------


async def test_openrc_status_seam_projects_running_bools():
    """The ``openrc_service_states`` store holds raw state words; the fact
    projects them to running bools, so a crashed entry reads False."""
    host = SimulatedHost(
        name="h",
        openrc_service_states={"default": {"sshd": "started", "nginx": "crashed"}},
    )
    assert await OpenrcStatus.process(host) == {"sshd": True, "nginx": False}


async def test_openrc_status_seam_unknown_runlevel_reads_empty():
    """A runlevel the store does not list reads as ``{}``, and so does the
    default empty seam."""
    host = SimulatedHost(name="h", openrc_service_states={"boot": {"x": "started"}})
    assert await OpenrcStatus.process(host, "default") == {}
    assert await OpenrcStatus.process(SimulatedHost(name="h")) == {}


async def test_openrc_services_seam_returns_copies():
    """The raw-state fact reads the same store losslessly; mutating the
    returned dict never touches the simulated state."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "crashed"}}
    )
    states = await OpenrcServices.process(host)
    assert states == {"nginx": "crashed"}
    states["nginx"] = "started"
    states["stray"] = "stopped"
    assert host.openrc_service_states == {"default": {"nginx": "crashed"}}


async def test_openrc_runlevels_seam_returns_copies():
    """The runlevel fact copies each list at the seam, so callers never
    mutate simulated state."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": ["sshd"]})
    runlevels = await OpenrcRunlevels.process(host)
    assert runlevels == {"default": ["sshd"]}
    runlevels["default"].append("stray")
    runlevels["boot"] = []
    assert host.openrc_runlevels == {"default": ["sshd"]}


async def test_openrc_enabled_seam_projects_membership():
    """``OpenrcEnabled`` projects membership from the runlevel -> names
    mapping: a service listed in several runlevels is True only for the
    requested one, and an unassigned service is absent (consumers read
    ``.get(service, False)``)."""
    host = SimulatedHost(
        name="h", openrc_runlevels={"boot": ["bootmisc"], "default": ["sshd"]}
    )
    assert await OpenrcEnabled.process(host) == {"bootmisc": False, "sshd": True}
    assert await OpenrcEnabled.process(host, "boot") == {
        "bootmisc": True,
        "sshd": False,
    }
    assert "cgroups" not in await OpenrcEnabled.process(host)


async def test_openrc_version_seam_projects_empty_to_none():
    """The ``openrc_version`` string seam projects its empty default to
    None (None itself is the seam-absent sentinel)."""
    assert await OpenrcVersion.process(SimulatedHost(name="h")) is None
    host = SimulatedHost(name="h", openrc_version="0.54.1")
    assert await OpenrcVersion.process(host) == "0.54.1"


# --- Facts: gather_fact fan-out -------------------------------------------------


async def test_gather_fact_openrc_status_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name; the runlevel argument fans out to every host."""
    host1 = SimulatedHost(
        name="web1", openrc_service_states={"boot": {"net": "started"}}
    )
    host2 = SimulatedHost(name="web2")
    statuses = await gather_fact([host1, host2], OpenrcStatus, "boot")
    assert statuses == {"web1": {"net": True}, "web2": {}}


async def test_gather_fact_openrc_enabled_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(RC_UPDATE_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    enabled = await gather_fact([host1, host2], OpenrcEnabled)
    assert enabled == {"web1": ENABLED_DEFAULT_FACT, "web2": {}}
    assert host1.last_shell == OpenrcEnabled.COMMAND
    assert host2.last_shell == OpenrcEnabled.COMMAND


async def test_gather_fact_openrc_runlevels_reads_simulated_seams():
    host1 = SimulatedHost(name="web1", openrc_runlevels=RUNLEVELS_FACT)
    host2 = SimulatedHost(name="web2")
    runlevels = await gather_fact([host1, host2], OpenrcRunlevels)
    assert runlevels == {"web1": RUNLEVELS_FACT, "web2": {}}


async def test_gather_fact_openrc_services_reads_simulated_seams():
    host1 = SimulatedHost(
        name="web1", openrc_service_states={"default": {"nginx": "crashed"}}
    )
    host2 = SimulatedHost(name="web2")
    services = await gather_fact([host1, host2], OpenrcServices)
    assert services == {"web1": {"nginx": "crashed"}, "web2": {}}


async def test_gather_fact_openrc_version_reads_simulated_seams():
    host1 = SimulatedHost(name="web1", openrc_version="0.54.1")
    host2 = SimulatedHost(name="web2")
    versions = await gather_fact([host1, host2], OpenrcVersion)
    assert versions == {"web1": "0.54.1", "web2": None}# --- Command descriptors -------------------------------------------------------


def test_rc_service_command_renders_the_action():
    """The runtime descriptor is the baseline's ``rc-service {0} {1}`` formatter:
    the quoted service name then the verb."""
    assert RcServiceCommand(service="nginx", action="start").shell() == (
        "rc-service nginx start"
    )
    assert RcServiceCommand(service="nginx", action="stop").shell() == (
        "rc-service nginx stop"
    )


def test_rc_service_command_shell_quotes_the_name():
    """The baseline's ``start_needs_quotes`` fixture: a name carrying spaces and
    a semicolon reaches rc-service literally, never the remote shell."""
    cmd = RcServiceCommand(service="my service; whoami", action="start")
    assert cmd.shell() == "rc-service 'my service; whoami' start"


def test_rc_service_command_is_elevated_by_doas():
    """rc-service needs root; stock Alpine ships no sudo, so ``doas=True``
    is the usual escalator -- the passwordless ``doas -n`` prefix wraps the
    whole rendered shell."""
    cmd = RcServiceCommand(service="nginx", action="start")
    assert command_shell(cmd) == "rc-service nginx start"
    assert command_shell(cmd, doas=True) == "doas -n rc-service nginx start"
    assert command_shell(cmd, sudo=True) == "sudo rc-service nginx start"


def test_rc_update_command_renders_add_and_del():
    """One present-flagged descriptor covers both assignment directions
    (the ``ApkHoldCommand`` precedent): add for present, del for absent."""
    assert RcUpdateCommand(service="nginx").shell() == "rc-update add nginx default"
    assert RcUpdateCommand(service="nginx", present=False).shell() == (
        "rc-update del nginx default"
    )
    assert RcUpdateCommand(service="nftables", runlevel="boot").shell() == (
        "rc-update add nftables boot"
    )


def test_rc_update_command_renders_the_stack_flag():
    """``stack=True`` inserts ``-s`` before the verb in both directions:
    the name is then a runlevel being stacked onto (or unstacked from) the
    target runlevel."""
    assert RcUpdateCommand(service="async", runlevel="default", stack=True).shell() == (
        "rc-update -s add async default"
    )
    assert RcUpdateCommand(
        service="async", runlevel="default", present=False, stack=True
    ).shell() == "rc-update -s del async default"


def test_rc_update_command_shell_quotes_both_names():
    """The baseline's ``enable_quotes_injection`` fixture: both arguments are
    shlex-quoted, so a metacharacter in either never executes."""
    cmd = RcUpdateCommand(service="x; reboot")
    assert cmd.shell() == "rc-update add 'x; reboot' default"
    cmd = RcUpdateCommand(service="nginx", runlevel="my level")
    assert cmd.shell() == "rc-update add nginx 'my level'"


def test_runlevel_command_renders_mkdir_and_rm():
    """The runlevel-directory descriptor: a runlevel *is* a directory, so
    creation is ``mkdir -p`` and removal the bare ``rm -rf`` (rc-update(8)
    ships no verb for either)."""
    assert RunlevelCommand(runlevel="async").shell() == "mkdir -p /etc/runlevels/async"
    assert RunlevelCommand(runlevel="async", present=False).shell() == (
        "rm -rf /etc/runlevels/async"
    )


def test_runlevel_command_shell_quotes_the_name():
    """The runlevel name is quoted inside the path, so a metacharacter
    cannot escape the directory argument."""
    cmd = RunlevelCommand(runlevel="my level; reboot")
    assert cmd.shell() == "mkdir -p /etc/runlevels/'my level; reboot'"


def test_runlevel_command_is_elevated_by_doas():
    """Writing under /etc/runlevels needs root; the escalation prefixes the
    whole rendered shell."""
    cmd = RunlevelCommand(runlevel="async", present=False)
    assert command_shell(cmd, doas=True) == "doas -n rm -rf /etc/runlevels/async"
    assert command_shell(cmd, su=True) == (
        f"su -c {shlex.quote('rm -rf /etc/runlevels/async')}"
    )


# --- openrc.service: the baseline's operation fixtures -------------------------------


async def test_openrc_service_starts_a_stopped_service():
    """The baseline's ``start.json``: the service reads not-running, so one
    ``rc-service nginx start`` is yielded."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "stopped"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx")]
    assert ctx.changed
    assert ctx.reason == "service nginx is not running, will start"
    assert commands == [RcServiceCommand(service="nginx", action="start")]
    assert commands[0].shell() == "rc-service nginx start"


async def test_openrc_service_stops_a_running_service():
    """The baseline's ``stop.json``: ``running=False`` against a started service
    yields ``rc-service nginx stop``."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "started"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx", running=False)]
    assert ctx.changed
    assert ctx.reason == "service nginx is running, will stop"
    assert commands == [RcServiceCommand(service="nginx", action="stop")]
    assert commands[0].shell() == "rc-service nginx stop"


async def test_openrc_service_running_noop():
    """The baseline's ``running_noop.json``: an already-running service is left
    alone, with the baseline's verbatim no-op reason."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "started"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx")]
    assert not ctx.changed
    assert ctx.reason == "service nginx is running"
    assert commands == []


async def test_openrc_service_stopped_noop():
    """The baseline's ``stopped_noop.json``: ``running=False`` against a stopped
    service is a no-op."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "stopped"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx", running=False)]
    assert not ctx.changed
    assert ctx.reason == "service nginx is stopped"
    assert commands == []


async def test_openrc_service_enables_an_unassigned_service():
    """The baseline's ``enable.json``: ``enabled=True`` against a service the
    runlevel does not list yields ``rc-update add nginx default`` (the
    runtime direction is left alone with ``running=None``)."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(ctx, service="nginx", running=None, enabled=True)
    ]
    assert ctx.changed
    assert ctx.reason == "service nginx is not enabled, will enable"
    assert commands == [RcUpdateCommand(service="nginx", present=True)]
    assert commands[0].shell() == "rc-update add nginx default"


async def test_openrc_service_disables_an_assigned_service():
    """The baseline's ``disable.json``: ``enabled=False`` against an assigned
    service yields ``rc-update del nginx default``."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": ["nginx"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(ctx, service="nginx", running=None, enabled=False)
    ]
    assert ctx.changed
    assert ctx.reason == "service nginx is enabled, will disable"
    assert commands == [RcUpdateCommand(service="nginx", present=False)]
    assert commands[0].shell() == "rc-update del nginx default"


async def test_openrc_service_enable_noop():
    """The baseline's ``enable_noop.json``: an already-enabled service reads the
    verbatim no-op reason."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": ["nginx"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(ctx, service="nginx", running=None, enabled=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "service nginx is enabled"
    assert commands == []


async def test_openrc_service_disable_noop():
    """The baseline's ``disable_noop.json``: an unassigned service with
    ``enabled=False`` is already in state."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(ctx, service="nginx", running=None, enabled=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "service nginx is disabled"
    assert commands == []


async def test_openrc_service_enable_in_the_boot_runlevel():
    """The baseline's ``enable_runlevel.json``: the boot assignment renders
    ``rc-update add nftables boot``."""
    host = SimulatedHost(name="h", openrc_runlevels={"boot": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(
            ctx, service="nftables", running=None, enabled=True, runlevel="boot"
        )
    ]
    assert ctx.changed
    assert commands == [RcUpdateCommand(service="nftables", runlevel="boot")]
    assert commands[0].shell() == "rc-update add nftables boot"


async def test_openrc_service_disable_in_the_boot_runlevel():
    """The baseline's ``disable_runlevel.json``: the boot unassignment renders
    ``rc-update del nftables boot``."""
    host = SimulatedHost(name="h", openrc_runlevels={"boot": ["nftables"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(
            ctx, service="nftables", running=None, enabled=False, runlevel="boot"
        )
    ]
    assert ctx.changed
    assert commands == [
        RcUpdateCommand(service="nftables", runlevel="boot", present=False)
    ]
    assert commands[0].shell() == "rc-update del nftables boot"


async def test_openrc_service_enable_quotes_injection():
    """The baseline's ``enable_quotes_injection.json``: the service name reaches
    rc-update quoted, never executed."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(
            ctx, service="x; reboot", running=None, enabled=True
        )
    ]
    assert commands[0].shell() == "rc-update add 'x; reboot' default"


async def test_openrc_service_start_needs_quotes():
    """The baseline's ``start_needs_quotes.json``: a name with spaces and a
    semicolon reaches rc-service as one literal argument."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="my service; whoami")]
    assert commands[0].shell() == "rc-service 'my service; whoami' start"


# --- openrc.service: restart/reload gates and combined directions ----------------


async def test_openrc_service_restarts_a_running_service():
    """``restarted=True`` acts on an already-running service: the runtime
    no-op reason and the restart reason join with ``; ``."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "started"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx", restarted=True)]
    assert ctx.changed
    assert ctx.reason == "service nginx is running; service nginx will restart"
    assert commands == [RcServiceCommand(service="nginx", action="restart")]


async def test_openrc_service_reloads_a_running_service():
    """``reloaded=True`` -- the option kept from the baseline -- yields the
    reload verb only for a running service."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "started"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx", reloaded=True)]
    assert ctx.changed
    assert ctx.reason == "service nginx is running; service nginx will reload"
    assert commands == [RcServiceCommand(service="nginx", action="reload")]


async def test_openrc_service_restart_skips_a_stopped_service():
    """The baseline's ``restarted and is_running`` gate: a stopped service is
    never restarted -- with ``running=None`` nothing at all is yielded."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "stopped"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.service(
            ctx, service="nginx", running=None, restarted=True, reloaded=True
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "service nginx left alone"
    assert commands == []


async def test_openrc_service_starts_a_crashed_service():
    """Only ``started`` counts as running, so a crashed service is started
    -- the ``OpenrcStatus`` projection the baseline's own fact performs."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "crashed"}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx")]
    assert ctx.changed
    assert commands == [RcServiceCommand(service="nginx", action="start")]


async def test_openrc_service_running_none_leaves_the_runtime_alone():
    """``running=None`` with no other option is the full leave-alone path:
    no fact-driven runtime command and the fallback reason."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx", running=None)]
    assert not ctx.changed
    assert ctx.reason == "service nginx left alone"
    assert commands == []


async def test_openrc_service_combines_start_and_enable():
    """Both directions in one request: the start and the rc-update add are
    yielded in order and their reasons join with ``; ``."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.service(ctx, service="nginx", enabled=True)]
    assert ctx.changed
    assert ctx.reason == (
        "service nginx is not running, will start; "
        "service nginx is not enabled, will enable"
    )
    assert commands == [
        RcServiceCommand(service="nginx", action="start"),
        RcUpdateCommand(service="nginx", present=True),
    ]


async def test_openrc_service_absorbs_the_dropped_command_option():
    """The baseline's arbitrary ``command`` option is deliberately dropped;
    ``**kwargs`` absorbs it (and the baseline's global arguments) without
    effect."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in openrc.service(ctx, service="nginx", command="zap")
    ]
    assert commands == [RcServiceCommand(service="nginx", action="start")]


# --- openrc.runlevel: idempotence ------------------------------------------------


async def test_openrc_runlevel_adds_an_unassigned_service():
    """The standalone membership verb: an absent assignment yields one
    ``rc-update add`` without touching the runtime state."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.runlevel(ctx, service="cron")]
    assert ctx.changed
    assert ctx.reason == "service cron is not enabled in default, will add"
    assert commands == [RcUpdateCommand(service="cron", present=True)]


async def test_openrc_runlevel_removes_an_assigned_service():
    """``enabled=False`` against a listed service yields the ``del``."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": ["cron"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.runlevel(ctx, service="cron", enabled=False)]
    assert ctx.changed
    assert ctx.reason == "service cron is enabled in default, will remove"
    assert commands == [RcUpdateCommand(service="cron", present=False)]


async def test_openrc_runlevel_add_noop():
    """An already-assigned service is left alone, with the reason recorded
    even when unchanged (the plan shows it)."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": ["cron"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.runlevel(ctx, service="cron")]
    assert not ctx.changed
    assert ctx.reason == "service cron is enabled in default"
    assert commands == []


async def test_openrc_runlevel_remove_noop():
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.runlevel(ctx, service="cron", enabled=False)]
    assert not ctx.changed
    assert ctx.reason == "service cron is not enabled in default"
    assert commands == []


async def test_openrc_runlevel_targets_the_boot_runlevel():
    """The runlevel argument selects both the membership test and the
    rc-update target."""
    host = SimulatedHost(name="h", openrc_runlevels={"boot": [], "default": ["cron"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.runlevel(ctx, service="cron", runlevel="boot")]
    assert ctx.changed
    assert ctx.reason == "service cron is not enabled in boot, will add"
    assert commands == [RcUpdateCommand(service="cron", runlevel="boot")]


async def test_openrc_runlevel_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in openrc.runlevel(ctx, service="cron", future_flag=True)
    ]
    assert commands == [RcUpdateCommand(service="cron", present=True)]


# --- openrc.custom_runlevel: creation and stacking -------------------------------


async def test_openrc_custom_runlevel_creates_an_absent_runlevel():
    """An absent runlevel yields one ``mkdir -p`` -- rc-update(8) ships no
    creation verb, a runlevel is just a directory."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.custom_runlevel(ctx, runlevel="async")]
    assert ctx.changed
    assert ctx.reason == "runlevel async does not exist, will create"
    assert commands == [RunlevelCommand(runlevel="async", present=True)]


async def test_openrc_custom_runlevel_creates_and_stacks():
    """Creation plus stacking yields the directory command then the
    ``rc-update -s add``, reasons joined in yield order."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.custom_runlevel(
            ctx, runlevel="async", stacked_to="default"
        )
    ]
    assert ctx.changed
    assert ctx.reason == (
        "runlevel async does not exist, will create; "
        "runlevel async is not stacked onto default, will stack"
    )
    assert commands == [
        RunlevelCommand(runlevel="async", present=True),
        RcUpdateCommand(service="async", runlevel="default", stack=True),
    ]


async def test_openrc_custom_runlevel_existing_is_a_noop():
    host = SimulatedHost(name="h", openrc_runlevels={"async": [], "default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.custom_runlevel(ctx, runlevel="async")]
    assert not ctx.changed
    assert ctx.reason == "runlevel async exists"
    assert commands == []


async def test_openrc_custom_runlevel_stacks_an_existing_runlevel():
    """An existing but unstacked runlevel yields only the stack command."""
    host = SimulatedHost(name="h", openrc_runlevels={"async": [], "default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.custom_runlevel(
            ctx, runlevel="async", stacked_to="default"
        )
    ]
    assert ctx.changed
    assert ctx.reason == (
        "runlevel async exists; runlevel async is not stacked onto default, will stack"
    )
    assert commands == [
        RcUpdateCommand(service="async", runlevel="default", stack=True)
    ]


async def test_openrc_custom_runlevel_already_stacked_is_a_noop():
    """The stacked runlevel appears as an entry of the target's list (the
    symlink ``ls -R`` reports), so both directions read in-state."""
    host = SimulatedHost(
        name="h", openrc_runlevels={"async": [], "default": ["async", "sshd"]}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.custom_runlevel(
            ctx, runlevel="async", stacked_to="default"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "runlevel async exists; runlevel async is stacked onto default"
    assert commands == []


# --- openrc.custom_runlevel: removal with unstacking -----------------------------


async def test_openrc_custom_runlevel_removes_with_unstacking():
    """Removal unstacks from every target the facts report (sorted target
    order keeps the request sequence stable) then deletes the directory --
    no dangling stack symlink survives."""
    host = SimulatedHost(
        name="h",
        openrc_runlevels={
            "default": ["async", "sshd"],
            "async": [],
            "boot": ["async"],
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in openrc.custom_runlevel(ctx, runlevel="async", present=False)
    ]
    assert ctx.changed
    assert ctx.reason == (
        "runlevel async will be unstacked from boot; "
        "runlevel async will be unstacked from default; "
        "runlevel async exists, will remove"
    )
    assert commands == [
        RcUpdateCommand(service="async", runlevel="boot", present=False, stack=True),
        RcUpdateCommand(service="async", runlevel="default", present=False, stack=True),
        RunlevelCommand(runlevel="async", present=False),
    ]


async def test_openrc_custom_runlevel_removes_an_unstacked_runlevel():
    """With no target listing the runlevel, removal is the bare directory
    delete; the runlevel's own entries never count as stack targets."""
    host = SimulatedHost(
        name="h", openrc_runlevels={"async": ["local"], "default": []}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in openrc.custom_runlevel(ctx, runlevel="async", present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "runlevel async exists, will remove"
    assert commands == [RunlevelCommand(runlevel="async", present=False)]


async def test_openrc_custom_runlevel_remove_absent_is_a_noop():
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in openrc.custom_runlevel(ctx, runlevel="async", present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "runlevel async does not exist"
    assert commands == []


async def test_openrc_custom_runlevel_rejects_an_empty_name():
    """Validation raises while materializing -- in a deploy run that is the
    prepare phase, before anything is sent."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="runlevel name must not be empty"):
        [c async for c in openrc.custom_runlevel(ctx, runlevel="")]


async def test_openrc_custom_runlevel_rejects_self_stacking():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="cannot be stacked onto itself"):
        [
            c
            async for c in openrc.custom_runlevel(
                ctx, runlevel="async", stacked_to="async"
            )
        ]


async def test_openrc_custom_runlevel_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.custom_runlevel(
            ctx, runlevel="async", future_flag=True
        )
    ]
    assert commands == [RunlevelCommand(runlevel="async", present=True)]# --- SimulatedHost seam dispatch -------------------------------------------------


async def test_rc_service_seam_updates_every_runlevel_listing():
    """Runtime state is global: the state word changes in every runlevel
    listing the service appears in, so both per-runlevel facts agree."""
    host = SimulatedHost(
        name="h",
        openrc_service_states={
            "boot": {"net": "stopped"},
            "default": {"net": "stopped"},
        },
    )
    await send_command(host, RcServiceCommand(service="net", action="start"))
    assert host.openrc_service_states == {
        "boot": {"net": "started"},
        "default": {"net": "started"},
    }
    await send_command(host, RcServiceCommand(service="net", action="stop"))
    assert host.openrc_service_states == {
        "boot": {"net": "stopped"},
        "default": {"net": "stopped"},
    }


async def test_rc_service_seam_inserts_an_unassigned_service_into_default():
    """Starting a service no runlevel lists yet inserts it into the default
    listing, so the change stays observable through the default-runlevel
    facts (the documented simulation simplification)."""
    host = SimulatedHost(name="h")
    await send_command(host, RcServiceCommand(service="nginx", action="start"))
    assert host.openrc_service_states == {"default": {"nginx": "started"}}


async def test_rc_service_seam_reload_leaves_the_state_word():
    """A reload re-reads configuration without a state change."""
    host = SimulatedHost(
        name="h", openrc_service_states={"default": {"nginx": "started"}}
    )
    await send_command(host, RcServiceCommand(service="nginx", action="reload"))
    assert host.openrc_service_states == {"default": {"nginx": "started"}}


async def test_rc_update_seam_add_assigns_and_seeds_stopped():
    """``rc-update add`` appends the assignment and seeds a ``stopped``
    state word -- enabling at boot never starts a service."""
    host = SimulatedHost(name="h")
    await send_command(host, RcUpdateCommand(service="nginx"))
    assert host.openrc_runlevels == {"default": ["nginx"]}
    assert host.openrc_service_states == {"default": {"nginx": "stopped"}}


async def test_rc_update_seam_del_unassigns_only_the_one_runlevel():
    """``rc-update del`` drops the assignment and the runlevel's state
    listing entry; other runlevels' listings are untouched."""
    host = SimulatedHost(
        name="h",
        openrc_runlevels={"boot": ["nginx"], "default": ["nginx"]},
        openrc_service_states={
            "boot": {"nginx": "started"},
            "default": {"nginx": "started"},
        },
    )
    await send_command(host, RcUpdateCommand(service="nginx", present=False))
    assert host.openrc_runlevels == {"boot": ["nginx"], "default": []}
    assert host.openrc_service_states == {
        "boot": {"nginx": "started"},
        "default": {},
    }


async def test_rc_update_seam_stack_and_unstack():
    """The ``-s`` arms edit the target's assignment list: stacking appends
    the runlevel name (the symlink), unstacking removes it."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    await send_command(
        host, RcUpdateCommand(service="async", runlevel="default", stack=True)
    )
    assert host.openrc_runlevels == {"default": ["async"]}
    await send_command(
        host,
        RcUpdateCommand(
            service="async", runlevel="default", present=False, stack=True
        ),
    )
    assert host.openrc_runlevels == {"default": []}


async def test_runlevel_seam_create_and_remove():
    """The directory arms seed/pop the runlevel stores; removal also drops
    the name from every other list, so no dangling stack entry survives a
    directly driven descriptor."""
    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    await send_command(host, RunlevelCommand(runlevel="async"))
    assert host.openrc_runlevels == {"default": [], "async": []}
    # Stack it, then drive the removal descriptor directly (no unstack).
    await send_command(
        host, RcUpdateCommand(service="async", runlevel="default", stack=True)
    )
    await send_command(host, RunlevelCommand(runlevel="async", present=False))
    assert host.openrc_runlevels == {"default": []}
    assert "async" not in host.openrc_service_states


# --- openrc.service: end to end ---------------------------------------------------


async def test_openrc_service_end_to_end_start_then_noop_under_doas():
    """Through run_prepare/run_apply: the start executes under doas (stock
    Alpine ships no sudo) and renders in the log; a second prepare sees the
    service started and plans no change (the fresh per-step fact cache
    observes the mutation)."""

    def start_deploy(host):
        yield op(openrc.service, name="Start nginx", service="nginx", doas=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], start_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (RcServiceCommand(service="nginx", action="start"),)
    assert "$ doas -n rc-service nginx start" in format_log(report)
    assert host.openrc_service_states == {"default": {"nginx": "started"}}
    # A second prepare sees the service running: no change.
    _runtimes, plans2 = await run_prepare([host], start_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "service nginx is running"


async def test_openrc_service_end_to_end_enable_then_noop():
    """The boot direction through the driver: the rc-update add lands in
    the assignment seam (seeding a stopped state word), and a second
    prepare reads the service enabled."""

    def enable_deploy(host):
        yield op(
            openrc.service,
            name="Enable nginx at boot",
            service="nginx",
            running=None,
            enabled=True,
            doas=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], enable_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (RcUpdateCommand(service="nginx", present=True),)
    assert "$ doas -n rc-update add nginx default" in format_log(report)
    assert host.openrc_runlevels == {"default": ["nginx"]}
    _runtimes, plans2 = await run_prepare([host], enable_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "service nginx is enabled"


async def test_openrc_service_end_to_end_lockstep_across_hosts():
    """Two hosts, one request each: the apply log pairs the Nth request
    across hosts, and each host's seam mutates independently."""

    def start_deploy(host):
        yield op(openrc.service, name="Start nginx", service="nginx", doas=True)

    host1 = SimulatedHost(name="web1")
    host2 = SimulatedHost(
        name="web2", openrc_service_states={"default": {"nginx": "started"}}
    )
    runtimes, plans = await run_prepare([host1, host2], start_deploy)
    assert plans["web1"][0].changed
    assert not plans["web2"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert len(report.log) == 2
    assert report.log[0].changed
    assert not report.log[1].changed
    assert host1.openrc_service_states == {"default": {"nginx": "started"}}
    assert host2.openrc_service_states == {"default": {"nginx": "started"}}


async def test_openrc_service_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the start is planned as a change
    but there is no apply report, so nothing runs on the host."""

    def start_deploy(host):
        yield op(openrc.service, name="Start nginx", service="nginx", doas=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        start_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (RcServiceCommand(service="nginx", action="start"),)
    # Nothing ran: the dry run sent no command.
    assert host.openrc_service_states == {}


# --- openrc.runlevel: end to end ---------------------------------------------------


async def test_openrc_runlevel_end_to_end_add_then_noop():
    """Through run_prepare/run_apply: the assignment executes under doas
    and a second prepare sees the service enabled in the runlevel."""

    def add_deploy(host):
        yield op(
            openrc.runlevel, name="Assign cron to default", service="cron", doas=True
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], add_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (RcUpdateCommand(service="cron", present=True),)
    assert "$ doas -n rc-update add cron default" in format_log(report)
    assert host.openrc_runlevels == {"default": ["cron"]}
    _runtimes, plans2 = await run_prepare([host], add_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "service cron is enabled in default"


# --- openrc.custom_runlevel: end to end ---------------------------------------------


async def test_openrc_custom_runlevel_end_to_end_create_stack_then_noop():
    """Through run_prepare/run_apply: the directory create and the stack
    execute in order under doas; a second prepare sees the runlevel present
    and stacked (the fresh per-step fact cache observes both mutations)."""

    def create_deploy(host):
        yield op(
            openrc.custom_runlevel,
            name="Create async stacked on default",
            runlevel="async",
            stacked_to="default",
            doas=True,
        )

    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    runtimes, plans = await run_prepare([host], create_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (
        RunlevelCommand(runlevel="async", present=True),
        RcUpdateCommand(service="async", runlevel="default", stack=True),
    )
    log = format_log(report)
    assert "$ doas -n mkdir -p /etc/runlevels/async" in log
    assert "$ doas -n rc-update -s add async default" in log
    assert host.openrc_runlevels == {"default": ["async"], "async": []}
    _runtimes, plans2 = await run_prepare([host], create_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == (
        "runlevel async exists; runlevel async is stacked onto default"
    )


async def test_openrc_custom_runlevel_end_to_end_stacked_removal():
    """Removal through the driver: the unstack commands run before the
    directory delete, and a second prepare sees the runlevel gone."""

    def remove_deploy(host):
        yield op(
            openrc.custom_runlevel,
            name="Remove async",
            runlevel="async",
            present=False,
            doas=True,
        )

    host = SimulatedHost(
        name="h", openrc_runlevels={"async": [], "default": ["async", "sshd"]}
    )
    runtimes, plans = await run_prepare([host], remove_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (
        RcUpdateCommand(service="async", runlevel="default", present=False, stack=True),
        RunlevelCommand(runlevel="async", present=False),
    )
    log = format_log(report)
    assert "$ doas -n rc-update -s del async default" in log
    assert "$ doas -n rm -rf /etc/runlevels/async" in log
    assert host.openrc_runlevels == {"default": ["sshd"]}
    _runtimes, plans2 = await run_prepare([host], remove_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "runlevel async does not exist"


async def test_openrc_custom_runlevel_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the create is planned but nothing
    is sent, so the runlevel store stays untouched."""

    def create_deploy(host):
        yield op(
            openrc.custom_runlevel, name="Create async", runlevel="async", doas=True
        )

    host = SimulatedHost(name="h", openrc_runlevels={"default": []})
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        create_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (RunlevelCommand(runlevel="async", present=True),)
    assert host.openrc_runlevels == {"default": []}


# --- OpenrcConfigFile: the raw-content fact -------------------------------------

# A stock-style rc.conf: a header comment, a commented-out assignment, the
# active assignment the operation will replace, and an unrelated setting.
RC_CONF_TEXT = '# /etc/rc.conf\n#rc_parallel=NO\nrc_parallel="NO"\nrc_logger="YES"\n'


def test_openrc_config_file_command_guards_and_quotes():
    """The ``test -e`` + marker + ``cat`` form, with the path shell-quoted
    (the ``ApkRepositoriesFile.command`` pattern)."""
    assert OpenrcConfigFile.command("/etc/rc.conf") == (
        "test -e /etc/rc.conf && { printf '#reemote-openrcconfig\\n'; "
        "cat -- /etc/rc.conf; }"
    )
    assert OpenrcConfigFile.command("/etc/my rc.conf") == (
        "test -e '/etc/my rc.conf' && { printf '#reemote-openrcconfig\\n'; "
        "cat -- '/etc/my rc.conf'; }"
    )


async def test_openrc_config_file_seam_absent_reads_none():
    """The simulated seam reports an absent file as None, never \"\"."""
    host = SimulatedHost(name="h")
    assert await OpenrcConfigFile.process(host, "/etc/rc.conf") is None


async def test_openrc_config_file_seam_empty_reads_empty_string():
    """An existing but empty file reads \"\" -- the marker distinction the
    unset direction needs to delete an empty leftover."""
    host = SimulatedHost(name="h", remote_files={"/etc/rc.conf": b""})
    assert await OpenrcConfigFile.process(host, "/etc/rc.conf") == ""


async def test_openrc_config_file_seam_decodes_content():
    """The seam decodes the stored bytes verbatim -- comments, blank lines
    and ordering survive, because the fact never parses the text."""
    host = SimulatedHost(name="h", remote_files={"/etc/rc.conf": RC_CONF_TEXT.encode()})
    assert await OpenrcConfigFile.process(host, "/etc/rc.conf") == RC_CONF_TEXT


async def test_openrc_config_file_canned_parses_marker_and_content():
    """Over the real-transport path the marker line is stripped and the
    rest returned verbatim (the seam short-circuit never exercises this)."""
    host = CannedShellHost(f"#reemote-openrcconfig\n{RC_CONF_TEXT}")
    assert await OpenrcConfigFile.process(host, "/etc/rc.conf") == RC_CONF_TEXT
    assert host.last_shell == OpenrcConfigFile.command("/etc/rc.conf")


async def test_openrc_config_file_canned_absent_reads_none():
    """No marker on stdout (the ``test -e`` guard failed) reads as None."""
    assert await OpenrcConfigFile.process(CannedShellHost(""), "/etc/rc.conf") is None


async def test_openrc_config_file_canned_empty_reads_empty_string():
    """The marker alone -- an existing empty file -- reads as \"\"."""
    host = CannedShellHost("#reemote-openrcconfig\n")
    assert await OpenrcConfigFile.process(host, "/etc/rc.conf") == ""


async def test_openrc_config_file_gather_fact_forwards_the_path():
    """``gather_fact`` forwards the path argument to every host's read."""
    host1 = SimulatedHost(name="web1", remote_files={"/etc/rc.conf": b"a=1\n"})
    host2 = SimulatedHost(name="web2")
    contents = await gather_fact([host1, host2], OpenrcConfigFile, "/etc/rc.conf")
    assert contents == {"web1": "a=1\n", "web2": None}


# --- openrc.config: the pure transforms -----------------------------------------


def test_set_config_value_absent_file_becomes_the_single_line():
    """None content gains just the quoted assignment and a trailing newline."""
    assert _set_config_value(None, "rc_parallel", "YES") == 'rc_parallel="YES"\n'
    assert _set_config_value("", "rc_parallel", "YES") == 'rc_parallel="YES"\n'


def test_set_config_value_replaces_active_and_commented_assignments():
    """Every assignment of the key is dropped -- commented ones included,
    the recipe's replacement semantics -- and the wanted line appended;
    unrelated lines keep their order and text."""
    assert _set_config_value(RC_CONF_TEXT, "rc_parallel", "YES") == (
        '# /etc/rc.conf\nrc_logger="YES"\nrc_parallel="YES"\n'
    )


def test_set_config_value_matches_whitespace_variants():
    """Leading whitespace, spaces around the ``=`` and a spaced comment
    marker all match the drop pattern."""
    content = '  rc_parallel = "NO"\n#  rc_parallel=NO\nother="KEEP"\n'
    assert _set_config_value(content, "rc_parallel", "YES") == (
        'other="KEEP"\nrc_parallel="YES"\n'
    )


def test_set_config_value_is_a_fixpoint():
    """The appended line matches the drop pattern, so a second pass
    reproduces the same content -- both phases derive the same bytes."""
    once = _set_config_value(RC_CONF_TEXT, "rc_parallel", "YES")
    assert _set_config_value(once, "rc_parallel", "YES") == once


def test_set_config_value_does_not_match_other_keys():
    """The pattern is key-anchored: ``rc_parallel`` never drops
    ``rc_parallel_extra`` or a line merely mentioning the key."""
    content = 'rc_parallel_extra="KEEP"\n# see rc_parallel docs\n'
    assert _set_config_value(content, "rc_parallel", "YES") == (
        'rc_parallel_extra="KEEP"\n# see rc_parallel docs\nrc_parallel="YES"\n'
    )


def test_unset_config_value_drops_assignments_and_keeps_the_rest():
    """Active and commented assignments alike are dropped; the remaining
    lines keep their order and the file's trailing newline."""
    assert _unset_config_value(RC_CONF_TEXT, "rc_parallel") == (
        '# /etc/rc.conf\nrc_logger="YES"\n'
    )


def test_unset_config_value_absent_and_no_match_are_fixpoints():
    """None reads as empty; a file without the key comes back verbatim."""
    assert _unset_config_value(None, "rc_parallel") == ""
    assert _unset_config_value("", "rc_parallel") == ""
    content = 'rc_logger="YES"\n'
    assert _unset_config_value(content, "rc_parallel") == content


def test_unset_config_value_empties_to_empty_string():
    """A file holding nothing but assignments of the key empties to \"\"
    (a legitimate spool payload), never disappears."""
    assert _unset_config_value('#rc_parallel=NO\nrc_parallel="YES"\n', "rc_parallel") == ""


def test_unset_config_value_keeps_a_missing_trailing_newline():
    """Unlike the set transform (which always ends in one newline), the
    unset preserves the file's own ending -- here, none."""
    assert _unset_config_value('a="1"\nrc_parallel="YES"', "rc_parallel") == 'a="1"'


def test_unset_config_value_is_a_fixpoint():
    once = _unset_config_value(RC_CONF_TEXT, "rc_parallel")
    assert _unset_config_value(once, "rc_parallel") == once


# --- openrc.config: spool derivation and descriptors ----------------------------


def test_config_spool_path_is_dest_derived_and_deterministic():
    """The same dest maps to the same spool in both phases, different dests
    never share one, and the name keeps the family prefix."""
    spool = _config_spool_path("/etc/rc.conf")
    assert spool == _config_spool_path("/etc/rc.conf")
    assert spool != _config_spool_path("/etc/conf.d/sshd")
    assert spool.startswith("/tmp/reemote-openrc-config-")
    assert spool.endswith("/config")
    assert len(spool.rsplit("-", 1)[1].split("/")[0]) == 16


def test_openrc_config_upload_str_and_never_elevates():
    """The transfer has no ``shell()``; its loggable form is the ``__str__``
    rendering, and ``command_shell`` never prefixes it with an escalator."""
    spool = _config_spool_path("/etc/rc.conf")
    cmd = OpenrcConfigUploadCommand(content='rc_parallel="YES"\n', dest=spool)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <openrc config> -> {spool}"
    assert command_shell(cmd, sudo=True) == str(cmd)
    assert command_shell(cmd, doas=True) == str(cmd)


def test_openrc_config_install_shell_and_elevation():
    """The install wraps the whole ``&&`` chain in ``sh -c`` so the
    escalator elevates every step, cleanup included."""
    spool = _config_spool_path("/etc/rc.conf")
    spool_dir = spool.rsplit("/", 1)[0]
    cmd = OpenrcConfigInstallCommand(spool=spool, dest="/etc/rc.conf")
    inner = (
        f"mkdir -p /etc && cp {spool} /etc/rc.conf && "
        f"chmod 0644 /etc/rc.conf && rm -rf {spool_dir}"
    )
    assert cmd.shell() == f"sh -c {shlex.quote(inner)}"
    assert command_shell(cmd, sudo=True) == f"sudo sh -c {shlex.quote(inner)}"
    assert command_shell(cmd, doas=True) == f"doas -n sh -c {shlex.quote(inner)}"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(cmd.shell())}"


def test_openrc_config_install_quotes_paths_with_spaces():
    """A space-carrying directory proves the quoting (a space-free path is
    left unquoted by shlex.quote); assert through shlex.split, never a raw
    substring of the quoted wrapper."""
    cmd = OpenrcConfigInstallCommand(
        spool="/tmp/spool dir/config x", dest="/etc/openrc conf.d/wei rd"
    )
    inner = shlex.split(cmd.shell())[2]
    assert "mkdir -p '/etc/openrc conf.d'" in inner
    assert "cp '/tmp/spool dir/config x' '/etc/openrc conf.d/wei rd'" in inner
    assert "chmod 0644 '/etc/openrc conf.d/wei rd'" in inner
    assert "rm -rf '/tmp/spool dir'" in inner


# --- openrc.config: the operation -----------------------------------------------


async def test_config_set_on_an_absent_file_yields_the_pair():
    """No rc.conf yet: the transform's single line is spooled and installed
    (the install's mkdir -p creates the parent)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.config(ctx, key="rc_parallel", value="YES")]
    assert ctx.changed
    assert ctx.reason == 'rc_parallel="YES" will be set in /etc/rc.conf'
    spool = _config_spool_path("/etc/rc.conf")
    assert commands == [
        OpenrcConfigUploadCommand(content='rc_parallel="YES"\n', dest=spool),
        OpenrcConfigInstallCommand(spool=spool, dest="/etc/rc.conf"),
    ]


async def test_config_set_replaces_the_existing_value():
    """The spooled content is the whole transformed file: the stale active
    and commented assignments are gone, the unrelated lines survive."""
    host = SimulatedHost(name="h", remote_files={"/etc/rc.conf": RC_CONF_TEXT.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.config(ctx, key="rc_parallel", value="YES")]
    assert ctx.changed
    assert commands[0].content == (
        '# /etc/rc.conf\nrc_logger="YES"\nrc_parallel="YES"\n'
    )


async def test_config_set_noop_when_the_transform_is_a_fixpoint():
    """The file's only assignment of the key already carries the wanted
    value: nothing to spool, the content-level no-op reason names the path."""
    host = SimulatedHost(
        name="h", remote_files={"/etc/rc.conf": b'rc_parallel="YES"\n'}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.config(ctx, key="rc_parallel", value="YES")]
    assert not ctx.changed
    assert ctx.reason == 'rc_parallel="YES" is already set in /etc/rc.conf'
    assert commands == []


async def test_config_set_cleans_a_commented_assignment_even_when_set():
    """The replacement semantics reach what a value-only comparison never
    would: a correct active value plus a commented-out variant still plans
    a change, because the transform drops the commented line."""
    host = SimulatedHost(
        name="h",
        remote_files={"/etc/rc.conf": b'#rc_parallel=NO\nrc_parallel="YES"\n'},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in openrc.config(ctx, key="rc_parallel", value="YES")]
    assert ctx.changed
    assert commands[0].content == 'rc_parallel="YES"\n'


async def test_config_service_targets_the_conf_d_file():
    """``service`` selects /etc/conf.d/<service>, and the spool is derived
    from that path -- never the rc.conf spool."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.config(
            ctx, key="sshd_opts", value="-4", service="sshd"
        )
    ]
    assert ctx.changed
    assert ctx.reason == 'sshd_opts="-4" will be set in /etc/conf.d/sshd'
    spool = _config_spool_path("/etc/conf.d/sshd")
    assert commands == [
        OpenrcConfigUploadCommand(content='sshd_opts="-4"\n', dest=spool),
        OpenrcConfigInstallCommand(spool=spool, dest="/etc/conf.d/sshd"),
    ]


async def test_config_unset_drops_every_assignment():
    """``present=False`` spools the file without the key's assignments;
    the value argument is ignored on this direction."""
    host = SimulatedHost(name="h", remote_files={"/etc/rc.conf": RC_CONF_TEXT.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.config(
            ctx, key="rc_parallel", value="ignored", present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "rc_parallel will be unset in /etc/rc.conf"
    assert commands[0].content == '# /etc/rc.conf\nrc_logger="YES"\n'


async def test_config_unset_noop_when_the_key_is_absent():
    """A file that never assigns the key is already in state."""
    host = SimulatedHost(
        name="h", remote_files={"/etc/rc.conf": b'rc_logger="YES"\n'}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.config(ctx, key="rc_parallel", value="", present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "rc_parallel is not set in /etc/rc.conf"
    assert commands == []


async def test_config_unset_on_an_absent_file_noops():
    """None content transforms to \"\", which equals the absent file's
    normalised text -- no spool, no install."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.config(ctx, key="rc_parallel", value="", present=False)
    ]
    assert not ctx.changed
    assert commands == []


async def test_config_unset_empties_the_file_to_a_blank_payload():
    """A file holding only the key's assignments spools content=\"\" -- a
    legitimate payload the descriptor never special-cases."""
    host = SimulatedHost(
        name="h", remote_files={"/etc/rc.conf": b'rc_parallel="YES"\n'}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.config(ctx, key="rc_parallel", value="", present=False)
    ]
    assert ctx.changed
    assert commands[0].content == ""


@pytest.mark.parametrize("key", ["", "1key", "rc-parallel", "key; reboot", "a b"])
async def test_config_rejects_a_non_identifier_key(key):
    """Validation raises while materializing, before any fact is read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="not a shell variable name"):
        [c async for c in openrc.config(ctx, key=key, value="YES")]


@pytest.mark.parametrize("service", ["", ".", "..", "a/b", "sshd; reboot"])
async def test_config_rejects_an_invalid_service_name(service):
    """The service charset (plus the dot special cases) keeps the path
    inside /etc/conf.d."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="not an OpenRC service name"):
        [c async for c in openrc.config(ctx, key="k", value="v", service=service)]


@pytest.mark.parametrize("value", ['a"b', "a\\b", "a$b", "a`b", "a\nb", "a\rb"])
async def test_config_rejects_shell_active_values(value):
    """A value that would break out of the double-quoted rendering raises
    instead of being written into a shell-sourced file."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="shell-active characters"):
        [c async for c in openrc.config(ctx, key="k", value=value)]


async def test_config_unset_ignores_an_unsafe_value():
    """The value validation gates the set direction only: an unset never
    renders the value, so even a shell-active one is accepted."""
    host = SimulatedHost(name="h", remote_files={"/etc/rc.conf": b'k="v"\n'})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.config(ctx, key="k", value='a"b', present=False)
    ]
    assert ctx.changed
    assert commands[0].content == ""


async def test_config_accepts_an_empty_value_and_absorbs_kwargs():
    """``key=\"\"`` is a legitimate assignment; an unknown keyword (a
    the baseline global) is absorbed by ``**kwargs`` with no effect."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in openrc.config(
            ctx, key="rc_parallel", value="", extra="ignored"
        )
    ]
    assert ctx.changed
    assert commands[0].content == 'rc_parallel=""\n'


# --- openrc.config: the SimulatedHost seam --------------------------------------


async def test_config_upload_seam_writes_only_the_spool():
    """The transfer arm stores the bytes at the spool path alone; the
    managed file is untouched until the install runs."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path("/etc/rc.conf")
    await send_command(
        host, OpenrcConfigUploadCommand(content='rc_parallel="YES"\n', dest=spool)
    )
    assert host.remote_files == {spool: b'rc_parallel="YES"\n'}
    assert "/etc/rc.conf" not in host.existing_files


async def test_config_install_seam_moves_the_spool_and_stats_the_dest():
    """The install arm pops the spool, stores it at dest and refreshes the
    existence and metadata seams together (root/root, mode 644, size)."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path("/etc/rc.conf")
    host.remote_files[spool] = b'rc_parallel="YES"\n'
    await send_command(host, OpenrcConfigInstallCommand(spool=spool, dest="/etc/rc.conf"))
    assert host.remote_files == {"/etc/rc.conf": b'rc_parallel="YES"\n'}
    assert "/etc/rc.conf" in host.existing_files
    assert host.file_stats["/etc/rc.conf"] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": 18,
    }
    # The content fact reads the installed text back through the same store.
    assert await OpenrcConfigFile.process(host, "/etc/rc.conf") == 'rc_parallel="YES"\n'


async def test_config_install_seam_without_a_spool_changes_nothing():
    """An absent spool installs nothing (the paired install mutators'
    tolerance)."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path("/etc/rc.conf")
    await send_command(host, OpenrcConfigInstallCommand(spool=spool, dest="/etc/rc.conf"))
    assert host.remote_files == {}
    assert host.existing_files == set()


async def test_config_install_seam_repeat_overwrites_never_appends():
    """A second install with fresh spool content replaces the file -- the
    overlay is content-based, so a repeat never accumulates lines."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path("/etc/rc.conf")
    host.remote_files[spool] = b'rc_parallel="YES"\n'
    await send_command(host, OpenrcConfigInstallCommand(spool=spool, dest="/etc/rc.conf"))
    host.remote_files[spool] = b'rc_parallel="NO"\n'
    await send_command(host, OpenrcConfigInstallCommand(spool=spool, dest="/etc/rc.conf"))
    assert host.remote_files == {"/etc/rc.conf": b'rc_parallel="NO"\n'}


# --- openrc.config: end to end ---------------------------------------------------


async def test_config_end_to_end_set_then_noop_under_doas():
    """Through run_prepare/run_apply: the unprivileged scp spools the text,
    the elevated sh -c installs it, and a second prepare sees the fixpoint
    (the fresh per-step fact cache observes the installed content)."""

    def set_deploy(host):
        yield op(
            openrc.config,
            name="Enable parallel startup",
            key="rc_parallel",
            value="YES",
            doas=True,
        )

    host = SimulatedHost(name="h", remote_files={"/etc/rc.conf": RC_CONF_TEXT.encode()})
    runtimes, plans = await run_prepare([host], set_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    spool = _config_spool_path("/etc/rc.conf")
    assert entry.commands == (
        OpenrcConfigUploadCommand(
            content='# /etc/rc.conf\nrc_logger="YES"\nrc_parallel="YES"\n', dest=spool
        ),
        OpenrcConfigInstallCommand(spool=spool, dest="/etc/rc.conf"),
    )
    log = format_log(report)
    # Two privilege levels in one request: the transfer never elevates.
    assert f"$ scp <openrc config> -> {spool}" in log
    assert "$ doas -n sh -c 'mkdir -p /etc && cp " in log
    assert host.remote_files["/etc/rc.conf"] == (
        b'# /etc/rc.conf\nrc_logger="YES"\nrc_parallel="YES"\n'
    )
    _runtimes, plans2 = await run_prepare([host], set_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == 'rc_parallel="YES" is already set in /etc/rc.conf'


async def test_config_end_to_end_two_identical_requests_in_one_deploy():
    """Both plan as changes (prepare never sends), but the apply log shows
    the second as a no-op: the first install is visible to its fact read."""

    def twice_deploy(host):
        yield op(openrc.config, name="Set rc_parallel", key="rc_parallel", value="YES", doas=True)
        yield op(openrc.config, name="Set rc_parallel again", key="rc_parallel", value="YES", doas=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], twice_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    assert report.log[0].changed
    assert not report.log[1].changed
    assert report.log[1].commands == ()


async def test_config_end_to_end_set_then_unset_in_one_deploy():
    """The unset step sees the content the set step installed: the key is
    dropped again and the file empties to the blank payload."""

    def cycle_deploy(host):
        yield op(openrc.config, name="Set", key="rc_parallel", value="YES", doas=True)
        yield op(
            openrc.config,
            name="Unset",
            key="rc_parallel",
            value="",
            present=False,
            doas=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    # Prepare compares both steps against the same absent file: the set
    # plans a change, the unset plans none.
    assert plans["h"][0].changed
    assert not plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    assert report.log[0].changed
    assert report.log[1].changed
    assert report.log[1].commands[0].content == ""
    assert host.remote_files["/etc/rc.conf"] == b""


async def test_config_end_to_end_conf_d_service_scope():
    """A service-scoped request installs under /etc/conf.d, creating the
    directory through the install's mkdir -p."""

    def sshd_deploy(host):
        yield op(
            openrc.config,
            name="Restrict sshd to IPv4",
            key="sshd_opts",
            value="-4",
            service="sshd",
            doas=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], sshd_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ doas -n sh -c 'mkdir -p /etc/conf.d && cp " in log
    assert host.remote_files["/etc/conf.d/sshd"] == b'sshd_opts="-4"\n'
    assert "/etc/conf.d/sshd" in host.existing_files


async def test_config_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the pair is planned but neither
    the spool nor the managed file appears on the host."""

    def set_deploy(host):
        yield op(openrc.config, name="Set rc_parallel", key="rc_parallel", value="YES", doas=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        set_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert len(planned.commands) == 2
    assert host.remote_files == {}
    assert host.existing_files == set()