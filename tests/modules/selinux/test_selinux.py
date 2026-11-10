# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the selinux module: ``SEBoolean``, ``Booleans``, ``FileContext``,
``FileContextMapping``, ``SEPorts``, ``SEPort``, ``Mode``, ``SELogin``,
``SEUser``, ``Modules``, ``PermissiveDomains`` and ``SelinuxConfigFile``,
plus all eleven operations.

Covers the read-only state discovery in ``reemote5.modules.selinux.selinux``:
the five pyinfra fact ports are exercised over the real-transport path with
pyinfra's own fact fixtures verbatim (the ``getsebool`` row, the ``stat -c %C``
contexts including the ``?`` unlabelled case, the grepped fcontext rows
including the no-match deviation to None, all six ``semanage port -ln``
outputs and the three ``sepolicy network`` outputs), plus the ``SimulatedHost``
seam projections and their copy semantics. The six reemote5 originals -- with
no pyinfra fixtures -- are fed realistic canned ``getsebool -a``, ``sestatus``,
``semanage login -ln``, ``semanage user -ln``, ``semodule -lfull`` and
``semanage permissive -l`` output respectively, plus the ``SEPorts`` filter
arguments, the ``gather_fact`` fan-outs and the private ``_Which`` /
``_AutorelabelPending`` probes.

The four pyinfra operation ports (``boolean``, ``file_context``,
``file_context_mapping``, ``port``) are exercised over their descriptors and
every branch of pyinfra's own operation fixtures (both ``sepolicy``-present
and absent port dispatches, the quoting-injection fixtures, the ``ValueError``
guards and the verbatim no-op reasons). The seven originals (``mode``, ``login``,
``module``, ``permissive_domain``, ``restorecon``, ``autorelabel``, ``config``)
are exercised over their fact-backed idempotence in both directions, the module
four-state machine, the disabled-subsystem guard, restorecon's deliberate
statelessness, the ``config`` spool-and-install pair (pure transforms, spool
determinism, both content-level no-ops, the SimulatedHost install mutator),
the ``SimulatedHost`` seam dispatch, and end-to-end through
``run_prepare`` + ``run_apply`` (change-then-noop, the mapping-then-restorecon
composition and a dry run).

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import shlex

import pytest

