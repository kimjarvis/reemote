# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the sysctl module: facts, descriptors and operations.

Covers ``reemote5.modules.sysctl.sysctl``: the private helpers
(``_normalize_value``'s pyinfra chain, ``_parse_conf`` and ``_edit_conf``'s
keyed replacement); the relocated ``Sysctl`` runtime fact (its ten tests
moved from ``tests/modules/server/test_server.py`` alongside the class
itself, pyinfra fixture parity included) and the two originals
``SysctlFile``/``SysctlFiles`` (command rendering, marker parsing,
copy-on-read seam reads and ``gather_fact`` forwarding); the five typed
descriptors (shell rendering + elevation, the non-shell transfer's
``__str__``); the ``setting`` and ``conf`` operations (idempotence against
the seams, every option branch, seam mutation and the end-to-end
plan/apply/dry/lockstep and determinism guards); and the export rewiring
(``reemote5.Sysctl`` now binds the sysctl class, ``server`` no longer
exports one).
"""

from collections.abc import Generator

import pytest

from reemote5.core import (
    FactCache,
    OpContext,
    OperationRequest,
    UnhashableArgumentError,
    op,
)
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import sysctl
from reemote5.modules.sysctl.sysctl import (
    _SPOOL_PATH,
    Sysctl,
    SysctlFile,
    SysctlFileInstallCommand,
    SysctlFileRemoveCommand,
    SysctlFiles,
    SysctlFileUploadCommand,
    SysctlReloadCommand,
    SysctlSetCommand,
    _edit_conf,
    _normalize_value,
    _parse_conf,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "connection": {"key_path": "~/.ssh/id_ed25519"}},
    {"host": "web2", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]

_CONF_PATH = "/etc/sysctl.conf"
_DROPIN_PATH = "/etc/sysctl.d/99-reemote5.conf"


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


async def materialize(host: Host, request: OperationRequest):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    return ctx, commands


# --- Helpers -------------------------------------------------------------------


def test_normalize_value_follows_pyintras_chain():
    # Single values try_int; a multi-word string splits into a list; a list
    # joins with spaces for the shell and collapses when single-item.
    assert _normalize_value(10) == ("10", 10)
    assert _normalize_value("10") == ("10", 10)
    assert _normalize_value("example") == ("example", "example")
    assert _normalize_value("1024 65535") == ("1024 65535", [1024, 65535])
    assert _normalize_value([1024, 65535]) == ("1024 65535", [1024, 65535])
    assert _normalize_value([42]) == ("42", 42)
    assert _normalize_value([1, "two"]) == ("1 two", [1, "two"])


def test_parse_conf_skips_comments_and_keeps_values_textual():
    text = (
        "# a comment\n"
        "; another comment\n"
        "\n"
        "no separator here\n"
        "vm.swappiness = 10\n"
        "net.ipv4.ip_forward=1\n"
        "  net.ipv4.ip_local_port_range = 1024 65535  \n"
        "vm.swappiness = 20\n"
    )
    # Values stay stripped strings (no int coercion); last assignment wins.
    assert _parse_conf(text) == {
        "vm.swappiness": "20",
        "net.ipv4.ip_forward": "1",
        "net.ipv4.ip_local_port_range": "1024 65535",
    }
    assert _parse_conf("") == {}


def test_edit_conf_replaces_in_place_and_collapses_duplicates():
    content = "vm.swappiness = 60\nfs.file-max = 100000\nvm.swappiness = 30\n"
    assert _edit_conf(content, "vm.swappiness", "10") == (
        "vm.swappiness = 10\nfs.file-max = 100000\n"
    )


def test_edit_conf_appends_when_absent_and_never_touches_comments():
    content = "# vm.swappiness = 99\nfs.file-max = 100000\n"
    assert _edit_conf(content, "vm.swappiness", "10") == (
        "# vm.swappiness = 99\nfs.file-max = 100000\nvm.swappiness = 10\n"
    )
    # An absent file gains the single assignment.
    assert _edit_conf(None, "vm.swappiness", "10") == "vm.swappiness = 10\n"


def test_edit_conf_removes_every_active_line_for_the_key():
    content = "vm.swappiness = 60\nfs.file-max = 100000\nvm.swappiness=30\n"
    assert _edit_conf(content, "vm.swappiness", None) == "fs.file-max = 100000\n"
    # An edit that empties the file returns "" (the file remains, empty);
    # removing from an absent file stays None.
    assert _edit_conf("vm.swappiness = 60\n", "vm.swappiness", None) == ""
    assert _edit_conf(None, "vm.swappiness", None) is None


def test_edit_conf_is_a_fixpoint_and_escapes_the_key():
    # A file without a trailing newline is rewritten once, then stable.
    once = _edit_conf("vm.swappiness=60", "vm.swappiness", "60")
    assert once == "vm.swappiness = 60\n"
    assert _edit_conf(once, "vm.swappiness", "60") == once
    # The key's dots are literal: a similar key is not matched.
    content = "vmXswappiness = 1\n"
    assert _edit_conf(content, "vm.swappiness", "10") == (
        "vmXswappiness = 1\nvm.swappiness = 10\n"
    )


# --- Sysctl (relocated from server) ---------------------------------------------


async def test_sysctl_parses_pyintras_linux_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Sysctl/sysctl_linux.json):
    # command "sysctl -a 2>/dev/null || true", int values including a negative
    # one, and a row without a separator ("nope what") skipped.
    host = CannedShellHost(
        "net.ipv4.tcp_adv_win_scale = -2\n"
        "vm.nr_hugepages_mempolicy = 0\n"
        "nope what\n"
        "vm.nr_overcommit_hugepages = 0\n"
    )
    assert await Sysctl.process(host) == {
        "net.ipv4.tcp_adv_win_scale": -2,
        "vm.nr_hugepages_mempolicy": 0,
        "vm.nr_overcommit_hugepages": 0,
    }
    assert host.shells == ["sysctl -a 2>/dev/null || true"]


async def test_sysctl_parses_pyintras_macos_fixture():
    # pyinfra's sysctl_macos.json fixture: BSD rows separate with ":".
    host = CannedShellHost(
        "security.mac.sysvmsg_enforce: 1\n"
        "security.mac.sysvsem_enforce: 1\n"
        "security.mac.sysvshm_enforce: 1\n"
    )
    assert await Sysctl.process(host) == {
        "security.mac.sysvmsg_enforce": 1,
        "security.mac.sysvsem_enforce": 1,
        "security.mac.sysvshm_enforce": 1,
    }


async def test_sysctl_parses_pyintras_port_range_fixture():
    # pyinfra's sysctl_port_range.json fixture: a tab-separated pair becomes
    # a list of ints, a whitespace-only value keeps "", and a simple word
    # stays a string.
    host = CannedShellHost(
        "net.ipv4.ip_local_port_range = 1024\t65535\n"
        "net.ipv4.ip_local_reserved_ports = \n"
        "kern.hostname: example\n"
    )
    assert await Sysctl.process(host) == {
        "net.ipv4.ip_local_port_range": [1024, 65535],
        "net.ipv4.ip_local_reserved_ports": "",
        "kern.hostname": "example",
    }


def test_sysctl_command_quotes_the_named_keys():
    assert Sysctl.command() == "sysctl -a 2>/dev/null || true"
    # Safe tokens stay unquoted (pyinfra's QuoteString), multiple keys join
    # with spaces, and a key carrying metacharacters is a literal lookup.
    assert (
        Sysctl.command(["kern.max-files"])
        == "sysctl kern.max-files 2>/dev/null || true"
    )
    assert (
        Sysctl.command(["a.b", "c.d"]) == "sysctl a.b c.d 2>/dev/null || true"
    )
    assert (
        Sysctl.command(["kern.evil; whoami"])
        == "sysctl 'kern.evil; whoami' 2>/dev/null || true"
    )


async def test_sysctl_keyed_read_over_the_shell():
    host = CannedShellHost("kern.max-files = 2000\n")
    assert await Sysctl.process(host, keys=["kern.max-files"]) == {
        "kern.max-files": 2000
    }
    assert host.shells == ["sysctl kern.max-files 2>/dev/null || true"]


def test_sysctl_parse_edge_cases():
    # A value outside the simple character set stays the stripped raw
    # string -- no int conversion, no list split.
    assert Sysctl.parse(["kernel.core_pattern = |/usr/sbin/coredump %p"]) == {
        "kernel.core_pattern": "|/usr/sbin/coredump %p"
    }
    # Mixed tokens int-convert individually; a single token collapses.
    assert Sysctl.parse(["kern.mix = 1 two"]) == {"kern.mix": [1, "two"]}
    assert Sysctl.parse(["kern.one = 42"]) == {"kern.one": 42}
    # Truthiness is tested BEFORE stripping: "key =" (nothing after the
    # separator) is skipped, "key = " (whitespace) keeps "".
    assert Sysctl.parse(["kern.none =", "kern.blank = "]) == {"kern.blank": ""}
    # "=" wins over ":" when both appear (the first split is on "=").
    assert Sysctl.parse(["kern.both: x = 5"]) == {"kern.both: x": 5}


async def test_sysctl_is_empty_on_empty_output():
    # An unknown key or a failed read maps to {} (pyinfra's default = dict).
    assert await Sysctl.process(CannedShellHost("")) == {}


async def test_sysctl_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        sysctls={"fs.file-max": 100000, "net.ipv4.ip_local_port_range": [1024, 65535]},
    )
    assert await Sysctl.process(host) == {
        "fs.file-max": 100000,
        "net.ipv4.ip_local_port_range": [1024, 65535],
    }
    # A keys filter selects the named settings; an unknown key reads as
    # absent, like the swallowed sysctl error.
    assert await Sysctl.process(host, keys=["fs.file-max"]) == {"fs.file-max": 100000}
    assert await Sysctl.process(host, keys=["fs.file-max", "no.such"]) == {
        "fs.file-max": 100000
    }
    assert await Sysctl.process(host, keys=["no.such"]) == {}
    # The seam defaults to {} -- a simulated machine with no settings listed.
    assert await Sysctl.process(SimulatedHost(name="h")) == {}


async def test_sysctl_seam_returns_a_copy():
    host = SimulatedHost(name="h", sysctls={"kern.pair": [1, 2]})
    result = await Sysctl.process(host)
    result["kern.pair"].append(3)
    result["kern.extra"] = 0
    assert host.sysctls == {"kern.pair": [1, 2]}


async def test_sysctl_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", sysctls={"fs.file-max": 100000}),
        SimulatedHost(name="web2", sysctls={"fs.file-max": 200000}),
    ]
    assert await gather_fact(hosts, Sysctl) == {
        "web1": {"fs.file-max": 100000},
        "web2": {"fs.file-max": 200000},
    }
    # The keys argument is forwarded to process verbatim.
    assert await gather_fact(hosts, Sysctl, keys=["fs.file-max"]) == {
        "web1": {"fs.file-max": 100000},
        "web2": {"fs.file-max": 200000},
    }
    assert await gather_fact(hosts, Sysctl, keys=["no.such"]) == {
        "web1": {},
        "web2": {},
    }


# --- SysctlFile / SysctlFiles ----------------------------------------------------


def test_sysctl_file_command_quotes_the_path():
    assert SysctlFile.command(_CONF_PATH) == (
        "test -e /etc/sysctl.conf && "
        "{ printf '#reemote5-sysctl-file\\n'; cat -- /etc/sysctl.conf; }"
    )
    assert SysctlFile.command("/etc/my file.conf") == (
        "test -e '/etc/my file.conf' && "
        "{ printf '#reemote5-sysctl-file\\n'; cat -- '/etc/my file.conf'; }"
    )


async def test_sysctl_file_reads_content_behind_the_marker():
    host = CannedShellHost("#reemote5-sysctl-file\nvm.swappiness = 10\n")
    assert await SysctlFile.process(host, _CONF_PATH) == "vm.swappiness = 10\n"
    assert host.shells == [SysctlFile.command(_CONF_PATH)]


async def test_sysctl_file_tells_empty_from_absent():
    # The marker alone means an existing-but-empty file (""); no marker at
    # all (test -e failed, empty stdout) means the file is absent (None).
    assert await SysctlFile.process(
        CannedShellHost("#reemote5-sysctl-file\n"), _CONF_PATH
    ) == ""
    assert await SysctlFile.process(CannedShellHost(""), _CONF_PATH) is None


async def test_sysctl_file_reads_the_shared_remote_files_seam():
    host = SimulatedHost(
        name="h", remote_files={_CONF_PATH: b"vm.swappiness = 10\n"}
    )
    assert await SysctlFile.process(host, _CONF_PATH) == "vm.swappiness = 10\n"
    assert await SysctlFile.process(host, "/etc/sysctl.d/none.conf") is None


async def test_sysctl_file_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(name="web1", remote_files={_CONF_PATH: b"a.b = 1\n"}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, SysctlFile, _CONF_PATH) == {
        "web1": "a.b = 1\n",
        "web2": None,
    }


def test_sysctl_files_parse_splits_marker_sections():
    output = [
        "ignored before the first marker",
        "#reemote5-sysctl-files /etc/sysctl.conf",
        "# a comment",
        "vm.swappiness = 10",
        "#reemote5-sysctl-files /etc/sysctl.d/90-net.conf",
        "net.ipv4.ip_forward = 1",
        "#reemote5-sysctl-files /etc/sysctl.d/empty.conf",
    ]
    assert SysctlFiles.parse(output) == {
        "/etc/sysctl.conf": {"vm.swappiness": "10"},
        "/etc/sysctl.d/90-net.conf": {"net.ipv4.ip_forward": "1"},
        # A present file with no assignments still appears, mapped to {}.
        "/etc/sysctl.d/empty.conf": {},
    }
    assert SysctlFiles.parse([]) == {}


async def test_sysctl_files_walks_the_standard_paths_over_the_shell():
    host = CannedShellHost(
        "#reemote5-sysctl-files /etc/sysctl.conf\n"
        "vm.swappiness = 10\n"
        "#reemote5-sysctl-files /etc/sysctl.d/90-net.conf\n"
        "net.ipv4.ip_forward = 1\n"
    )
    assert await SysctlFiles.process(host) == {
        "/etc/sysctl.conf": {"vm.swappiness": "10"},
        "/etc/sysctl.d/90-net.conf": {"net.ipv4.ip_forward": "1"},
    }
    assert host.shells == [SysctlFiles.COMMAND]
    # No files at all (the glob stays literal and fails test -f) reads {}.
    assert await SysctlFiles.process(CannedShellHost("")) == {}


async def test_sysctl_files_seam_projects_the_standard_paths_sorted():
    host = SimulatedHost(
        name="h",
        remote_files={
            "/etc/sysctl.d/90-net.conf": b"net.ipv4.ip_forward = 1\n",
            "/etc/sysctl.conf": b"vm.swappiness = 10\n",
            # Neither the drop-in dir's non-.conf files nor unrelated paths
            # are sysctl configuration; the projection excludes them.
            "/etc/sysctl.d/README": b"not a conf file\n",
            "/etc/other.conf": b"a.b = 1\n",
        },
    )
    assert await SysctlFiles.process(host) == {
        "/etc/sysctl.conf": {"vm.swappiness": "10"},
        "/etc/sysctl.d/90-net.conf": {"net.ipv4.ip_forward": "1"},
    }
    assert list(await SysctlFiles.process(host)) == [
        "/etc/sysctl.conf",
        "/etc/sysctl.d/90-net.conf",
    ]
    assert await SysctlFiles.process(SimulatedHost(name="h")) == {}


async def test_sysctl_files_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", remote_files={_CONF_PATH: b"a.b = 1\n"}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, SysctlFiles) == {
        "web1": {_CONF_PATH: {"a.b": "1"}},
        "web2": {},
    }


# --- Descriptors -----------------------------------------------------------------


def test_sysctl_set_command_shell_rendering():
    assert (
        SysctlSetCommand(key="vm.swappiness", value="10").shell()
        == "sysctl -w vm.swappiness=10"
    )
    # A multi-word value reaches the kernel as one quoted argument.
    assert (
        SysctlSetCommand(
            key="net.ipv4.ip_local_port_range", value="1024 65535"
        ).shell()
        == "sysctl -w net.ipv4.ip_local_port_range='1024 65535'"
    )
    assert (
        SysctlSetCommand(key="vm.swappiness", value="10", ignore_errors=True).shell()
        == "sysctl -w vm.swappiness=10 || true"
    )
    assert command_shell(
        SysctlSetCommand(key="vm.swappiness", value="10"), sudo=True
    ) == "sudo sysctl -w vm.swappiness=10"


def test_sysctl_file_upload_command_is_a_non_shell_transfer():
    cmd = SysctlFileUploadCommand(content="vm.swappiness = 10\n", dest=_SPOOL_PATH)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == "scp <sysctl conf> -> /tmp/reemote5-sysctl.conf"
    assert command_shell(cmd) == str(cmd)
    # The content is a plain hashable str, stable across phases.
    assert hash(cmd) == hash(
        SysctlFileUploadCommand(content="vm.swappiness = 10\n", dest=_SPOOL_PATH)
    )


def test_sysctl_file_install_command_wraps_the_chain_in_sh():
    cmd = SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_CONF_PATH)
    assert cmd.shell() == (
        "sh -c 'mkdir -p /etc && cp /tmp/reemote5-sysctl.conf /etc/sysctl.conf "
        "&& chmod 0644 /etc/sysctl.conf && rm -f /tmp/reemote5-sysctl.conf'"
    )
    # sudo elevates the WHOLE chain, cleanup included.
    assert command_shell(cmd, sudo=True) == f"sudo {cmd.shell()}"
    assert SysctlFileInstallCommand(
        spool=_SPOOL_PATH, dest=_DROPIN_PATH
    ).shell() == (
        "sh -c 'mkdir -p /etc/sysctl.d && "
        "cp /tmp/reemote5-sysctl.conf /etc/sysctl.d/99-reemote5.conf && "
        "chmod 0644 /etc/sysctl.d/99-reemote5.conf && "
        "rm -f /tmp/reemote5-sysctl.conf'"
    )


def test_sysctl_remove_and_reload_command_rendering():
    assert (
        SysctlFileRemoveCommand(path=_DROPIN_PATH).shell()
        == "rm -f /etc/sysctl.d/99-reemote5.conf"
    )
    assert (
        SysctlReloadCommand(path=_CONF_PATH).shell() == "sysctl -p /etc/sysctl.conf"
    )
    assert (
        SysctlReloadCommand(path="/etc/my file.conf").shell()
        == "sysctl -p '/etc/my file.conf'"
    )


# --- setting ---------------------------------------------------------------------


async def test_setting_applies_runtime_and_persists_when_both_differ():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10)
    )
    assert ctx.changed
    assert "vm.swappiness not set to 10 at runtime, will apply" in ctx.reason
    assert "/etc/sysctl.conf missing or differs for vm.swappiness, will write" in ctx.reason
    assert commands == [
        SysctlSetCommand(key="vm.swappiness", value="10"),
        SysctlFileUploadCommand(content="vm.swappiness = 10\n", dest=_SPOOL_PATH),
        SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_CONF_PATH),
        SysctlReloadCommand(path=_CONF_PATH),
    ]


async def test_setting_only_the_runtime_half_when_the_file_matches():
    host = SimulatedHost(
        name="h", remote_files={_CONF_PATH: b"vm.swappiness = 10\n"}
    )
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10)
    )
    assert ctx.changed
    assert commands == [SysctlSetCommand(key="vm.swappiness", value="10")]
    assert "/etc/sysctl.conf already up to date for vm.swappiness" in ctx.reason


async def test_setting_only_the_persist_half_when_the_runtime_matches():
    host = SimulatedHost(name="h", sysctls={"vm.swappiness": 10})
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10)
    )
    assert ctx.changed
    assert "vm.swappiness already set to 10 at runtime" in ctx.reason
    assert commands == [
        SysctlFileUploadCommand(content="vm.swappiness = 10\n", dest=_SPOOL_PATH),
        SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_CONF_PATH),
        SysctlReloadCommand(path=_CONF_PATH),
    ]


async def test_setting_no_change_when_fully_in_state():
    host = SimulatedHost(
        name="h",
        sysctls={"vm.swappiness": 10},
        remote_files={_CONF_PATH: b"vm.swappiness = 10\n"},
    )
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10)
    )
    assert not ctx.changed
    assert ctx.reason == (
        "vm.swappiness already set to 10 at runtime; "
        "/etc/sysctl.conf already up to date for vm.swappiness"
    )
    assert commands == []


async def test_setting_normalizes_a_multi_word_string_like_pyinfra():
    # "1024 65535" compares as the int list the Sysctl fact parses.
    host = SimulatedHost(
        name="h",
        sysctls={"net.ipv4.ip_local_port_range": [1024, 65535]},
        remote_files={
            _CONF_PATH: b"net.ipv4.ip_local_port_range = 1024 65535\n"
        },
    )
    ctx, commands = await materialize(
        host,
        op(sysctl.setting, key="net.ipv4.ip_local_port_range", value="1024 65535"),
    )
    assert not ctx.changed
    assert commands == []


async def test_setting_list_value_normalizes_like_pyinfra():
    # A list value takes the same normalization path (direct generator call;
    # op() itself rejects a list -- see the hashability regression below).
    host = SimulatedHost(
        name="h", sysctls={"net.ipv4.ip_local_port_range": [32768, 60999]}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    commands = [
        cmd
        async for cmd in sysctl.setting(
            ctx,
            key="net.ipv4.ip_local_port_range",
            value=[1024, 65535],
            persist=False,
        )
    ]
    assert ctx.changed
    assert commands == [
        SysctlSetCommand(key="net.ipv4.ip_local_port_range", value="1024 65535")
    ]


def test_op_rejects_a_list_value_but_the_string_spelling_is_equivalent():
    # OperationRequest kwargs must be hashable; the multi-word string is the
    # op()-safe spelling of pyinfra's list value (same normalization).
    with pytest.raises(UnhashableArgumentError):
        op(sysctl.setting, key="net.ipv4.ip_local_port_range", value=[1024, 65535])
    request = op(
        sysctl.setting, key="net.ipv4.ip_local_port_range", value="1024 65535"
    )
    assert request.kwargs["value"] == "1024 65535"


async def test_setting_absent_removes_the_key_lines_only():
    host = SimulatedHost(
        name="h",
        sysctls={"vm.swappiness": 60},
        remote_files={
            _CONF_PATH: b"fs.file-max = 100000\nvm.swappiness = 60\n"
        },
    )
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=60, present=False)
    )
    assert ctx.changed
    assert "runtime value left unchanged (present=False)" in ctx.reason
    assert "vm.swappiness present in /etc/sysctl.conf, will remove" in ctx.reason
    # The runtime value is untouched; the file keeps its other lines.
    assert commands == [
        SysctlFileUploadCommand(content="fs.file-max = 100000\n", dest=_SPOOL_PATH),
        SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_CONF_PATH),
        SysctlReloadCommand(path=_CONF_PATH),
    ]


async def test_setting_absent_no_change_when_the_key_is_not_persisted():
    host = SimulatedHost(
        name="h", remote_files={_CONF_PATH: b"fs.file-max = 100000\n"}
    )
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10, present=False)
    )
    assert not ctx.changed
    assert commands == []


async def test_setting_persist_false_manages_only_the_runtime():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10, persist=False)
    )
    assert ctx.changed
    assert "persistence skipped (persist=False)" in ctx.reason
    assert commands == [SysctlSetCommand(key="vm.swappiness", value="10")]


async def test_setting_sysctl_set_false_manages_only_the_file():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10, sysctl_set=False)
    )
    assert ctx.changed
    assert "runtime apply skipped (sysctl_set=False)" in ctx.reason
    assert commands == [
        SysctlFileUploadCommand(content="vm.swappiness = 10\n", dest=_SPOOL_PATH),
        SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_CONF_PATH),
        SysctlReloadCommand(path=_CONF_PATH),
    ]


async def test_setting_reload_false_skips_the_reload():
    host = SimulatedHost(name="h", sysctls={"vm.swappiness": 10})
    _ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10, reload=False)
    )
    assert commands == [
        SysctlFileUploadCommand(content="vm.swappiness = 10\n", dest=_SPOOL_PATH),
        SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_CONF_PATH),
    ]


async def test_setting_ignore_errors_guards_the_runtime_command():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host,
        op(sysctl.setting, key="vm.nosuch", value=1, persist=False, ignore_errors=True),
    )
    assert commands == [
        SysctlSetCommand(key="vm.nosuch", value="1", ignore_errors=True)
    ]


async def test_setting_targets_a_dropin_file():
    host = SimulatedHost(name="h", sysctls={"net.ipv4.ip_forward": 1})
    _ctx, commands = await materialize(
        host,
        op(
            sysctl.setting,
            key="net.ipv4.ip_forward",
            value=1,
            sysctl_file="/etc/sysctl.d/90-network.conf",
        ),
    )
    # The legacy /etc/sysctl.conf stays untouched.
    assert commands == [
        SysctlFileUploadCommand(
            content="net.ipv4.ip_forward = 1\n", dest=_SPOOL_PATH
        ),
        SysctlFileInstallCommand(spool=_SPOOL_PATH, dest="/etc/sysctl.d/90-network.conf"),
        SysctlReloadCommand(path="/etc/sysctl.d/90-network.conf"),
    ]


async def test_setting_kwargs_absorb_an_unknown_argument():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(sysctl.setting, key="vm.swappiness", value=10, pyinfra_global="ignored"),
    )
    assert ctx.changed
    assert len(commands) == 4


async def test_setting_mutates_the_seams_and_is_idempotent():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10)
    )
    for cmd in commands:
        await send_command(host, cmd)
    assert host.sysctls == {"vm.swappiness": 10}
    assert host.remote_files[_CONF_PATH] == b"vm.swappiness = 10\n"
    assert _SPOOL_PATH not in host.remote_files  # the install removed the spool
    assert _CONF_PATH in host.existing_files
    assert host.file_stats[_CONF_PATH]["mode"] == 644
    # A fresh materialization now reads both halves in state.
    ctx, commands = await materialize(
        host, op(sysctl.setting, key="vm.swappiness", value=10)
    )
    assert not ctx.changed
    assert commands == []


# --- conf ------------------------------------------------------------------------

_SETTINGS = (("vm.swappiness", 10), ("net.ipv4.ip_forward", 1))
_CONF_CONTENT = "vm.swappiness = 10\nnet.ipv4.ip_forward = 1\n"


async def test_conf_writes_the_whole_file_then_reloads():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(sysctl.conf, settings=_SETTINGS))
    assert ctx.changed
    assert ctx.reason == (
        f"sysctl config {_DROPIN_PATH} missing or differs from the settings"
    )
    assert commands == [
        SysctlFileUploadCommand(content=_CONF_CONTENT, dest=_SPOOL_PATH),
        SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_DROPIN_PATH),
        SysctlReloadCommand(path=_DROPIN_PATH),
    ]


async def test_conf_no_change_when_content_matches():
    host = SimulatedHost(
        name="h", remote_files={_DROPIN_PATH: _CONF_CONTENT.encode()}
    )
    ctx, commands = await materialize(host, op(sysctl.conf, settings=_SETTINGS))
    assert not ctx.changed
    assert ctx.reason == f"sysctl config already up to date ({_DROPIN_PATH})"
    assert commands == []


async def test_conf_rewrites_a_drifted_file_wholesale():
    host = SimulatedHost(
        name="h", remote_files={_DROPIN_PATH: b"vm.swappiness = 60\n# stale\n"}
    )
    _ctx, commands = await materialize(host, op(sysctl.conf, settings=_SETTINGS))
    # The whole-file model replaces the content, stale lines included.
    assert commands[0] == SysctlFileUploadCommand(
        content=_CONF_CONTENT, dest=_SPOOL_PATH
    )


async def test_conf_removes_the_file_when_absent_requested():
    host = SimulatedHost(
        name="h", remote_files={_DROPIN_PATH: _CONF_CONTENT.encode()}
    )
    ctx, commands = await materialize(
        host, op(sysctl.conf, settings=(), present=False)
    )
    assert ctx.changed
    assert ctx.reason == f"sysctl config {_DROPIN_PATH} present, will remove"
    assert commands == [SysctlFileRemoveCommand(path=_DROPIN_PATH)]


async def test_conf_absent_no_change_when_already_missing():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(sysctl.conf, settings=(), present=False)
    )
    assert not ctx.changed
    assert ctx.reason == f"sysctl config already absent ({_DROPIN_PATH})"
    assert commands == []


async def test_conf_reload_false_and_custom_file():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host,
        op(
            sysctl.conf,
            settings=_SETTINGS,
            sysctl_file="/etc/sysctl.d/90-network.conf",
            reload=False,
        ),
    )
    assert commands == [
        SysctlFileUploadCommand(content=_CONF_CONTENT, dest=_SPOOL_PATH),
        SysctlFileInstallCommand(
            spool=_SPOOL_PATH, dest="/etc/sysctl.d/90-network.conf"
        ),
    ]


async def test_conf_empty_settings_write_an_empty_file():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(sysctl.conf, settings=()))
    assert ctx.changed
    assert commands[0] == SysctlFileUploadCommand(content="", dest=_SPOOL_PATH)


def test_op_rejects_a_dict_of_settings():
    # A dict kwarg is unhashable; the tuple-of-pairs spelling is the API.
    with pytest.raises(UnhashableArgumentError):
        op(sysctl.conf, settings={"vm.swappiness": 10})


async def test_conf_kwargs_absorb_an_unknown_argument():
    host = SimulatedHost(name="h")
    ctx, _commands = await materialize(
        host, op(sysctl.conf, settings=_SETTINGS, pyinfra_global="ignored")
    )
    assert ctx.changed


async def test_conf_mutates_the_seams_and_the_reload_applies_at_runtime():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(host, op(sysctl.conf, settings=_SETTINGS))
    for cmd in commands:
        await send_command(host, cmd)
    assert host.remote_files[_DROPIN_PATH] == _CONF_CONTENT.encode()
    assert _SPOOL_PATH not in host.remote_files
    # The scoped sysctl -p applied every assignment to the runtime seam.
    assert host.sysctls == {"vm.swappiness": 10, "net.ipv4.ip_forward": 1}
    ctx, commands = await materialize(host, op(sysctl.conf, settings=_SETTINGS))
    assert not ctx.changed
    assert commands == []


# --- Simulated seams ---------------------------------------------------------------


async def test_set_command_mutates_the_sysctls_seam_with_parsed_values():
    host = SimulatedHost(name="h")
    await send_command(host, SysctlSetCommand(key="vm.swappiness", value="10"))
    assert host.sysctls == {"vm.swappiness": 10}
    await send_command(
        host,
        SysctlSetCommand(key="net.ipv4.ip_local_port_range", value="1024 65535"),
    )
    assert host.sysctls["net.ipv4.ip_local_port_range"] == [1024, 65535]


async def test_install_and_remove_keep_every_file_store_consistent():
    host = SimulatedHost(name="h")
    await send_command(
        host, SysctlFileUploadCommand(content=_CONF_CONTENT, dest=_SPOOL_PATH)
    )
    await send_command(
        host, SysctlFileInstallCommand(spool=_SPOOL_PATH, dest=_DROPIN_PATH)
    )
    assert host.remote_files[_DROPIN_PATH] == _CONF_CONTENT.encode()
    assert _SPOOL_PATH not in host.remote_files
    assert _DROPIN_PATH in host.existing_files
    assert host.file_stats[_DROPIN_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(_CONF_CONTENT.encode()),
    }
    # Both persistence facts see the installed file immediately.
    assert await SysctlFile.process(host, _DROPIN_PATH) == _CONF_CONTENT
    assert await SysctlFiles.process(host) == {
        _DROPIN_PATH: {"vm.swappiness": "10", "net.ipv4.ip_forward": "1"}
    }
    await send_command(host, SysctlFileRemoveCommand(path=_DROPIN_PATH))
    assert _DROPIN_PATH not in host.remote_files
    assert _DROPIN_PATH not in host.existing_files
    assert _DROPIN_PATH not in host.file_stats
    assert await SysctlFile.process(host, _DROPIN_PATH) is None
    assert await SysctlFiles.process(host) == {}


async def test_reload_applies_the_files_assignments_and_tolerates_absence():
    host = SimulatedHost(
        name="h",
        remote_files={
            _CONF_PATH: b"# comment\nvm.swappiness = 10\n\nnet.ipv4.ip_forward=1\n"
        },
    )
    await send_command(host, SysctlReloadCommand(path=_CONF_PATH))
    assert host.sysctls == {"vm.swappiness": 10, "net.ipv4.ip_forward": 1}
    # An absent path applies nothing (no KeyError).
    await send_command(host, SysctlReloadCommand(path="/etc/sysctl.d/none.conf"))
    assert host.sysctls == {"vm.swappiness": 10, "net.ipv4.ip_forward": 1}


# --- End-to-end: plan / apply / dry / lockstep / determinism -------------------------


def tune_swappiness(host: Host) -> Generator[OperationRequest]:
    """The example deploy: tune vm.swappiness at runtime and persist it."""
    yield op(
        sysctl.setting,
        name="Tune swappiness",
        key="vm.swappiness",
        value=10,
        sudo=True,
    )


def empty_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts with no sysctl state yet."""
    return SimulatedHost.from_inventory(entry)


async def test_setting_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(INVENTORY, tune_swappiness, host_factory=empty_factory)
    assert [p.changed for p in plans["web1"]] == [True]
    assert [p.changed for p in plans["web2"]] == [True]
    assert "Tune swappiness" in format_plan(plans)
    assert report is not None
    assert [e.name for e in report.log] == ["Tune swappiness", "Tune swappiness"]
    log = format_log(report)
    # The transfer is unprivileged (no sudo); runtime/install/reload elevate.
    assert log.count("scp <sysctl conf> -> /tmp/reemote5-sysctl.conf") == 2
    assert log.count("$ sudo sysctl -w vm.swappiness=10") == 2
    assert log.count("$ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote5-sysctl.conf") == 2
    assert log.count("$ sudo sysctl -p /etc/sysctl.conf") == 2
    for host in hosts:
        assert host.sysctls == {"vm.swappiness": 10}
        assert host.remote_files[_CONF_PATH] == b"vm.swappiness = 10\n"
        assert _SPOOL_PATH not in host.remote_files
    # A fresh prepare against the now-tuned hosts plans no change.
    for host in hosts:
        _runtimes, plans2 = await run_prepare([host], tune_swappiness)
        assert [p.changed for p in plans2[host.name]] == [False]


async def test_setting_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, tune_swappiness, host_factory=empty_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.sysctls == {}
        assert host.remote_files == {}