from reemote5.core import FactCache, OpContext, op
from reemote5.drivers import (
    command_shell,
    format_log,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import selinux
from reemote5.modules.selinux.selinux import (
    AUTORELABEL_PATH,
    AutorelabelCommand,
    Booleans,
    ChconCommand,
    FileContext,
    FileContextMapping,
    Mode,
    ModeInfo,
    Module,
    Modules,
    PermissiveDomains,
    RestoreconCommand,
    SEBoolean,
    SeContext,
    SelinuxConfigFile,
    SelinuxConfigInstallCommand,
    SelinuxConfigUploadCommand,
    SELogin,
    SeLogin,
    SemanageFcontextCommand,
    SemanageLoginCommand,
    SemanagePermissiveCommand,
    SemanagePortCommand,
    SemoduleEnableCommand,
    SemoduleInstallCommand,
    SemoduleRemoveCommand,
    SEPort,
    SEPorts,
    SetenforceCommand,
    SetseboolCommand,
    SEUser,
    SeUser,
    _AutorelabelPending,
    _config_spool_path,
    _set_config_value,
    _Which,
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


# --- SEBoolean: pyinfra's fixture ------------------------------------------------


def test_se_boolean_command_matches_pyinfra():
    """The shell is pyinfra's fixture command verbatim (the quoted name is
    a no-op for a plain identifier)."""
    assert SEBoolean.command("some_boolean") == "getsebool some_boolean"


async def test_se_boolean_parses_the_fixture():
    """Over the real-transport path, the fixture's ``some_boolean --> off``
    row reads as the fixture fact's ``"off"``."""
    host = CannedShellHost("some_boolean --> off\n")
    assert await SEBoolean.process(host, "some_boolean") == "off"
    assert host.last_shell == "getsebool some_boolean"


async def test_se_boolean_quotes_the_name():
    """A name carrying shell metacharacters is looked up literally -- the
    ``QuoteString`` hardening the sibling operation already applies."""
    host = CannedShellHost("")
    assert await SEBoolean.process(host, "httpd`id`") == ""
    assert host.last_shell == "getsebool 'httpd`id`'"


async def test_se_boolean_empty_output_reads_as_empty_string():
    """An unknown boolean makes getsebool fail with empty stdout, which
    reads as "" -- the parser stays total where pyinfra's split would
    raise."""
    assert await SEBoolean.process(CannedShellHost(""), "some_boolean") == ""
    assert await SEBoolean.process(CannedShellHost("garbage\n"), "some_boolean") == ""


async def test_se_boolean_seam_projects_one_name():
    """The ``selinux_booleans`` bool map projects the one requested name to
    its value word; an absent name reads as ""."""
    host = SimulatedHost(name="h", selinux_booleans={"a_bool": True, "b_bool": False})
    assert await SEBoolean.process(host, "a_bool") == "on"
    assert await SEBoolean.process(host, "b_bool") == "off"
    assert await SEBoolean.process(host, "nosuch_bool") == ""


# --- Booleans: canned getsebool -a ------------------------------------------------

GETSEBOOL_ALL_OUTPUT = """\
abrt_anon_write --> off
httpd_can_network_connect --> on
selinuxuser_ping --> on
virt_use_nfs --> off
"""

BOOLEANS_FACT = {
    "abrt_anon_write": False,
    "httpd_can_network_connect": True,
    "selinuxuser_ping": True,
    "virt_use_nfs": False,
}


async def test_booleans_parses_canned_output():
    """Over the real-transport path, every ``name --> on|off`` row becomes
    one entry, True only for "on"."""
    host = CannedShellHost(GETSEBOOL_ALL_OUTPUT)
    assert await Booleans.process(host) == BOOLEANS_FACT
    assert host.last_shell == "getsebool -a"


async def test_booleans_empty_output_reads_as_empty_dict():
    """A disabled SELinux prints its error to stderr with empty stdout,
    which reads as {} -- the parser is total."""
    assert await Booleans.process(CannedShellHost("")) == {}
    assert Booleans.parse(["garbage", "", "  "]) == {}


async def test_booleans_seam_is_copied_on_read():
    """Mutating the returned dict never mutates the simulated state."""
    host = SimulatedHost(name="h", selinux_booleans={"a_bool": True})
    result = await Booleans.process(host)
    result["a_bool"] = False
    assert host.selinux_booleans == {"a_bool": True}


# --- FileContext: pyinfra's fixtures ----------------------------------------------

# The ``fact`` field shared by pyinfra's FileContext fixtures: the
# four-component split of ``stat -c %C`` output.
NET_CONF_CONTEXT = SeContext(
    user="system_u", role="object_r", type="net_conf_t", level="s0"
)


def test_file_context_command_matches_pyinfra():
    """The shell is pyinfra's fixture command verbatim for its path."""
    assert FileContext.command("/etc/hosts") == "stat -c %C /etc/hosts || exit 0"


async def test_file_context_parses_the_hosts_fixture():
    """The ``/etc/hosts`` fixture: the context line splits into the fixture
    fact's four components."""
    host = CannedShellHost("system_u:object_r:net_conf_t:s0\n")
    assert await FileContext.process(host, "/etc/hosts") == NET_CONF_CONTEXT
    assert host.last_shell == "stat -c %C /etc/hosts || exit 0"


async def test_file_context_unlabelled_reads_as_none():
    """The ``/etc/hostname`` fixture: the ``?`` of an unlabelled filesystem
    has fewer than four components and reads as None."""
    host = CannedShellHost("?\n")
    assert await FileContext.process(host, "/etc/hostname") is None


async def test_file_context_missing_path_reads_as_none():
    """A missing path prints nothing (the ``|| exit 0`` guard swallows
    stat's error), which reads as None."""
    assert await FileContext.process(CannedShellHost(""), "/nope") is None


async def test_file_context_quotes_the_path():
    """A path carrying shell metacharacters is stat'ed literally -- the
    deliberate hardening over pyinfra's bare interpolation."""
    host = CannedShellHost("")
    assert await FileContext.process(host, "/my file; reboot") is None
    assert host.last_shell == "stat -c %C '/my file; reboot' || exit 0"


async def test_file_context_seam_is_copied_on_read():
    """The seam reads the path's entry (None when absent) and copies it, so
    a caller's mutation never reaches the simulated state."""
    host = SimulatedHost(
        name="h", selinux_file_contexts={"/etc/hosts": dict(NET_CONF_CONTEXT)}
    )
    result = await FileContext.process(host, "/etc/hosts")
    assert result == NET_CONF_CONTEXT
    result["type"] = "var_t"
    assert host.selinux_file_contexts["/etc/hosts"]["type"] == "net_conf_t"
    assert await FileContext.process(host, "/nope") is None


# --- FileContextMapping: pyinfra's fixtures ---------------------------------------

ZEBRA_MAPPING_LINE = (
    "/etc/zebra(/.*)?                                   all files          "
    "system_u:object_r:zebra_conf_t:s0"
)

ZEBRA_CONTEXT = SeContext(
    user="system_u", role="object_r", type="zebra_conf_t", level="s0"
)


def test_file_context_mapping_command_matches_pyinfra():
    """The shell is pyinfra's fixture command verbatim: pipefail, the
    local-only listing and the quoted ``^target`` grep."""
    assert FileContextMapping.command("/etc/zebra(/.*)?") == (
        "set -o pipefail && semanage fcontext -n -l | "
        "(grep '^/etc/zebra(/.*)?' || true)"
    )


async def test_file_context_mapping_parses_the_zebra_fixture():
    """The single grepped row's trailing context splits into the fixture
    fact's four components."""
    host = CannedShellHost(ZEBRA_MAPPING_LINE + "\n")
    assert await FileContextMapping.process(host, "/etc/zebra(/.*)?") == ZEBRA_CONTEXT
    assert host.last_shell == FileContextMapping.command("/etc/zebra(/.*)?")


async def test_file_context_mapping_no_match_reads_as_none():
    """The ``/etc/etc/etc/etc/etc/etc`` fixture: no row matches. The house
    deviation from pyinfra's ``{}`` -- None keeps an absent mapping
    distinguishable from a context with empty components."""
    assert await FileContextMapping.process(CannedShellHost(""), "/nope") is None


async def test_file_context_mapping_several_rows_read_as_none():
    """A prefix shared by longer targets greps several rows -- pyinfra's
    ``len(output) != 1`` gate reads None rather than guessing."""
    stdout = ZEBRA_MAPPING_LINE + "\n" + ZEBRA_MAPPING_LINE.replace(
        "/etc/zebra(", "/etc/zebra/conf(", 1
    )
    assert FileContextMapping.parse(stdout.splitlines()) is None


async def test_file_context_mapping_equivalence_row_reads_as_none():
    """An equivalence row (``a = b``) carries no context and matches
    nothing, even as the single grepped line."""
    line = "/etc/systemd/system = /usr/lib/systemd/system"
    assert FileContextMapping.parse([line]) is None


async def test_file_context_mapping_seam_is_copied_on_read():
    """The seam reads the target's entry (None when absent) and copies it."""
    host = SimulatedHost(
        name="h",
        selinux_fcontext_mappings={"/etc/zebra(/.*)?": dict(ZEBRA_CONTEXT)},
    )
    result = await FileContextMapping.process(host, "/etc/zebra(/.*)?")
    assert result == ZEBRA_CONTEXT
    result["type"] = "var_t"
    assert host.selinux_fcontext_mappings["/etc/zebra(/.*)?"]["type"] == "zebra_conf_t"
    assert await FileContextMapping.process(host, "/nope") is None


# --- SEPorts: pyinfra's fixtures ---------------------------------------------------


async def test_se_ports_noise_row_reads_as_empty_dict():
    """The garbage-row fixture: a line matching no ``type proto ports``
    shape contributes nothing -- the parser is total."""
    host = CannedShellHost("!$%(*&Q#(*&%_)*Q%#(*Q)%@#(*&Q)@(*$)Q(*@$)(*)(Q@*$()*Q@$)\n")
    assert await SEPorts.process(host) == {}
    assert host.last_shell == "semanage port -ln"


async def test_se_ports_parses_the_comma_list_fixture():
    """The ``20,30,40`` fixture: every listed port becomes an int key."""
    host = CannedShellHost("example_port_t                    tcp      20,30,40\n")
    assert await SEPorts.process(host) == {
        "tcp": {20: "example_port_t", 30: "example_port_t", 40: "example_port_t"}
    }


async def test_se_ports_parses_the_sctp_fixture():
    """The ``sctp 99`` fixture: protocols beyond tcp/udp are kept."""
    host = CannedShellHost("example_port_t                    sctp      99\n")
    assert await SEPorts.process(host) == {"sctp": {99: "example_port_t"}}


async def test_se_ports_expands_ranges_and_skips_unreserved():
    """The range fixture: ``50-52`` expands to its three ports and every
    ``unreserved_port_t`` row is skipped -- pyinfra's verbatim fact."""
    stdout = """\
example_port_t                    tcp      50-52
unreserved_port_t              sctp     1024-65535
unreserved_port_t              tcp      61000-65535, 1024-32767
unreserved_port_t              udp      61000-65535, 1024-32767
"""
    assert await SEPorts.process(CannedShellHost(stdout)) == {
        "tcp": {50: "example_port_t", 51: "example_port_t", 52: "example_port_t"}
    }


async def test_se_ports_parses_the_mixed_list_fixture():
    """The ``93, 50-51`` fixture: single ports and ranges mix in one list,
    whitespace after the comma included."""
    host = CannedShellHost("example_port_t                    tcp      93, 50-51\n")
    assert await SEPorts.process(host) == {
        "tcp": {93: "example_port_t", 50: "example_port_t", 51: "example_port_t"}
    }


async def test_se_ports_filters_by_protocol_and_type():
    """The reemote5 extension: ``protocol`` keeps one protocol, ``se_type``
    keeps only its ports, and a protocol left empty is dropped -- on both
    transports with the same shell."""
    stdout = """\
http_port_t                    tcp      80,443
dns_port_t                     udp      53
http_port_t                    udp      8080
"""
    host = CannedShellHost(stdout)
    assert await SEPorts.process(host, protocol="tcp") == {
        "tcp": {80: "http_port_t", 443: "http_port_t"}
    }
    assert host.last_shell == "semanage port -ln"
    assert await SEPorts.process(host, se_type="http_port_t") == {
        "tcp": {80: "http_port_t", 443: "http_port_t"},
        "udp": {8080: "http_port_t"},
    }
    assert await SEPorts.process(host, protocol="udp", se_type="dns_port_t") == {
        "udp": {53: "dns_port_t"}
    }
    assert await SEPorts.process(host, protocol="tcp", se_type="dns_port_t") == {}
    seam_host = SimulatedHost(
        name="h",
        selinux_ports={
            "tcp": {80: "http_port_t", 443: "http_port_t"},
            "udp": {53: "dns_port_t", 8080: "http_port_t"},
        },
    )
    assert await SEPorts.process(seam_host, se_type="dns_port_t") == {
        "udp": {53: "dns_port_t"}
    }


async def test_se_ports_seam_is_deep_copied_on_read():
    """Mutating the returned map or its inner dicts never mutates the
    simulated state."""
    host = SimulatedHost(name="h", selinux_ports={"tcp": {80: "http_port_t"}})
    result = await SEPorts.process(host)
    result["tcp"][80] = "var_t"
    result["udp"] = {}
    assert host.selinux_ports == {"tcp": {80: "http_port_t"}}


# --- SEPort: pyinfra's fixtures ----------------------------------------------------


async def test_se_port_parses_the_ssh_fixture():
    """The ``tcp 22`` fixture: two rows mean the first is the specific
    label, whose third field is the answer."""
    stdout = "22: tcp ssh_port_t 22\n22: tcp reserved_port_t 1-511\n"
    host = CannedShellHost(stdout)
    assert await SEPort.process(host, "tcp", 22) == "ssh_port_t"
    assert host.last_shell == "(sepolicy network -p 22 2>/dev/null || true) | grep tcp"


async def test_se_port_no_rows_read_as_empty_string():
    """The ``portname`` fixture: a name sepolicy cannot resolve prints
    nothing, which reads as ""."""
    host = CannedShellHost("")
    assert await SEPort.process(host, "tcp", "portname") == ""
    assert host.last_shell == (
        "(sepolicy network -p portname 2>/dev/null || true) | grep tcp"
    )


async def test_se_port_generic_range_alone_reads_as_empty_string():
    """The ``tcp 9997`` fixture: a port covered only by the generic
    ``unreserved_port_t`` range reads as "" -- pyinfra's ``len > 1`` gate."""
    host = CannedShellHost("9997: tcp unreserved_port_t 1024-32767\n")
    assert await SEPort.process(host, "tcp", 9997) == ""


async def test_se_port_seam_projects_one_pair():
    """The ``selinux_ports`` seam projects the one protocol/port pair; an
    unlabelled pair and a non-numeric port both read as ""."""
    host = SimulatedHost(name="h", selinux_ports={"tcp": {22: "ssh_port_t"}})
    assert await SEPort.process(host, "tcp", 22) == "ssh_port_t"
    assert await SEPort.process(host, "tcp", 9997) == ""
    assert await SEPort.process(host, "udp", 22) == ""
    assert await SEPort.process(host, "tcp", "portname") == ""


# --- Mode: canned sestatus ----------------------------------------------------------

SESTATUS_ENFORCING_OUTPUT = """\
SELinux status:                 enabled
SELinuxfs mount:                /sys/fs/selinux
SELinux root directory:         /etc/selinux
Loaded policy name:             targeted
Current mode:                   enforcing
Mode from config file:          enforcing
Policy MLS status:              enabled
Policy deny_unknown status:     allowed
Max kernel policy version:      33
"""


async def test_mode_parses_canned_sestatus():
    """Over the real-transport path, the runtime mode, config-file mode and
    loaded policy come from one ``sestatus`` read."""
    host = CannedShellHost(SESTATUS_ENFORCING_OUTPUT)
    assert await Mode.process(host) == ModeInfo(
        mode="enforcing", config_mode="enforcing", policy="targeted"
    )
    assert host.last_shell == "sestatus"


async def test_mode_distinguishes_runtime_from_config():
    """A host switched permissive at runtime but enforcing at boot reports
    both -- the drift the fact exists to expose."""
    stdout = SESTATUS_ENFORCING_OUTPUT.replace(
        "Current mode:                   enforcing",
        "Current mode:                   permissive",
        1,
    )
    assert Mode.parse(stdout.splitlines()) == ModeInfo(
        mode="permissive", config_mode="enforcing", policy="targeted"
    )


async def test_mode_disabled_reports_disabled_alone():
    """A disabled subsystem prints only its status line: the mode reads
    "disabled" and the config/policy fields stay empty."""
    assert Mode.parse(["SELinux status:                 disabled"]) == ModeInfo(
        mode="disabled", config_mode="", policy=""
    )


async def test_mode_empty_output_reads_as_empty_mode():
    """Empty output reads as an empty mode rather than guessing."""
    assert Mode.parse([]) == ModeInfo(mode="", config_mode="", policy="")


async def test_mode_seam_is_copied_on_read():
    """The seam defaults to an enforcing/targeted host and is copied."""
    host = SimulatedHost(name="h")
    result = await Mode.process(host)
    assert result == ModeInfo(
        mode="enforcing", config_mode="enforcing", policy="targeted"
    )
    result["mode"] = "permissive"
    assert host.selinux_mode["mode"] == "enforcing"


# --- SELogin: canned semanage login -ln --------------------------------------------

SEMANAGE_LOGIN_OUTPUT = """\
__default__          unconfined_u         s0-s0:c0.c1023       *
deploy               staff_u              s0                   *
"""

LOGINS_FACT = {
    "__default__": SeLogin(
        seuser="unconfined_u", mls_range="s0-s0:c0.c1023", service="*"
    ),
    "deploy": SeLogin(seuser="staff_u", mls_range="s0", service="*"),
}


async def test_se_login_parses_canned_output():
    """Over the real-transport path, each row keys by the Linux login and
    keeps the SELinux user, range and Service column."""
    host = CannedShellHost(SEMANAGE_LOGIN_OUTPUT)
    assert await SELogin.process(host) == LOGINS_FACT
    assert host.last_shell == "semanage login -ln"


async def test_se_login_absent_service_column_defaults_to_star():
    """Older semanage releases print no Service column: the entry reads
    the universal ``*``."""
    stdout = "deploy               staff_u              s0\n"
    assert SELogin.parse(stdout.splitlines()) == {
        "deploy": SeLogin(seuser="staff_u", mls_range="s0", service="*")
    }


async def test_se_login_short_rows_contribute_nothing():
    """Blank and short lines are ignored -- the parser is total."""
    assert SELogin.parse(["", "  ", "deploy staff_u"]) == {}


async def test_se_login_seam_entries_are_copied_on_read():
    """Mutating a returned entry never mutates the simulated state."""
    host = SimulatedHost(
        name="h",
        selinux_logins={"deploy": SeLogin(seuser="staff_u", mls_range="s0", service="*")},
    )
    result = await SELogin.process(host)
    result["deploy"]["seuser"] = "sysadm_u"
    assert host.selinux_logins["deploy"]["seuser"] == "staff_u"


# --- SEUser: canned semanage user -ln ----------------------------------------------

SEMANAGE_USER_OUTPUT = """\
guest_u              user       s0         s0                       guest_r
staff_u              staff      s0         s0-s0:c0.c1023           staff_r sysadm_r
unconfined_u         unconfined s0         s0-s0:c0.c1023           system_r unconfined_r
"""

USERS_FACT = {
    "guest_u": SeUser(
        prefix="user", mls_level="s0", mls_range="s0", roles=["guest_r"]
    ),
    "staff_u": SeUser(
        prefix="staff",
        mls_level="s0",
        mls_range="s0-s0:c0.c1023",
        roles=["staff_r", "sysadm_r"],
    ),
    "unconfined_u": SeUser(
        prefix="unconfined",
        mls_level="s0",
        mls_range="s0-s0:c0.c1023",
        roles=["system_r", "unconfined_r"],
    ),
}


async def test_se_user_parses_canned_output():
    """Over the real-transport path, each row keys by the SELinux user and
    splits the variable-length roles tail."""
    host = CannedShellHost(SEMANAGE_USER_OUTPUT)
    assert await SEUser.process(host) == USERS_FACT
    assert host.last_shell == "semanage user -ln"


async def test_se_user_short_rows_contribute_nothing():
    """A row without at least one role is ignored -- the parser is total."""
    assert SEUser.parse(["", "guest_u user s0 s0"]) == {}


async def test_se_user_seam_copies_entries_and_roles():
    """Mutating a returned entry or its roles list never mutates the
    simulated state."""
    host = SimulatedHost(
        name="h",
        selinux_users={
            "staff_u": SeUser(
                prefix="staff",
                mls_level="s0",
                mls_range="s0-s0:c0.c1023",
                roles=["staff_r", "sysadm_r"],
            )
        },
    )
    result = await SEUser.process(host)
    result["staff_u"]["roles"].append("guest_r")
    result["staff_u"]["prefix"] = "user"
    assert host.selinux_users["staff_u"]["roles"] == ["staff_r", "sysadm_r"]
    assert host.selinux_users["staff_u"]["prefix"] == "staff"


# --- Modules: canned semodule -lfull ------------------------------------------------

SEMODULE_LFULL_OUTPUT = """\
400 base                          pp
400 myhttpd                       pp disabled
200 myhttpd                       pp
100 zebra                         cil
"""

MODULES_FACT = [
    Module(name="base", priority=400, format="pp", enabled=True),
    Module(name="myhttpd", priority=400, format="pp", enabled=False),
    Module(name="myhttpd", priority=200, format="pp", enabled=True),
    Module(name="zebra", priority=100, format="cil", enabled=True),
]


async def test_modules_parses_canned_output():
    """Over the real-transport path, every ``priority name format
    [disabled]`` row becomes one instance in listing order -- the disabled
    and lower-priority duplicates plain ``-l`` would hide survive."""
    host = CannedShellHost(SEMODULE_LFULL_OUTPUT)
    assert await Modules.process(host) == MODULES_FACT
    assert host.last_shell == "semodule -lfull"


async def test_modules_non_numeric_rows_contribute_nothing():
    """Headers and notices (a non-numeric first field) are ignored -- the
    parser is total."""
    assert Modules.parse(["", "Modules:", "foo bar pp"]) == []


async def test_modules_seam_entries_are_copied_on_read():
    """Mutating a returned instance never mutates the simulated state."""
    host = SimulatedHost(
        name="h",
        selinux_modules=[Module(name="myhttpd", priority=400, format="pp", enabled=True)],
    )
    result = await Modules.process(host)
    result[0]["enabled"] = False
    assert host.selinux_modules[0]["enabled"] is True


# --- PermissiveDomains: canned semanage permissive -l -------------------------------

SEMANAGE_PERMISSIVE_OUTPUT = """\
Built-in Permissive Types

Customized Permissive Types

cupsd_t
my_daemon_t
"""


async def test_permissive_domains_parses_canned_output():
    """Over the real-transport path, both section headers drop and every
    domain from both sections is kept in listing order."""
    host = CannedShellHost(SEMANAGE_PERMISSIVE_OUTPUT)
    assert await PermissiveDomains.process(host) == ["cupsd_t", "my_daemon_t"]
    assert host.last_shell == "semanage permissive -l"


async def test_permissive_domains_dedupes_preserving_order():
    """A domain listed twice is kept once, at its first position."""
    assert PermissiveDomains.parse(["a_t", "b_t", "a_t"]) == ["a_t", "b_t"]


async def test_permissive_domains_empty_reads_as_empty_list():
    """No permissive domains (headers only, or nothing) reads as []."""
    assert PermissiveDomains.parse(["", "Customized Permissive Types"]) == []
    assert await PermissiveDomains.process(CannedShellHost("")) == []


async def test_permissive_domains_seam_is_copied_on_read():
    """Mutating the returned list never mutates the simulated state."""
    host = SimulatedHost(name="h", selinux_permissive_domains=["cupsd_t"])
    result = await PermissiveDomains.process(host)
    result.append("my_daemon_t")
    assert host.selinux_permissive_domains == ["cupsd_t"]


# --- Facts: gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_mode_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name."""
    host1 = SimulatedHost(
        name="web1",
        selinux_mode=ModeInfo(mode="permissive", config_mode="enforcing", policy="mls"),
    )
    host2 = SimulatedHost(name="web2")
    modes = await gather_fact([host1, host2], Mode)
    assert modes == {
        "web1": ModeInfo(mode="permissive", config_mode="enforcing", policy="mls"),
        "web2": ModeInfo(mode="enforcing", config_mode="enforcing", policy="targeted"),
    }


async def test_gather_fact_booleans_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(GETSEBOOL_ALL_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    booleans = await gather_fact([host1, host2], Booleans)
    assert booleans == {"web1": BOOLEANS_FACT, "web2": {}}
    assert host1.last_shell == Booleans.COMMAND
    assert host2.last_shell == Booleans.COMMAND


async def test_gather_fact_se_ports_fans_out_the_filters():
    """The protocol/type filters fan out to every host's seam read."""
    host1 = SimulatedHost(
        name="web1", selinux_ports={"tcp": {80: "http_port_t"}, "udp": {53: "dns_port_t"}}
    )
    host2 = SimulatedHost(name="web2")
    ports = await gather_fact([host1, host2], SEPorts, "tcp")
    assert ports == {"web1": {"tcp": {80: "http_port_t"}}, "web2": {}}


async def test_gather_fact_modules_reads_simulated_seams():
    host1 = SimulatedHost(
        name="web1",
        selinux_modules=[Module(name="myhttpd", priority=400, format="pp", enabled=True)],
    )
    host2 = SimulatedHost(name="web2")
    modules = await gather_fact([host1, host2], Modules)
    assert modules == {
        "web1": [Module(name="myhttpd", priority=400, format="pp", enabled=True)],
        "web2": [],
    }


# --- Private probes: _Which and _AutorelabelPending ----------------------------------


async def test_which_probe_reads_the_command_paths_seam():
    """The selinux-local ``server.Which`` stand-in reads the same
    ``command_paths`` seam: a named command resolves, an absent one None."""
    host = SimulatedHost(name="h", command_paths={"sepolicy": "/usr/bin/sepolicy"})
    assert await _Which.process(host, "sepolicy") == "/usr/bin/sepolicy"
    assert await _Which.process(host, "nosuchcommand") is None
    assert await _Which.process(SimulatedHost(name="h"), "sepolicy") is None


async def test_which_probe_over_the_real_transport():
    """On a real host the probe is ``command -v <name> || true``; the
    swallowed failure reads as None."""
    host = CannedShellHost("/usr/bin/sepolicy\n")
    assert await _Which.process(host, "sepolicy") == "/usr/bin/sepolicy"
    assert host.last_shell == "command -v sepolicy || true"
    assert await _Which.process(CannedShellHost(""), "sepolicy") is None


async def test_autorelabel_pending_reads_the_existing_files_seam():
    """The selinux-local ``files.FileExists`` stand-in reads the same
    ``existing_files`` seam: the flag's presence is membership."""
    assert await _AutorelabelPending.process(SimulatedHost(name="h")) is False
    host = SimulatedHost(name="h", existing_files={AUTORELABEL_PATH})
    assert await _AutorelabelPending.process(host) is True


async def test_autorelabel_pending_over_the_real_transport():
    """On a real host the probe is ``test -e /.autorelabel && echo yes``;
    a missing flag prints nothing and reads as False."""
    host = CannedShellHost("yes\n")
    assert await _AutorelabelPending.process(host) is True
    assert host.last_shell == "test -e /.autorelabel && echo yes"
    assert await _AutorelabelPending.process(CannedShellHost("")) is False# --- Command descriptors -----------------------------------------------------------


def test_setsebool_command_renders_both_persistences():
    """pyinfra's ``setsebool -P`` and runtime-only forms."""
    assert SetseboolCommand(boolean="some_boolean", value="on", persistent=True).shell() == (
        "setsebool -P some_boolean on"
    )
    assert SetseboolCommand(boolean="some_boolean", value="off").shell() == (
        "setsebool some_boolean off"
    )


def test_setsebool_command_shell_quotes_the_name():
    """pyinfra's ``change_bool_name_with_metachars`` fixture: the backticked
    name reaches setsebool literally, never the remote shell."""
    cmd = SetseboolCommand(boolean="httpd`id`", value="on", persistent=True)
    assert cmd.shell() == "setsebool -P 'httpd`id`' on"


def test_setsebool_command_is_elevated_by_sudo():
    """setsebool needs root; the escalation prefixes the whole shell."""
    cmd = SetseboolCommand(boolean="some_boolean", value="on", persistent=True)
    assert command_shell(cmd) == "setsebool -P some_boolean on"
    assert command_shell(cmd, sudo=True) == "sudo setsebool -P some_boolean on"
    assert command_shell(cmd, su=True) == (
        f"su -c {shlex.quote('setsebool -P some_boolean on')}"
    )


def test_chcon_command_renders_type_only():
    """pyinfra's ``change.json``: a type-only request is the exact
    ``chcon -t var_t /etc``."""
    assert ChconCommand(path="/etc", se_type="var_t").shell() == "chcon -t var_t /etc"


def test_chcon_command_renders_the_full_context():
    """The reemote5 extension: all four components render in chcon(1)'s
    ``-u -r -t -l`` order."""
    cmd = ChconCommand(
        path="/web", se_user="system_u", se_role="object_r",
        se_type="httpd_sys_content_t", se_level="s0",
    )
    assert cmd.shell() == (
        "chcon -u system_u -r object_r -t httpd_sys_content_t -l s0 /web"
    )


def test_chcon_command_shell_quotes_components_and_path():
    """Every component and the path are quoted, so a metacharacter never
    executes."""
    cmd = ChconCommand(path="/my file; reboot", se_type="var_t; whoami")
    assert cmd.shell() == "chcon -t 'var_t; whoami' '/my file; reboot'"


def test_semanage_fcontext_command_renders_all_three_verbs():
    """One present-flagged descriptor covers add, modify and delete (the
    ``RcUpdateCommand`` precedent), matching pyinfra's fixture commands."""
    assert SemanageFcontextCommand(
        target="/web(/.*)?", se_type="httpd_sys_content_t"
    ).shell() == "semanage fcontext -a -t httpd_sys_content_t '/web(/.*)?'"
    assert SemanageFcontextCommand(
        target="/web(/.*)?", se_type="httpd_sys_content_t", modify=True
    ).shell() == "semanage fcontext -m -t httpd_sys_content_t '/web(/.*)?'"
    assert SemanageFcontextCommand(target="/web(/.*)?", present=False).shell() == (
        "semanage fcontext -d '/web(/.*)?'"
    )


def test_semanage_port_command_renders_all_three_verbs():
    """The port descriptor matches pyinfra's fixture commands verbatim,
    including the ``-m`` modify branch."""
    assert SemanagePortCommand(protocol="tcp", port=22, se_type="ssh_port_t").shell() == (
        "semanage port -a -t ssh_port_t -p tcp 22"
    )
    assert SemanagePortCommand(
        protocol="tcp", port=22, se_type="ssh_port_t", modify=True
    ).shell() == "semanage port -m -t ssh_port_t -p tcp 22"
    assert SemanagePortCommand(protocol="tcp", port=22, present=False).shell() == (
        "semanage port -d -p tcp 22"
    )


def test_semanage_port_command_shell_quotes_the_injection_fixture():
    """pyinfra's ``security_injection`` fixture: both the type and the
    protocol carry metacharacters and reach semanage literally."""
    cmd = SemanagePortCommand(
        protocol="tcp; rm -rf /", port=80, se_type="http_port_t; whoami"
    )
    assert cmd.shell() == (
        "semanage port -a -t 'http_port_t; whoami' -p 'tcp; rm -rf /' 80"
    )


def test_setenforce_command_renders_the_numeric_modes():
    """setenforce(8)'s numeric arguments: enforcing is 1, permissive 0."""
    assert SetenforceCommand(mode="enforcing").shell() == "setenforce 1"
    assert SetenforceCommand(mode="permissive").shell() == "setenforce 0"
    assert command_shell(SetenforceCommand(mode="permissive"), sudo=True) == (
        "sudo setenforce 0"
    )


def test_semanage_login_command_renders_all_three_verbs():
    """Add, modify and delete of one login mapping, user and range quoted."""
    assert SemanageLoginCommand(login="deploy", seuser="staff_u", mls_range="s0").shell() == (
        "semanage login -a -s staff_u -r s0 deploy"
    )
    assert SemanageLoginCommand(
        login="deploy", seuser="staff_u", mls_range="s0-s0:c0.c1023", modify=True
    ).shell() == "semanage login -m -s staff_u -r s0-s0:c0.c1023 deploy"
    assert SemanageLoginCommand(login="deploy", present=False).shell() == (
        "semanage login -d deploy"
    )


def test_semodule_commands_render_install_remove_and_enable():
    """The three module descriptors: priority-scoped install/remove and
    the name-keyed enable/disable pair."""
    assert SemoduleInstallCommand(source="/root/myhttpd.pp").shell() == (
        "semodule -X 400 -i /root/myhttpd.pp"
    )
    assert SemoduleInstallCommand(source="/root/my policy.pp", priority=500).shell() == (
        "semodule -X 500 -i '/root/my policy.pp'"
    )
    assert SemoduleRemoveCommand(name="myhttpd").shell() == "semodule -X 400 -r myhttpd"
    assert SemoduleRemoveCommand(name="myhttpd", priority=200).shell() == (
        "semodule -X 200 -r myhttpd"
    )
    assert SemoduleEnableCommand(name="myhttpd").shell() == "semodule -e myhttpd"
    assert SemoduleEnableCommand(name="myhttpd", present=False).shell() == (
        "semodule -d myhttpd"
    )


def test_semanage_permissive_command_renders_add_and_del():
    """The permissive descriptor's two directions, the domain quoted."""
    assert SemanagePermissiveCommand(domain="cupsd_t").shell() == (
        "semanage permissive -a cupsd_t"
    )
    assert SemanagePermissiveCommand(domain="my domain; reboot", present=False).shell() == (
        "semanage permissive -d 'my domain; reboot'"
    )


def test_restorecon_command_renders_the_flags():
    """Recursive by default, ``-F`` on force, both droppable."""
    assert RestoreconCommand(path="/web").shell() == "restorecon -R /web"
    assert RestoreconCommand(path="/web", recurse=False).shell() == "restorecon /web"
    assert RestoreconCommand(path="/web", force=True).shell() == "restorecon -R -F /web"
    assert RestoreconCommand(path="/web", recurse=False, force=True).shell() == (
        "restorecon -F /web"
    )
    assert RestoreconCommand(path="/my file").shell() == "restorecon -R '/my file'"


def test_autorelabel_command_touches_the_fixed_flag():
    """The flag path is the constant, never user text; root escalation
    wraps the whole shell."""
    cmd = AutorelabelCommand()
    assert cmd.shell() == "touch /.autorelabel"
    assert command_shell(cmd, sudo=True) == "sudo touch /.autorelabel"


# --- selinux.boolean: pyinfra's operation fixtures ----------------------------------


async def test_boolean_change_persistent():
    """pyinfra's ``change_persistent.json``: an off boolean turned on with
    ``persistent=True`` yields the ``-P`` form."""
    host = SimulatedHost(name="h", selinux_booleans={"some_boolean": False})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.boolean(ctx, "some_boolean", "on", persistent=True)
    ]
    assert ctx.changed
    assert ctx.reason == "boolean 'some_boolean' will be set to 'on'"
    assert commands == [
        SetseboolCommand(boolean="some_boolean", value="on", persistent=True)
    ]
    assert commands[0].shell() == "setsebool -P some_boolean on"


async def test_boolean_change_not_persistent():
    """pyinfra's ``change_not_persistent.json``: without ``persistent`` the
    change is runtime-only."""
    host = SimulatedHost(name="h", selinux_booleans={"some_boolean": True})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.boolean(ctx, "some_boolean", "off")]
    assert ctx.changed
    assert commands == [SetseboolCommand(boolean="some_boolean", value="off")]
    assert commands[0].shell() == "setsebool some_boolean off"


async def test_boolean_no_change():
    """pyinfra's ``no_change.json``: an unchanged boolean is left alone --
    the check-before-execute that spares the expensive ``-P`` rewrite --
    with pyinfra's verbatim no-op reason."""
    host = SimulatedHost(name="h", selinux_booleans={"some_boolean": True})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.boolean(ctx, "some_boolean", "on", persistent=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "boolean 'some_boolean' already had the value 'on'"
    assert commands == []


async def test_boolean_bad_value():
    """pyinfra's ``bad_value.json``: anything but on/off raises, with
    pyinfra's message verbatim."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [
            c async for c in selinux.boolean(
                ctx, "some_boolean", "really-off", persistent=True
            )
        ]
    assert str(excinfo.value) == "Invalid value 'really-off' for boolean operation"


async def test_boolean_change_with_metachars():
    """pyinfra's ``change_bool_name_with_metachars`` fixture: the backticked
    name is looked up and set literally."""
    host = SimulatedHost(name="h", selinux_booleans={"httpd`id`": False})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.boolean(ctx, "httpd`id`", "on", persistent=True)
    ]
    assert ctx.changed
    assert commands[0].shell() == "setsebool -P 'httpd`id`' on"


# --- selinux.file_context: pyinfra's fixtures + the full-context extension ----------


async def test_file_context_change():
    """pyinfra's ``change.json``: a type mismatch yields ``chcon -t``."""
    host = SimulatedHost(
        name="h",
        selinux_file_contexts={
            "/etc": SeContext(
                user="system_u", role="object_r", type="etc_t", level="s0"
            )
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.file_context(ctx, "/etc", se_type="var_t")]
    assert ctx.changed
    assert ctx.reason == "file_context: '/etc' will get type"
    assert commands == [ChconCommand(path="/etc", se_type="var_t")]
    assert commands[0].shell() == "chcon -t var_t /etc"


async def test_file_context_no_change():
    """pyinfra's ``no_change.json``: a matching type is left alone with
    pyinfra's verbatim no-op reason."""
    host = SimulatedHost(
        name="h",
        selinux_file_contexts={
            "/foo/bar": SeContext(
                user="system_u", role="object_r", type="httpd_sys_content_t", level="s0"
            )
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context(ctx, "/foo/bar", se_type="httpd_sys_content_t")
    ]
    assert not ctx.changed
    assert ctx.reason == (
        "file_context: '/foo/bar' already had type 'httpd_sys_content_t'"
    )
    assert commands == []


async def test_file_context_requires_a_component():
    """A request naming no component at all is a ValueError, not a silent
    no-op."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.file_context(ctx, "/etc")]
    assert str(excinfo.value) == (
        "file_context requires at least one of se_type, se_user, se_role or se_level"
    )


async def test_file_context_yields_only_differing_components():
    """The full-context extension: of a user+type request only the type
    differs, so only ``-t`` is yielded."""
    host = SimulatedHost(
        name="h",
        selinux_file_contexts={
            "/etc": SeContext(
                user="system_u", role="object_r", type="etc_t", level="s0"
            )
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context(
            ctx, "/etc", se_type="var_t", se_user="system_u"
        )
    ]
    assert ctx.changed
    assert ctx.reason == "file_context: '/etc' will get type"
    assert commands == [ChconCommand(path="/etc", se_type="var_t")]


async def test_file_context_unreadable_context_differs_everything():
    """A None context (pyinfra's ``or {}``) makes every requested component
    differ; the reason lists them sorted."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context(ctx, "/new", se_type="var_t", se_level="s0")
    ]
    assert ctx.changed
    assert ctx.reason == "file_context: '/new' will get level, type"
    assert commands == [ChconCommand(path="/new", se_type="var_t", se_level="s0")]


async def test_file_context_multi_component_noop():
    """A multi-component request whose every component already matches
    reports the requested context as present."""
    host = SimulatedHost(
        name="h",
        selinux_file_contexts={
            "/etc": SeContext(
                user="system_u", role="object_r", type="etc_t", level="s0"
            )
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context(
            ctx, "/etc", se_type="etc_t", se_user="system_u"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "file_context: '/etc' already had the requested context"
    assert commands == []


# --- selinux.file_context_mapping: pyinfra's operation fixtures ---------------------

WEB_TARGET = "/web(/.*)?"


def _mapping_context(se_type: str) -> SeContext:
    return SeContext(user="system_u", role="object_r", type=se_type, level="s0")


async def test_file_context_mapping_add_none_existing():
    """pyinfra's ``add_none_existeing.json``: no mapping yields ``-a`` (the
    fact's None-on-absent replaces pyinfra's {})."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context_mapping(
            ctx, WEB_TARGET, se_type="httpd_sys_content_t"
        )
    ]
    assert ctx.changed
    assert ctx.reason == "mapping for '/web(/.*)?' absent, will add"
    assert commands == [
        SemanageFcontextCommand(target=WEB_TARGET, se_type="httpd_sys_content_t")
    ]
    assert commands[0].shell() == (
        "semanage fcontext -a -t httpd_sys_content_t '/web(/.*)?'"
    )


async def test_file_context_mapping_add_different():
    """pyinfra's ``add_different.json``: a mapping with another type yields
    ``-m``."""
    host = SimulatedHost(
        name="h",
        selinux_fcontext_mappings={WEB_TARGET: _mapping_context("some_other_t")},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context_mapping(
            ctx, WEB_TARGET, se_type="httpd_sys_content_t"
        )
    ]
    assert ctx.changed
    assert ctx.reason == (
        "mapping for '/web(/.*)?' will change type to 'httpd_sys_content_t'"
    )
    assert commands == [
        SemanageFcontextCommand(
            target=WEB_TARGET, se_type="httpd_sys_content_t", modify=True
        )
    ]
    assert commands[0].shell() == (
        "semanage fcontext -m -t httpd_sys_content_t '/web(/.*)?'"
    )


async def test_file_context_mapping_add_no_change():
    """pyinfra's ``add_no_change.json``: the same type is a no-op with
    pyinfra's verbatim reason."""
    host = SimulatedHost(
        name="h",
        selinux_fcontext_mappings={
            WEB_TARGET: _mapping_context("httpd_sys_content_t")
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context_mapping(
            ctx, WEB_TARGET, se_type="httpd_sys_content_t"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == (
        "mapping for '/web(/.*)?' -> 'httpd_sys_content_t' already present"
    )
    assert commands == []


async def test_file_context_mapping_add_none_type():
    """pyinfra's ``add_None.json``: present without a type is a ValueError
    with pyinfra's message verbatim."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.file_context_mapping(ctx, WEB_TARGET)]
    assert str(excinfo.value) == "se_type must have a valid value if present is set"


async def test_file_context_mapping_delete_existing():
    """pyinfra's ``delete_existing.json``: an existing mapping yields ``-d``
    (no type needed)."""
    host = SimulatedHost(
        name="h",
        selinux_fcontext_mappings={
            WEB_TARGET: _mapping_context("httpd_sys_content_t")
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context_mapping(ctx, WEB_TARGET, present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "mapping for '/web(/.*)?' present, will remove"
    assert commands == [SemanageFcontextCommand(target=WEB_TARGET, present=False)]
    assert commands[0].shell() == "semanage fcontext -d '/web(/.*)?'"


async def test_file_context_mapping_delete_none_existing():
    """pyinfra's ``delete_none_existing.json``: removing an absent mapping
    is a no-op with pyinfra's verbatim reason."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.file_context_mapping(ctx, WEB_TARGET, present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no existing mapping for '/web(/.*)?'"
    assert commands == []


# --- selinux.port: pyinfra's operation fixtures (both fact dispatches) ---------------


async def test_port_add_different_direct():
    """pyinfra's ``add_different_direct.json``: with sepolicy installed the
    ``SEPort`` fact answers directly and a differing label yields ``-m``."""
    host = SimulatedHost(
        name="h",
        command_paths={"sepolicy": "/usr/bin/sepolicy"},
        selinux_ports={"tcp": {22: "xray_port_t"}},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, "ssh_port_t")]
    assert ctx.changed
    assert ctx.reason == "setype for 'tcp/22' will change to 'ssh_port_t'"
    assert commands == [
        SemanagePortCommand(protocol="tcp", port=22, se_type="ssh_port_t", modify=True)
    ]
    assert commands[0].shell() == "semanage port -m -t ssh_port_t -p tcp 22"


async def test_port_add_not_existing_direct():
    """pyinfra's ``add_not_existing_direct.json``: an unlabelled port
    yields ``-a``."""
    host = SimulatedHost(
        name="h", command_paths={"sepolicy": "/usr/bin/sepolicy"}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, "ssh_port_t")]
    assert ctx.changed
    assert ctx.reason == "setype for 'tcp/22' unset, will add"
    assert commands == [
        SemanagePortCommand(protocol="tcp", port=22, se_type="ssh_port_t")
    ]


async def test_port_add_same_direct_noop():
    """pyinfra's ``add_same_direct.json``: the same label is a no-op with
    pyinfra's verbatim reason."""
    host = SimulatedHost(
        name="h",
        command_paths={"sepolicy": "/usr/bin/sepolicy"},
        selinux_ports={"tcp": {22: "ssh_port_t"}},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, "ssh_port_t")]
    assert not ctx.changed
    assert ctx.reason == "setype for 'tcp/22' is already 'ssh_port_t'"
    assert commands == []


async def test_port_remove_existing_direct():
    """pyinfra's ``remove_existing_direct.json``: a labelled port with
    ``present=False`` yields ``-d``."""
    host = SimulatedHost(
        name="h",
        command_paths={"sepolicy": "/usr/bin/sepolicy"},
        selinux_ports={"tcp": {22: "ssh_port_t"}},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, present=False)]
    assert ctx.changed
    assert ctx.reason == "setype for 'tcp/22' set, will remove"
    assert commands == [SemanagePortCommand(protocol="tcp", port=22, present=False)]
    assert commands[0].shell() == "semanage port -d -p tcp 22"


async def test_port_remove_not_existing_direct_noop():
    """pyinfra's ``remove_not_existing_direct.json``: removing an
    unlabelled port is a no-op with pyinfra's verbatim reason."""
    host = SimulatedHost(
        name="h", command_paths={"sepolicy": "/usr/bin/sepolicy"}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, present=False)]
    assert not ctx.changed
    assert ctx.reason == "setype for 'tcp/22' is already unset"
    assert commands == []


async def test_port_add_none_type():
    """pyinfra's ``add_None.json``: present without a type is a ValueError
    with pyinfra's message verbatim."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.port(ctx, "tcp", 22)]
    assert str(excinfo.value) == "se_type must have a valid value if present is set"


async def test_port_add_different_indirect():
    """pyinfra's ``add_different.yaml``: without sepolicy the ``SEPorts``
    map is projected and a differing label yields ``-m``."""
    host = SimulatedHost(name="h", selinux_ports={"tcp": {22: "xray_port_t"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, "ssh_port_t")]
    assert ctx.changed
    assert ctx.reason == "setype for 'tcp/22' will change to 'ssh_port_t'"
    assert commands[0].shell() == "semanage port -m -t ssh_port_t -p tcp 22"


async def test_port_add_not_existing_protocol_indirect():
    """pyinfra's ``add_not_existing_protocol.yaml``: a protocol the map
    does not list reads as unlabelled and yields ``-a``."""
    host = SimulatedHost(name="h", selinux_ports={"udp": {53: "dns_port_t"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, "ssh_port_t")]
    assert ctx.changed
    assert ctx.reason == "setype for 'tcp/22' unset, will add"
    assert commands[0].shell() == "semanage port -a -t ssh_port_t -p tcp 22"


async def test_port_add_same_indirect_noop():
    """pyinfra's ``add_same.yaml``: the projected label already matches."""
    host = SimulatedHost(name="h", selinux_ports={"tcp": {22: "ssh_port_t"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, "ssh_port_t")]
    assert not ctx.changed
    assert ctx.reason == "setype for 'tcp/22' is already 'ssh_port_t'"
    assert commands == []


async def test_port_remove_indirect_both_directions():
    """pyinfra's ``remove_existing.yaml`` / ``remove_not_existing.yaml``:
    removal yields ``-d`` when labelled, no-ops when not."""
    host = SimulatedHost(name="h", selinux_ports={"tcp": {22: "ssh_port_t"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.port(ctx, "tcp", 22, present=False)]
    assert ctx.changed
    assert commands[0].shell() == "semanage port -d -p tcp 22"
    empty = SimulatedHost(name="h")
    ctx2 = OpContext(host=empty, facts=FactCache(empty, phase="prepare"))
    commands2 = [c async for c in selinux.port(ctx2, "tcp", 22, present=False)]
    assert not ctx2.changed
    assert ctx2.reason == "setype for 'tcp/22' is already unset"
    assert commands2 == []


async def test_port_security_injection_indirect():
    """pyinfra's ``security_injection.yaml``: the metacharacter-laden
    protocol and type reach semanage quoted, never the shell."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.port(
            ctx, "tcp; rm -rf /", 80, "http_port_t; whoami"
        )
    ]
    assert ctx.changed
    assert commands[0].shell() == (
        "semanage port -a -t 'http_port_t; whoami' -p 'tcp; rm -rf /' 80"
    )# --- selinux.mode -------------------------------------------------------------------


async def test_mode_switches_the_runtime_state():
    """An enforcing host asked for permissive yields ``setenforce 0``."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.mode(ctx, "permissive")]
    assert ctx.changed
    assert ctx.reason == "SELinux is enforcing, will switch to permissive"
    assert commands == [SetenforceCommand(mode="permissive")]
    assert commands[0].shell() == "setenforce 0"


async def test_mode_switches_back_to_enforcing_by_default():
    """A permissive host with no argument gets the enforcing default."""
    host = SimulatedHost(
        name="h",
        selinux_mode=ModeInfo(
            mode="permissive", config_mode="enforcing", policy="targeted"
        ),
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.mode(ctx)]
    assert ctx.changed
    assert ctx.reason == "SELinux is permissive, will switch to enforcing"
    assert commands == [SetenforceCommand(mode="enforcing")]
    assert commands[0].shell() == "setenforce 1"


async def test_mode_noop():
    """A host already in the requested mode is left alone -- only the
    runtime mode is compared, never the config-file mode."""
    host = SimulatedHost(
        name="h",
        selinux_mode=ModeInfo(
            mode="enforcing", config_mode="permissive", policy="targeted"
        ),
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.mode(ctx, "enforcing")]
    assert not ctx.changed
    assert ctx.reason == "SELinux already in enforcing mode"
    assert commands == []


async def test_mode_invalid_value():
    """Anything but enforcing/permissive raises before any fact read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.mode(ctx, "disabled")]
    assert str(excinfo.value) == (
        "Invalid mode 'disabled' -- the runtime mode must be "
        "'enforcing' or 'permissive'"
    )


async def test_mode_disabled_subsystem_raises_pointing_at_config():
    """setenforce cannot enable a disabled SELinux: the guard points at
    selinux.config and the autorelabel-then-reboot sequence."""
    host = SimulatedHost(
        name="h",
        selinux_mode=ModeInfo(mode="disabled", config_mode="", policy=""),
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.mode(ctx, "enforcing")]
    assert str(excinfo.value) == (
        "SELinux is disabled; setenforce cannot enable it -- set "
        "SELINUX=enforcing (or permissive) with selinux.config, request "
        "selinux.autorelabel and reboot first"
    )


# --- selinux.login ------------------------------------------------------------------


async def test_login_adds_an_unmapped_user():
    """An absent mapping yields ``semanage login -a`` with the default
    ``s0`` range."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.login(ctx, "deploy", seuser="staff_u")]
    assert ctx.changed
    assert ctx.reason == "login 'deploy' unmapped, will map to 'staff_u'"
    assert commands == [
        SemanageLoginCommand(login="deploy", seuser="staff_u", mls_range="s0")
    ]
    assert commands[0].shell() == "semanage login -a -s staff_u -r s0 deploy"


async def test_login_modifies_a_differing_seuser():
    """A mapping to another SELinux user yields ``-m``."""
    host = SimulatedHost(
        name="h",
        selinux_logins={
            "deploy": SeLogin(seuser="unconfined_u", mls_range="s0", service="*")
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.login(ctx, "deploy", seuser="staff_u")]
    assert ctx.changed
    assert ctx.reason == "login 'deploy' will map to 'staff_u' with range 's0'"
    assert commands == [
        SemanageLoginCommand(
            login="deploy", seuser="staff_u", mls_range="s0", modify=True
        )
    ]


async def test_login_modifies_a_differing_range():
    """The range alone differing also yields ``-m`` -- both fields are
    compared, the Service column is not."""
    host = SimulatedHost(
        name="h",
        selinux_logins={
            "deploy": SeLogin(seuser="staff_u", mls_range="s0", service="other")
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.login(
            ctx, "deploy", seuser="staff_u", mls_range="s0-s0:c0.c1023"
        )
    ]
    assert ctx.changed
    assert commands[0].shell() == (
        "semanage login -m -s staff_u -r s0-s0:c0.c1023 deploy"
    )


async def test_login_noop_ignores_the_service_column():
    """A matching seuser/range is a no-op even when the fact's Service
    column is scoped -- the operation does not manage that refinement."""
    host = SimulatedHost(
        name="h",
        selinux_logins={
            "deploy": SeLogin(seuser="staff_u", mls_range="s0", service="sshd")
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.login(ctx, "deploy", seuser="staff_u")]
    assert not ctx.changed
    assert ctx.reason == "login 'deploy' already maps to 'staff_u' with range 's0'"
    assert commands == []


async def test_login_removes_a_mapping():
    """``present=False`` on a mapped login yields ``-d`` (no user needed)."""
    host = SimulatedHost(
        name="h",
        selinux_logins={
            "deploy": SeLogin(seuser="staff_u", mls_range="s0", service="*")
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.login(ctx, "deploy", present=False)]
    assert ctx.changed
    assert ctx.reason == "login 'deploy' mapped, will remove"
    assert commands == [SemanageLoginCommand(login="deploy", present=False)]


async def test_login_remove_noop():
    """Removing an unmapped login is a no-op."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.login(ctx, "deploy", present=False)]
    assert not ctx.changed
    assert ctx.reason == "no existing login mapping for 'deploy'"
    assert commands == []


async def test_login_requires_a_seuser_when_present():
    """``present=True`` without a seuser is a ValueError."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.login(ctx, "deploy")]
    assert str(excinfo.value) == "seuser must have a valid value if present is set"


# --- selinux.module: the four-state machine ------------------------------------------


async def test_module_installs_an_absent_module():
    """``present`` on an uninstalled name installs from the host-side
    package at the requested priority."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.module(
            ctx, "myhttpd", source="/root/myhttpd.pp", priority=500
        )
    ]
    assert ctx.changed
    assert ctx.reason == "module 'myhttpd' absent, will install"
    assert commands == [
        SemoduleInstallCommand(source="/root/myhttpd.pp", priority=500)
    ]
    assert commands[0].shell() == "semodule -X 500 -i /root/myhttpd.pp"


async def test_module_install_requires_a_source():
    """Installing an absent module without ``source`` is a ValueError --
    the package must already be on the host."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.module(ctx, "myhttpd")]
    assert str(excinfo.value) == "source must be given to install module 'myhttpd'"


async def test_module_present_enables_a_disabled_module():
    """``present`` on an installed-but-disabled name enables it rather
    than reinstalling."""
    host = SimulatedHost(
        name="h",
        selinux_modules=[
            Module(name="myhttpd", priority=400, format="pp", enabled=False)
        ],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.module(ctx, "myhttpd")]
    assert ctx.changed
    assert ctx.reason == "module 'myhttpd' disabled, will enable"
    assert commands == [SemoduleEnableCommand(name="myhttpd", present=True)]


async def test_module_present_noop_never_upgrades():
    """An installed, enabled module is left alone even with a ``source``
    given -- the fact carries no version to compare, so ``present`` never
    reinstalls."""
    host = SimulatedHost(
        name="h",
        selinux_modules=[
            Module(name="myhttpd", priority=400, format="pp", enabled=True)
        ],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.module(ctx, "myhttpd", source="/root/myhttpd.pp")
    ]
    assert not ctx.changed
    assert ctx.reason == "module 'myhttpd' already present and enabled"
    assert commands == []


async def test_module_absent_removes_every_instance_in_priority_order():
    """``absent`` yields one priority-scoped removal per stored instance,
    ascending, so both phases plan the same sequence."""
    host = SimulatedHost(
        name="h",
        selinux_modules=[
            Module(name="myhttpd", priority=400, format="pp", enabled=False),
            Module(name="myhttpd", priority=200, format="pp", enabled=True),
            Module(name="base", priority=400, format="pp", enabled=True),
        ],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.module(ctx, "myhttpd", state="absent")]
    assert ctx.changed
    assert ctx.reason == "module 'myhttpd' present, will remove"
    assert commands == [
        SemoduleRemoveCommand(name="myhttpd", priority=200),
        SemoduleRemoveCommand(name="myhttpd", priority=400),
    ]


async def test_module_absent_noop():
    """Removing an uninstalled module is a no-op."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.module(ctx, "myhttpd", state="absent")]
    assert not ctx.changed
    assert ctx.reason == "module 'myhttpd' already absent"
    assert commands == []


async def test_module_disable_and_enable():
    """The ``disabled``/``enabled`` states flip an installed module with
    one name-keyed command each."""
    host = SimulatedHost(
        name="h",
        selinux_modules=[
            Module(name="myhttpd", priority=400, format="pp", enabled=True)
        ],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.module(ctx, "myhttpd", state="disabled")]
    assert ctx.changed
    assert ctx.reason == "module 'myhttpd' will be disabled"
    assert commands == [SemoduleEnableCommand(name="myhttpd", present=False)]
    assert commands[0].shell() == "semodule -d myhttpd"
    host.selinux_modules[0]["enabled"] = False
    ctx2 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands2 = [c async for c in selinux.module(ctx2, "myhttpd", state="enabled")]
    assert ctx2.changed
    assert ctx2.reason == "module 'myhttpd' will be enabled"
    assert commands2 == [SemoduleEnableCommand(name="myhttpd", present=True)]


async def test_module_enabled_and_disabled_noops():
    """A module already in the requested enabled state is left alone."""
    host = SimulatedHost(
        name="h",
        selinux_modules=[
            Module(name="myhttpd", priority=400, format="pp", enabled=True)
        ],
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.module(ctx, "myhttpd", state="enabled")]
    assert not ctx.changed
    assert ctx.reason == "module 'myhttpd' already enabled"
    assert commands == []
    host.selinux_modules[0]["enabled"] = False
    ctx2 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands2 = [c async for c in selinux.module(ctx2, "myhttpd", state="disabled")]
    assert not ctx2.changed
    assert ctx2.reason == "module 'myhttpd' already disabled"
    assert commands2 == []


async def test_module_enable_of_absent_raises():
    """Enabling or disabling nothing is a request error, not a silent
    install."""
    host = SimulatedHost(name="h")
    for state in ("enabled", "disabled"):
        ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
        with pytest.raises(ValueError) as excinfo:
            [c async for c in selinux.module(ctx, "myhttpd", state=state)]
        assert str(excinfo.value) == (
            f"module 'myhttpd' is not installed; cannot set it {state}"
        )


async def test_module_invalid_state():
    """A state outside the four is a ValueError."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.module(ctx, "myhttpd", state="loaded")]
    assert str(excinfo.value) == (
        "Invalid state 'loaded' -- must be present, absent, enabled or disabled"
    )


# --- selinux.permissive_domain --------------------------------------------------------


async def test_permissive_domain_adds_an_enforcing_domain():
    """A domain not on the permissive list yields ``-a``."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.permissive_domain(ctx, "cupsd_t")]
    assert ctx.changed
    assert ctx.reason == "domain 'cupsd_t' enforcing, will make permissive"
    assert commands == [SemanagePermissiveCommand(domain="cupsd_t", present=True)]
    assert commands[0].shell() == "semanage permissive -a cupsd_t"


async def test_permissive_domain_removes_an_exempted_domain():
    """A listed domain with ``present=False`` yields ``-d`` -- built-in
    permissive types are on the same list."""
    host = SimulatedHost(name="h", selinux_permissive_domains=["cupsd_t"])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.permissive_domain(ctx, "cupsd_t", present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "domain 'cupsd_t' permissive, will enforce"
    assert commands == [SemanagePermissiveCommand(domain="cupsd_t", present=False)]


async def test_permissive_domain_both_noops():
    """Adding an already-permissive domain and removing an enforcing one
    are both no-ops."""
    host = SimulatedHost(name="h", selinux_permissive_domains=["cupsd_t"])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.permissive_domain(ctx, "cupsd_t")]
    assert not ctx.changed
    assert ctx.reason == "domain 'cupsd_t' is already permissive"
    assert commands == []
    ctx2 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands2 = [
        c async for c in selinux.permissive_domain(ctx2, "httpd_t", present=False)
    ]
    assert not ctx2.changed
    assert ctx2.reason == "domain 'httpd_t' is already enforcing"
    assert commands2 == []


# --- selinux.restorecon / selinux.autorelabel ------------------------------------------


async def test_restorecon_is_stateless():
    """The chosen design: restorecon always reports a change and always
    yields, even on an already-correct tree (the ``apt.key``
    non-idempotence precedent)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.restorecon(ctx, "/web")]
    assert ctx.changed
    assert ctx.reason == "restorecon will relabel '/web'"
    assert commands == [RestoreconCommand(path="/web")]
    # A second prepare plans the same relabel again.
    ctx2 = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands2 = [c async for c in selinux.restorecon(ctx2, "/web")]
    assert ctx2.changed
    assert commands2 == commands


async def test_restorecon_passes_the_flags_through():
    """``recurse`` and ``force`` reach the descriptor untouched."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in selinux.restorecon(ctx, "/web", recurse=False, force=True)
    ]
    assert commands == [RestoreconCommand(path="/web", recurse=False, force=True)]
    assert commands[0].shell() == "restorecon -F /web"


async def test_autorelabel_creates_the_boot_flag():
    """No flag exists: one ``touch /.autorelabel`` is planned."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.autorelabel(ctx)]
    assert ctx.changed
    assert ctx.reason == "will create /.autorelabel to relabel at next boot"
    assert commands == [AutorelabelCommand()]
    assert commands[0].shell() == "touch /.autorelabel"


async def test_autorelabel_noop_when_pending():
    """An existing flag means a relabel is already pending -- the probe
    reads the shared ``existing_files`` seam."""
    host = SimulatedHost(name="h", existing_files={AUTORELABEL_PATH})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.autorelabel(ctx)]
    assert not ctx.changed
    assert ctx.reason == "a full relabel is already pending (/.autorelabel exists)"
    assert commands == []# --- SimulatedHost: descriptor dispatch ----------------------------------------------


async def test_setsebool_seam_stores_the_bool():
    """The setsebool arm edits ``selinux_booleans``; ``persistent`` carries
    no separate simulated policy store."""
    host = SimulatedHost(name="h")
    await send_command(
        host, SetseboolCommand(boolean="some_boolean", value="on", persistent=True)
    )
    assert host.selinux_booleans == {"some_boolean": True}
    await send_command(host, SetseboolCommand(boolean="some_boolean", value="off"))
    assert host.selinux_booleans == {"some_boolean": False}


async def test_chcon_seam_seeds_and_edits_named_components():
    """An unlabelled path gains the stock context first; only the carried
    components change."""
    host = SimulatedHost(name="h")
    await send_command(host, ChconCommand(path="/web", se_type="httpd_sys_content_t"))
    assert host.selinux_file_contexts == {
        "/web": SeContext(
            user="system_u", role="object_r", type="httpd_sys_content_t", level="s0"
        )
    }
    await send_command(host, ChconCommand(path="/web", se_level="s0:c1"))
    assert host.selinux_file_contexts["/web"] == SeContext(
        user="system_u", role="object_r", type="httpd_sys_content_t", level="s0:c1"
    )


async def test_semanage_fcontext_seam_add_modify_delete():
    """Add seeds the stock context, modify changes the type only, delete
    drops the rule -- and existing file labels stay untouched throughout
    (the database-only write)."""
    host = SimulatedHost(
        name="h",
        selinux_file_contexts={"/web/index.html": dict(NET_CONF_CONTEXT)},
    )
    await send_command(
        host, SemanageFcontextCommand(target=WEB_TARGET, se_type="httpd_sys_content_t")
    )
    assert host.selinux_fcontext_mappings == {
        WEB_TARGET: _mapping_context("httpd_sys_content_t")
    }
    assert host.selinux_file_contexts["/web/index.html"] == NET_CONF_CONTEXT
    await send_command(
        host,
        SemanageFcontextCommand(target=WEB_TARGET, se_type="var_t", modify=True),
    )
    assert host.selinux_fcontext_mappings[WEB_TARGET]["type"] == "var_t"
    await send_command(
        host, SemanageFcontextCommand(target=WEB_TARGET, present=False)
    )
    assert host.selinux_fcontext_mappings == {}


async def test_semanage_port_seam_prunes_an_empty_protocol():
    """Add/modify store the pair; delete drops it and prunes a protocol
    left empty, so ``SEPorts`` never reports an empty dict."""
    host = SimulatedHost(name="h")
    await send_command(
        host, SemanagePortCommand(protocol="tcp", port=8080, se_type="http_port_t")
    )
    assert host.selinux_ports == {"tcp": {8080: "http_port_t"}}
    await send_command(
        host,
        SemanagePortCommand(
            protocol="tcp", port=8080, se_type="var_t", modify=True
        ),
    )
    assert host.selinux_ports == {"tcp": {8080: "var_t"}}
    await send_command(
        host, SemanagePortCommand(protocol="tcp", port=8080, present=False)
    )
    assert host.selinux_ports == {}


async def test_setenforce_seam_touches_the_runtime_mode_only():
    """The config-file mode and policy survive a runtime switch, exactly
    like the real setenforce."""
    host = SimulatedHost(name="h")
    await send_command(host, SetenforceCommand(mode="permissive"))
    assert host.selinux_mode == ModeInfo(
        mode="permissive", config_mode="enforcing", policy="targeted"
    )


async def test_semanage_login_seam_add_and_delete():
    """Add/modify store the universal ``*`` service; delete drops the
    login."""
    host = SimulatedHost(name="h")
    await send_command(
        host, SemanageLoginCommand(login="deploy", seuser="staff_u", mls_range="s0")
    )
    assert host.selinux_logins == {
        "deploy": SeLogin(seuser="staff_u", mls_range="s0", service="*")
    }
    await send_command(host, SemanageLoginCommand(login="deploy", present=False))
    assert host.selinux_logins == {}


async def test_semodule_seams_install_remove_and_enable():
    """Install derives the name from the package basename (``.cil`` format
    included), removal is priority-scoped, enable/disable flips every
    instance of the name."""
    host = SimulatedHost(name="h")
    await send_command(host, SemoduleInstallCommand(source="/root/myhttpd.pp"))
    await send_command(
        host, SemoduleInstallCommand(source="/root/zebra.cil", priority=200)
    )
    assert host.selinux_modules == [
        Module(name="myhttpd", priority=400, format="pp", enabled=True),
        Module(name="zebra", priority=200, format="cil", enabled=True),
    ]
    # A reinstall at the same name/priority replaces, never duplicates
    # (the replacement is appended, so the listing order changes).
    await send_command(host, SemoduleInstallCommand(source="/root/myhttpd.pp"))
    assert len(host.selinux_modules) == 2
    await send_command(host, SemoduleEnableCommand(name="myhttpd", present=False))
    myhttpd = next(e for e in host.selinux_modules if e["name"] == "myhttpd")
    assert myhttpd["enabled"] is False
    await send_command(host, SemoduleRemoveCommand(name="myhttpd", priority=200))
    assert len(host.selinux_modules) == 2  # no instance at that priority
    await send_command(host, SemoduleRemoveCommand(name="myhttpd", priority=400))
    assert host.selinux_modules == [
        Module(name="zebra", priority=200, format="cil", enabled=True)
    ]


async def test_semanage_permissive_seam_add_dedupes_and_delete():
    """Add appends without duplicates, delete removes."""
    host = SimulatedHost(name="h", selinux_permissive_domains=["cupsd_t"])
    await send_command(host, SemanagePermissiveCommand(domain="cupsd_t"))
    await send_command(host, SemanagePermissiveCommand(domain="my_daemon_t"))
    assert host.selinux_permissive_domains == ["cupsd_t", "my_daemon_t"]
    await send_command(
        host, SemanagePermissiveCommand(domain="cupsd_t", present=False)
    )
    assert host.selinux_permissive_domains == ["my_daemon_t"]


async def test_restorecon_seam_reapplies_the_database_to_the_tree():
    """The relabel arm matches covered paths against the fcontext rules
    with ``re.fullmatch``; an uncovered or unmatched path keeps its
    label."""
    host = SimulatedHost(
        name="h",
        selinux_file_contexts={
            "/web": dict(NET_CONF_CONTEXT),
            "/web/index.html": dict(NET_CONF_CONTEXT),
            "/other": dict(NET_CONF_CONTEXT),
        },
        selinux_fcontext_mappings={
            WEB_TARGET: _mapping_context("httpd_sys_content_t")
        },
    )
    await send_command(host, RestoreconCommand(path="/web"))
    relabelled = _mapping_context("httpd_sys_content_t")
    assert host.selinux_file_contexts == {
        "/web": relabelled,
        "/web/index.html": relabelled,
        "/other": NET_CONF_CONTEXT,
    }


async def test_restorecon_seam_without_recurse_covers_the_path_only():
    """``recurse=False`` relabels the exact path, not the tree under it."""
    host = SimulatedHost(
        name="h",
        selinux_file_contexts={
            "/web": dict(NET_CONF_CONTEXT),
            "/web/index.html": dict(NET_CONF_CONTEXT),
        },
        selinux_fcontext_mappings={
            WEB_TARGET: _mapping_context("httpd_sys_content_t")
        },
    )
    await send_command(host, RestoreconCommand(path="/web", recurse=False))
    assert host.selinux_file_contexts["/web"]["type"] == "httpd_sys_content_t"
    assert host.selinux_file_contexts["/web/index.html"] == NET_CONF_CONTEXT


async def test_autorelabel_seam_creates_the_flag():
    """The touch arm records the flag in ``existing_files``, which the
    ``_AutorelabelPending`` probe reads back."""
    host = SimulatedHost(name="h")
    await send_command(host, AutorelabelCommand())
    assert AUTORELABEL_PATH in host.existing_files
    assert await _AutorelabelPending.process(host) is True


# --- End to end: run_prepare + run_apply ----------------------------------------------


async def test_boolean_end_to_end_change_then_noop():
    """Through the driver: the setsebool executes under sudo and renders
    in the log; a second prepare sees the new value and plans no change."""

    def boolean_deploy(host):
        yield op(
            selinux.boolean,
            name="Allow httpd network connect",
            bool_name="httpd_can_network_connect",
            value="on",
            persistent=True,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], boolean_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        SetseboolCommand(
            boolean="httpd_can_network_connect", value="on", persistent=True
        ),
    )
    assert "$ sudo setsebool -P httpd_can_network_connect on" in format_log(report)
    assert host.selinux_booleans == {"httpd_can_network_connect": True}
    _runtimes, plans2 = await run_prepare([host], boolean_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == (
        "boolean 'httpd_can_network_connect' already had the value 'on'"
    )


async def test_mode_end_to_end_switch_then_noop():
    """The runtime switch lands in the seam (config mode untouched) and a
    second prepare reads the host already permissive."""

    def permissive_deploy(host):
        yield op(selinux.mode, name="Troubleshoot permissive", mode="permissive", sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], permissive_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (SetenforceCommand(mode="permissive"),)
    assert "$ sudo setenforce 0" in format_log(report)
    assert host.selinux_mode == ModeInfo(
        mode="permissive", config_mode="enforcing", policy="targeted"
    )
    _runtimes, plans2 = await run_prepare([host], permissive_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "SELinux already in permissive mode"


async def test_port_end_to_end_add_then_noop():
    """The indirect (no sepolicy) dispatch through the driver: the label
    lands in the seam and a second prepare sees it set."""

    def port_deploy(host):
        yield op(
            selinux.port,
            name="Label 8080 as http",
            protocol="tcp",
            port_num=8080,
            se_type="http_port_t",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], port_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (
        SemanagePortCommand(protocol="tcp", port=8080, se_type="http_port_t"),
    )
    assert "$ sudo semanage port -a -t http_port_t -p tcp 8080" in format_log(report)
    assert host.selinux_ports == {"tcp": {8080: "http_port_t"}}
    _runtimes, plans2 = await run_prepare([host], port_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "setype for 'tcp/8080' is already 'http_port_t'"


async def test_login_end_to_end_map_then_noop():
    """The confined-user mapping lands in the seam and a second prepare
    reads it already mapped."""

    def login_deploy(host):
        yield op(
            selinux.login,
            name="Confine deploy",
            user="deploy",
            seuser="staff_u",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], login_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (
        SemanageLoginCommand(login="deploy", seuser="staff_u", mls_range="s0"),
    )
    assert "$ sudo semanage login -a -s staff_u -r s0 deploy" in format_log(report)
    assert host.selinux_logins == {
        "deploy": SeLogin(seuser="staff_u", mls_range="s0", service="*")
    }
    _runtimes, plans2 = await run_prepare([host], login_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == (
        "login 'deploy' already maps to 'staff_u' with range 's0'"
    )


async def test_module_end_to_end_install_then_disable_then_noop():
    """The module lifecycle through the driver: install lands enabled, a
    ``disabled`` request flips it, and a third prepare no-ops."""

    def install_deploy(host):
        yield op(
            selinux.module,
            name="Install myhttpd",
            module_name="myhttpd",
            source="/root/myhttpd.pp",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], install_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (
        SemoduleInstallCommand(source="/root/myhttpd.pp", priority=400),
    )
    assert "$ sudo semodule -X 400 -i /root/myhttpd.pp" in format_log(report)
    assert host.selinux_modules == [
        Module(name="myhttpd", priority=400, format="pp", enabled=True)
    ]
    _runtimes, plans2 = await run_prepare([host], install_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "module 'myhttpd' already present and enabled"

    def disable_deploy(host):
        yield op(
            selinux.module,
            name="Disable myhttpd",
            module_name="myhttpd",
            state="disabled",
            sudo=True,
        )

    runtimes, plans = await run_prepare([host], disable_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (SemoduleEnableCommand(name="myhttpd", present=False),)
    assert host.selinux_modules[0]["enabled"] is False
    _runtimes, plans2 = await run_prepare([host], disable_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "module 'myhttpd' already disabled"


async def test_mapping_then_restorecon_end_to_end_composition():
    """The documented pairing: the mapping writes the database, restorecon
    applies it to the tree, and the relabel is observable through
    ``FileContext``. A second prepare no-ops the mapping but restorecon
    stays changed -- its deliberate statelessness."""

    def web_deploy(host):
        yield op(
            selinux.file_context_mapping,
            name="Label /web persistently",
            target=WEB_TARGET,
            se_type="httpd_sys_content_t",
            sudo=True,
        )
        yield op(
            selinux.restorecon, name="Relabel /web", path="/web", sudo=True
        )

    host = SimulatedHost(
        name="h",
        selinux_file_contexts={"/web/index.html": dict(NET_CONF_CONTEXT)},
    )
    runtimes, plans = await run_prepare([host], web_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    assert len(report.log) == 2
    log = format_log(report)
    assert "$ sudo semanage fcontext -a -t httpd_sys_content_t '/web(/.*)?'" in log
    assert "$ sudo restorecon -R /web" in log
    assert host.selinux_file_contexts["/web/index.html"] == _mapping_context(
        "httpd_sys_content_t"
    )
    _runtimes, plans2 = await run_prepare([host], web_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == (
        "mapping for '/web(/.*)?' -> 'httpd_sys_content_t' already present"
    )
    assert plans2["h"][1].changed
    assert plans2["h"][1].reason == "restorecon will relabel '/web'"


async def test_permissive_domain_end_to_end_add_then_noop():
    """The exemption lands in the seam and a second prepare reads the
    domain already permissive."""

    def permissive_deploy(host):
        yield op(
            selinux.permissive_domain,
            name="Exempt cupsd",
            domain="cupsd_t",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], permissive_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (SemanagePermissiveCommand(domain="cupsd_t", present=True),)
    assert "$ sudo semanage permissive -a cupsd_t" in format_log(report)
    assert host.selinux_permissive_domains == ["cupsd_t"]
    _runtimes, plans2 = await run_prepare([host], permissive_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "domain 'cupsd_t' is already permissive"


async def test_autorelabel_end_to_end_flag_then_noop():
    """The boot flag lands in ``existing_files`` and a second prepare sees
    the relabel already pending."""

    def relabel_deploy(host):
        yield op(selinux.autorelabel, name="Relabel at next boot", sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], relabel_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.commands == (AutorelabelCommand(),)
    assert "$ sudo touch /.autorelabel" in format_log(report)
    assert AUTORELABEL_PATH in host.existing_files
    _runtimes, plans2 = await run_prepare([host], relabel_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == (
        "a full relabel is already pending (/.autorelabel exists)"
    )


async def test_boolean_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the change is planned but nothing
    runs, so the boolean store stays untouched."""

    def boolean_deploy(host):
        yield op(
            selinux.boolean,
            name="Allow httpd network connect",
            bool_name="httpd_can_network_connect",
            value="on",
            persistent=True,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        boolean_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (
        SetseboolCommand(
            boolean="httpd_can_network_connect", value="on", persistent=True
        ),
    )
    assert host.selinux_booleans == {}


# --- SelinuxConfigFile: the raw-content fact ------------------------------------------

# A stock-shaped /etc/selinux/config: the two active assignments plus the
# comment headers the distro ships (which the key pattern also matches --
# the recipe's replacement semantics clean them out with the assignments).
SELINUX_CONFIG_TEXT = """\
# This file controls the state of SELinux on the system.
# SELINUX= can take one of these three values:
SELINUX=enforcing
# SELINUXTYPE= can take one of these three values:
SELINUXTYPE=targeted
"""

CONFIG_PATH = "/etc/selinux/config"


def test_selinux_config_file_command_guards_and_quotes():
    """The ``test -e`` + marker + ``cat`` form, with the path shell-quoted
    (the ``OpenrcConfigFile.command`` pattern)."""
    assert SelinuxConfigFile.command(CONFIG_PATH) == (
        "test -e /etc/selinux/config && { printf '#reemote5-selinuxconfig\\n'; "
        "cat -- /etc/selinux/config; }"
    )
    assert SelinuxConfigFile.command("/etc/my config") == (
        "test -e '/etc/my config' && { printf '#reemote5-selinuxconfig\\n'; "
        "cat -- '/etc/my config'; }"
    )


async def test_selinux_config_file_seam_absent_reads_none():
    """The simulated seam reports an absent file as None, never \"\"."""
    host = SimulatedHost(name="h")
    assert await SelinuxConfigFile.process(host, CONFIG_PATH) is None


async def test_selinux_config_file_seam_empty_reads_empty_string():
    """An existing but empty file reads \"\" -- the marker distinction that
    keeps absence and emptiness apart."""
    host = SimulatedHost(name="h", remote_files={CONFIG_PATH: b""})
    assert await SelinuxConfigFile.process(host, CONFIG_PATH) == ""


async def test_selinux_config_file_seam_decodes_content():
    """The seam decodes the stored bytes verbatim -- comments, blank lines
    and ordering survive, because the fact never parses the text."""
    host = SimulatedHost(
        name="h", remote_files={CONFIG_PATH: SELINUX_CONFIG_TEXT.encode()}
    )
    assert await SelinuxConfigFile.process(host, CONFIG_PATH) == SELINUX_CONFIG_TEXT


async def test_selinux_config_file_canned_parses_marker_and_content():
    """Over the real-transport path the marker line is stripped and the
    rest returned verbatim (the seam short-circuit never exercises this)."""
    host = CannedShellHost(f"#reemote5-selinuxconfig\n{SELINUX_CONFIG_TEXT}")
    assert await SelinuxConfigFile.process(host, CONFIG_PATH) == SELINUX_CONFIG_TEXT
    assert host.last_shell == SelinuxConfigFile.command(CONFIG_PATH)


async def test_selinux_config_file_canned_absent_reads_none():
    """No marker on stdout (the ``test -e`` guard failed) reads as None."""
    assert await SelinuxConfigFile.process(CannedShellHost(""), CONFIG_PATH) is None


async def test_selinux_config_file_canned_empty_reads_empty_string():
    """The marker alone -- an existing empty file -- reads as \"\"."""
    host = CannedShellHost("#reemote5-selinuxconfig\n")
    assert await SelinuxConfigFile.process(host, CONFIG_PATH) == ""


async def test_selinux_config_file_gather_fact_forwards_the_path():
    """``gather_fact`` forwards the path argument to every host's read."""
    host1 = SimulatedHost(
        name="web1", remote_files={CONFIG_PATH: b"SELINUX=enforcing\n"}
    )
    host2 = SimulatedHost(name="web2")
    contents = await gather_fact([host1, host2], SelinuxConfigFile, CONFIG_PATH)
    assert contents == {"web1": "SELINUX=enforcing\n", "web2": None}


# --- selinux.config: the pure transform ------------------------------------------------


def test_set_config_value_absent_file_becomes_the_single_line():
    """None content gains just the unquoted assignment and a trailing newline."""
    assert _set_config_value(None, "SELINUX", "enforcing") == "SELINUX=enforcing\n"
    assert _set_config_value("", "SELINUX", "enforcing") == "SELINUX=enforcing\n"


def test_set_config_value_replaces_active_and_commented_assignments():
    """Every line the key pattern matches is dropped -- the active
    assignment and the commented ``# SELINUX=`` header alike, the recipe's
    replacement semantics -- and the wanted line appended; unrelated lines
    keep their order and text."""
    assert _set_config_value(SELINUX_CONFIG_TEXT, "SELINUX", "permissive") == (
        "# This file controls the state of SELinux on the system.\n"
        "# SELINUXTYPE= can take one of these three values:\n"
        "SELINUXTYPE=targeted\n"
        "SELINUX=permissive\n"
    )


def test_set_config_value_matches_whitespace_variants():
    """Leading whitespace, spaces around the ``=`` and a spaced comment
    marker all match the drop pattern."""
    content = "  SELINUX = enforcing\n#  SELINUX=disabled\nSELINUXTYPE=targeted\n"
    assert _set_config_value(content, "SELINUX", "permissive") == (
        "SELINUXTYPE=targeted\nSELINUX=permissive\n"
    )


def test_set_config_value_is_a_fixpoint():
    """The appended line matches the drop pattern, so a second pass
    reproduces the same content -- both phases derive the same bytes."""
    once = _set_config_value(SELINUX_CONFIG_TEXT, "SELINUX", "permissive")
    assert _set_config_value(once, "SELINUX", "permissive") == once


def test_set_config_value_keeps_a_matching_sole_assignment_byte_for_byte():
    """When the wanted line is the key's only assignment the content is
    returned unchanged -- position and trailing-newline state preserved,
    so a stock config whose value already matches is never rewritten."""
    content = "SELINUX=enforcing\nSELINUXTYPE=mls"
    assert _set_config_value(content, "SELINUX", "enforcing") == content
    # A commented sibling still counts as a second assignment: cleanup wins.
    content = "# SELINUX=disabled\nSELINUX=enforcing\n"
    assert _set_config_value(content, "SELINUX", "enforcing") == "SELINUX=enforcing\n"


def test_set_config_value_does_not_match_the_sibling_key():
    """The pattern is key-anchored: ``SELINUX`` never drops a
    ``SELINUXTYPE`` assignment or header."""
    content = "SELINUXTYPE=targeted\n# SELINUXTYPE= can take values\n"
    assert _set_config_value(content, "SELINUX", "enforcing") == (
        "SELINUXTYPE=targeted\n# SELINUXTYPE= can take values\nSELINUX=enforcing\n"
    )


# --- selinux.config: spool derivation and descriptors ----------------------------------


def test_config_spool_path_is_dest_derived_and_deterministic():
    """The same dest maps to the same spool in both phases, different dests
    never share one, and the name keeps the family prefix."""
    spool = _config_spool_path(CONFIG_PATH)
    assert spool == _config_spool_path(CONFIG_PATH)
    assert spool != _config_spool_path("/etc/selinux/other")
    assert spool.startswith("/tmp/reemote5-selinux-config-")
    assert len(spool.rsplit("-", 1)[1]) == 16


def test_selinux_config_upload_str_and_never_elevates():
    """The transfer has no ``shell()``; its loggable form is the ``__str__``
    rendering, and ``command_shell`` never prefixes it with an escalator."""
    spool = _config_spool_path(CONFIG_PATH)
    cmd = SelinuxConfigUploadCommand(content="SELINUX=enforcing\n", dest=spool)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <selinux config> -> {spool}"
    assert command_shell(cmd, sudo=True) == str(cmd)
    assert command_shell(cmd, su=True) == str(cmd)


def test_selinux_config_install_shell_and_elevation():
    """The install wraps the whole ``&&`` chain in ``sh -c`` so the
    escalator elevates every step, cleanup included."""
    spool = _config_spool_path(CONFIG_PATH)
    cmd = SelinuxConfigInstallCommand(spool=spool, dest=CONFIG_PATH)
    inner = (
        f"mkdir -p /etc/selinux && cp {spool} {CONFIG_PATH} && "
        f"chmod 0644 {CONFIG_PATH} && rm -f {spool}"
    )
    assert cmd.shell() == f"sh -c {shlex.quote(inner)}"
    assert command_shell(cmd, sudo=True) == f"sudo sh -c {shlex.quote(inner)}"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote(cmd.shell())}"


def test_selinux_config_install_quotes_paths_with_spaces():
    """A space-carrying directory proves the quoting (a space-free path is
    left unquoted by shlex.quote); assert through shlex.split, never a raw
    substring of the quoted wrapper."""
    cmd = SelinuxConfigInstallCommand(
        spool="/tmp/spool x", dest="/etc/seli nux/wei rd"
    )
    inner = shlex.split(cmd.shell())[2]
    assert "mkdir -p '/etc/seli nux'" in inner
    assert "cp '/tmp/spool x' '/etc/seli nux/wei rd'" in inner
    assert "chmod 0644 '/etc/seli nux/wei rd'" in inner
    assert "rm -f '/tmp/spool x'" in inner


# --- selinux.config: the operation ------------------------------------------------------


async def test_config_requires_at_least_one_setting():
    """Neither SELINUX nor SELINUXTYPE given is a ValueError before any
    fact read."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.config(ctx)]
    assert str(excinfo.value) == (
        "at least one of SELINUX and SELINUXTYPE must be given"
    )


async def test_config_rejects_an_invalid_boot_mode():
    """SELINUX is an enum: a bogus mode would silently disable SELinux at
    boot, so it fails in prepare."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.config(ctx, SELINUX="enabled")]
    assert str(excinfo.value) == "not an SELinux boot mode: 'enabled'"


@pytest.mark.parametrize(
    "value", ["", "two words", "has#hash", 'quo"ted', "sin'gle", "back\\slash", "new\nline"]
)
async def test_config_rejects_a_corrupting_policy_name(value):
    """SELINUXTYPE is free-form (custom policies exist) but never carries
    characters that would corrupt the line libselinux parses."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError) as excinfo:
        [c async for c in selinux.config(ctx, SELINUXTYPE=value)]
    assert str(excinfo.value) == f"not a policy type name: {value!r}"


async def test_config_accepts_a_custom_policy_name():
    """A custom policy package name passes the charset check -- there is
    no targeted/minimum/mls whitelist."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.config(ctx, SELINUXTYPE="custom-policy")]
    assert ctx.changed
    assert commands[0] == SelinuxConfigUploadCommand(
        content="SELINUXTYPE=custom-policy\n", dest=_config_spool_path(CONFIG_PATH)
    )


async def test_config_sets_both_settings_on_an_absent_file():
    """An absent file reads None and gains exactly the requested lines, in
    fixed SELINUX-then-SELINUXTYPE order; the pair is upload then install."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in selinux.config(
            ctx, SELINUX="enforcing", SELINUXTYPE="targeted"
        )
    ]
    assert ctx.changed
    assert ctx.reason == (
        "SELINUX=enforcing, SELINUXTYPE=targeted will be set in /etc/selinux/config"
    )
    spool = _config_spool_path(CONFIG_PATH)
    assert commands == [
        SelinuxConfigUploadCommand(
            content="SELINUX=enforcing\nSELINUXTYPE=targeted\n", dest=spool
        ),
        SelinuxConfigInstallCommand(spool=spool, dest=CONFIG_PATH),
    ]


async def test_config_leaves_the_unrequested_setting_untouched():
    """``SELINUX`` alone never rewrites the file's SELINUXTYPE assignment;
    None means untouched, not unset."""
    host = SimulatedHost(
        name="h", remote_files={CONFIG_PATH: SELINUX_CONFIG_TEXT.encode()}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.config(ctx, SELINUX="permissive")]
    assert ctx.changed
    assert ctx.reason == "SELINUX=permissive will be set in /etc/selinux/config"
    assert commands[0].content == (
        "# This file controls the state of SELinux on the system.\n"
        "# SELINUXTYPE= can take one of these three values:\n"
        "SELINUXTYPE=targeted\n"
        "SELINUX=permissive\n"
    )


async def test_config_noop_when_the_transform_is_a_fixpoint():
    """The content-level no-op: the file already holds exactly the wanted
    assignments, so nothing is spooled even though both keys are given."""
    host = SimulatedHost(
        name="h",
        remote_files={
            CONFIG_PATH: b"SELINUX=enforcing\nSELINUXTYPE=targeted\n"
        },
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in selinux.config(
            ctx, SELINUX="enforcing", SELINUXTYPE="targeted"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == (
        "/etc/selinux/config already sets SELINUX=enforcing, SELINUXTYPE=targeted"
    )
    assert commands == []


async def test_config_partial_noop_compares_only_the_requested_keys():
    """A SELINUX-only request no-ops on a file whose SELINUX already
    matches, whatever SELINUXTYPE holds."""
    host = SimulatedHost(
        name="h",
        remote_files={CONFIG_PATH: b"SELINUX=enforcing\nSELINUXTYPE=mls\n"},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.config(ctx, SELINUX="enforcing")]
    assert not ctx.changed
    assert ctx.reason == "/etc/selinux/config already sets SELINUX=enforcing"
    assert commands == []


async def test_config_cleans_out_commented_assignments():
    """A file holding only a commented-out assignment is a change: the
    replacement semantics drop it and append the active line."""
    host = SimulatedHost(
        name="h", remote_files={CONFIG_PATH: b"#SELINUX=permissive\n"}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in selinux.config(ctx, SELINUX="enforcing")]
    assert ctx.changed
    assert commands[0].content == "SELINUX=enforcing\n"


async def test_config_absorbs_global_kwargs():
    """The escalation flags op() normally strips are absorbed by
    ``**kwargs`` when the generator is driven directly."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in selinux.config(
            ctx, SELINUX="disabled", sudo=True, su=False, name="ignored"
        )
    ]
    assert ctx.changed
    assert commands[0].content == "SELINUX=disabled\n"


# --- selinux.config: the SimulatedHost seam ---------------------------------------------


async def test_config_upload_seam_writes_only_the_spool():
    """The upload arm stores the content at the spool path only -- the
    protected file is untouched until the install runs."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path(CONFIG_PATH)
    await send_command(
        host,
        SelinuxConfigUploadCommand(content="SELINUX=permissive\n", dest=spool),
    )
    assert host.remote_files == {spool: b"SELINUX=permissive\n"}
    assert CONFIG_PATH not in host.existing_files


async def test_config_install_seam_moves_the_spool_and_refreshes_mode():
    """The install arm pops the spool, stores the bytes at the dest with
    existence/stats, and refreshes ``Mode``'s config_mode from the installed
    SELINUX assignment -- runtime mode and loaded policy survive."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path(CONFIG_PATH)
    content = b"SELINUXTYPE=targeted\nSELINUX=permissive\n"
    host.remote_files[spool] = content
    await send_command(
        host, SelinuxConfigInstallCommand(spool=spool, dest=CONFIG_PATH)
    )
    assert spool not in host.remote_files
    assert host.remote_files[CONFIG_PATH] == content
    assert CONFIG_PATH in host.existing_files
    assert host.file_stats[CONFIG_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(content),
    }
    assert host.selinux_mode == ModeInfo(
        mode="enforcing", config_mode="permissive", policy="targeted"
    )


async def test_config_install_seam_tolerates_an_absent_spool():
    """An install without its upload changes nothing (the
    ``install_openrc_config_file`` tolerance)."""
    host = SimulatedHost(name="h")
    await send_command(
        host,
        SelinuxConfigInstallCommand(
            spool=_config_spool_path(CONFIG_PATH), dest=CONFIG_PATH
        ),
    )
    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.selinux_mode["config_mode"] == "enforcing"


async def test_config_install_seam_repeat_does_not_duplicate():
    """The refresh is content-based: installing the same spooled text again
    replaces the file verbatim, never appends a second assignment."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path(CONFIG_PATH)
    content = b"SELINUX=permissive\n"
    for _ in range(2):
        host.remote_files[spool] = content
        await send_command(
            host, SelinuxConfigInstallCommand(spool=spool, dest=CONFIG_PATH)
        )
    assert host.remote_files[CONFIG_PATH] == content


async def test_config_install_seam_disabled_host_keeps_empty_config_mode():
    """A disabled subsystem's sestatus reports no config mode, so the
    install leaves the empty config_mode alone."""
    host = SimulatedHost(
        name="h",
        selinux_mode=ModeInfo(mode="disabled", config_mode="", policy=""),
    )
    spool = _config_spool_path(CONFIG_PATH)
    host.remote_files[spool] = b"SELINUX=enforcing\n"
    await send_command(
        host, SelinuxConfigInstallCommand(spool=spool, dest=CONFIG_PATH)
    )
    assert host.selinux_mode == ModeInfo(mode="disabled", config_mode="", policy="")


async def test_config_install_seam_without_selinux_line_keeps_config_mode():
    """A SELINUXTYPE-only install carries no SELINUX assignment, so the
    reported config mode survives (the loaded policy changes only at boot)."""
    host = SimulatedHost(name="h")
    spool = _config_spool_path(CONFIG_PATH)
    host.remote_files[spool] = b"SELINUXTYPE=mls\n"
    await send_command(
        host, SelinuxConfigInstallCommand(spool=spool, dest=CONFIG_PATH)
    )
    assert host.selinux_mode == ModeInfo(
        mode="enforcing", config_mode="enforcing", policy="targeted"
    )


# --- selinux.config: end to end ----------------------------------------------------------


async def test_config_end_to_end_change_then_noop():
    """Through the driver: the unprivileged scp spools the text, the
    elevated sh -c installs it, and a second prepare sees the fixpoint."""

    def config_deploy(host):
        yield op(
            selinux.config,
            name="Enforce targeted at boot",
            SELINUX="permissive",
            SELINUXTYPE="targeted",
            sudo=True,
        )

    host = SimulatedHost(
        name="h", remote_files={CONFIG_PATH: SELINUX_CONFIG_TEXT.encode()}
    )
    runtimes, plans = await run_prepare([host], config_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    spool = _config_spool_path(CONFIG_PATH)
    assert entry.commands == (
        SelinuxConfigUploadCommand(
            content=(
                "# This file controls the state of SELinux on the system.\n"
                "SELINUX=permissive\n"
                "SELINUXTYPE=targeted\n"
            ),
            dest=spool,
        ),
        SelinuxConfigInstallCommand(spool=spool, dest=CONFIG_PATH),
    )
    log = format_log(report)
    assert f"$ scp <selinux config> -> {spool}" in log
    assert "$ sudo sh -c" in log
    assert host.remote_files[CONFIG_PATH] == entry.commands[0].content.encode()
    assert host.selinux_mode["config_mode"] == "permissive"
    _runtimes, plans2 = await run_prepare([host], config_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == (
        "/etc/selinux/config already sets SELINUX=permissive, SELINUXTYPE=targeted"
    )


async def test_config_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the pair is planned but nothing
    runs, so the file and the spool stay untouched."""

    def config_deploy(host):
        yield op(
            selinux.config,
            name="Enforce at boot",
            SELINUX="enforcing",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        config_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert len(planned.commands) == 2
    assert host.remote_files == {}
    assert host.existing_files == set()


async def test_config_duplicate_requests_second_is_apply_phase_noop():
    """Two identical requests in one deploy: prepare plans both as changes
    (nothing is sent yet), apply's fresh per-step fact cache sees the first
    install and no-ops the second."""

    def config_deploy(host):
        yield op(selinux.config, name="First", SELINUX="permissive", sudo=True)
        yield op(selinux.config, name="Second", SELINUX="permissive", sudo=True)

    host = SimulatedHost(
        name="h", remote_files={CONFIG_PATH: b"SELINUX=enforcing\n"}
    )
    runtimes, plans = await run_prepare([host], config_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == "/etc/selinux/config already sets SELINUX=permissive"
    assert host.remote_files[CONFIG_PATH] == b"SELINUX=permissive\n"