def install_conf(host: Host) -> Generator[OperationRequest]:
    """The example deploy: manage a whole drop-in file, elevated."""
    yield op(sysctl.conf, name="Kernel tuning", settings=_SETTINGS, sudo=True)


async def test_conf_runs_across_hosts_and_the_reload_applies():
    plans, report, hosts = await run(INVENTORY, install_conf, host_factory=empty_factory)
    assert [p.changed for p in plans["web1"]] == [True]
    assert report is not None
    log = format_log(report)
    assert log.count("$ sudo sysctl -p /etc/sysctl.d/99-reemote5.conf") == 2
    for host in hosts:
        assert host.remote_files[_DROPIN_PATH] == _CONF_CONTENT.encode()
        assert host.sysctls == {"vm.swappiness": 10, "net.ipv4.ip_forward": 1}
        _runtimes, plans2 = await run_prepare([host], install_conf)
        assert [p.changed for p in plans2[host.name]] == [False]


async def test_setting_branching_on_live_state_raises_determinism_error():
    def live_branch(host: Host) -> Generator[OperationRequest]:
        yield op(
            sysctl.setting, name="Tune", key="vm.swappiness", value=10, sudo=True
        )
        # The illegal branch: consult live state the first write mutates.
        if "vm.swappiness" in host.sysctls:
            yield op(sysctl.conf, name="Extra", settings=_SETTINGS, sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], live_branch)
    assert [p.request.name for p in plans["h"]] == ["Tune"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- Exports ------------------------------------------------------------------------


def test_sysctl_exports_and_the_server_relocation():
    import reemote5
    from reemote5.modules import server

    # Facts and descriptors are exported from the namespace and the top level.
    for symbol in (
        Sysctl,
        SysctlFile,
        SysctlFiles,
        SysctlSetCommand,
        SysctlFileUploadCommand,
        SysctlFileInstallCommand,
        SysctlFileRemoveCommand,
        SysctlReloadCommand,
    ):
        assert getattr(sysctl, symbol.__name__) is symbol
        assert getattr(reemote5, symbol.__name__) is symbol
    # The top-level Sysctl binding now points at the sysctl module's class.
    assert reemote5.Sysctl is Sysctl
    # Operations stay namespace-only (never re-exported at the top level).
    for operation in ("setting", "conf"):
        assert operation in sysctl.__all__
        assert hasattr(sysctl, operation)
        assert operation not in reemote5.__all__
    # server no longer exports a Sysctl fact (the canonical-home decision).
    assert not hasattr(server, "Sysctl")
    assert "Sysctl" not in server.__all__