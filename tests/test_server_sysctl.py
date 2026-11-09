# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the server.sysctl example-only recipe (spool-and-install).

pyinfra's ``server.sysctl`` is not ported as a bespoke operation -- the
developer chose the example-only recipe: ``normalize_value`` and
``sysctl_persist_edit`` in ``examples/modules/server/server_op_sysctl.py``
reproduce pyinfra's value coercion and ``files.line`` lookup on a
``Sysctl`` + ``FileContents`` snapshot, the generic ``files.edit``
primitive spools the desired sysctl.conf to a writable path, an elevated
``files.move`` installs it, and the demo-side ``sysctl_set`` operation (a
raw ``StringCommand``) applies the live value. These tests import that
code straight from the example module, so the shipped demonstration is
what is verified.

Covers the value normalization and command rendering against pyinfra's
own operation-test fixtures (``set.json``, ``set_list.json``,
``set_needs_quotes.json``, the four ``set_noop*`` comparisons,
``set_persist.json``'s appended line), the ``files.line`` persist parity
including the documented ``re.escape`` hardening and surviving stale
lines, the two independent snapshot gates, the ``files.edit``
materialization, the three-run recipe end to end through ``SimulatedHost``
including elevation, script-level idempotence, and the
``DeterminismError`` a factory call inside the deploy generator would
trip.
"""

import importlib.util
from collections.abc import Generator
from pathlib import Path

import pytest

from reemote5 import StringCommand
from reemote5.core import FactCache, OpContext, OperationRequest, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    run,
)
from reemote5.hosts import Host
from reemote5.modules import files
from reemote5.modules.files.files import PutCommand, read_source_bytes
from tests.simulated_host import SimulatedHost

# Import the example module by path: the recipe under test lives in
# examples/ (demo content never enters src/), and the example is not a
# package on sys.path.
_example_path = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "modules"
    / "server"
    / "server_op_sysctl.py"
)
_spec = importlib.util.spec_from_file_location("server_op_sysctl_example", _example_path)
server_sysctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server_sysctl)
normalize_value = server_sysctl.normalize_value
sysctl_persist_edit = server_sysctl.sysctl_persist_edit
sysctl_needed = server_sysctl.sysctl_needed
make_transform = server_sysctl.make_transform
sysctl_set = server_sysctl.sysctl_set

KEY = "fs.file-max"
CONF_PATH = "/etc/sysctl.conf"
SPOOL_PATH = "/tmp/sysctl.conf"

# The fixture sysctl.conf: the setting commented out among unrelated lines
# (pyinfra's files.line search-style match uncomments it in place).
CONF_CONTENT = "# /etc/sysctl.conf\n# fs.file-max = 100000\nvm.swappiness = 10\n"
DESIRED = "# /etc/sysctl.conf\nfs.file-max = 100000\nvm.swappiness = 10\n"

INVENTORY = [
    {
        "host": "web1",
        "group": "centos",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]
TWO_HOSTS = [
    *INVENTORY,
    {"host": "web2", "group": "centos", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]

# Stable spool transforms for the end-to-end deploys, exactly as main() fills
# them between the snapshots and run 2 (the same callable objects in both
# phases -- the determinism guard compares them by value).
server_sysctl.SPOOL_TRANSFORMS["web1"] = make_transform(DESIRED)
server_sysctl.SPOOL_TRANSFORMS["web2"] = make_transform(DESIRED)


def seeded_factory(entry: dict) -> SimulatedHost:
    """A host with the fixture sysctl.conf and a stale live value."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files = {CONF_PATH: CONF_CONTENT.encode()}
    host.file_stats = {CONF_PATH: {"mode": 644}}
    host.directory_stats = {"/etc": {"mode": 755}}
    host.sysctls = {KEY: 90000, "vm.swappiness": 10}
    return host


# --- Value normalization: pyinfra fixture parity -------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2000, ("2000", 2000)),  # set.json / set_noop_string_value.json
        ("2000", ("2000", 2000)),
        (0, ("0", 0)),  # set_noop_zero_int.json
        ([2000, 3000], ("2000 3000", [2000, 3000])),  # set_list.json
        ([1024, 65535], ("1024 65535", [1024, 65535])),  # set_noop_*_list.json
        ("1024 65535", ("1024 65535", [1024, 65535])),  # set_noop_*_string.json
        ([2000], ("2000", 2000)),  # single-item list collapses
        # set_needs_quotes.json: any multi-word string splits, even words
        ("some value", ("some value", ["some", "value"])),
        (["a", 2], ("a 2", ["a", 2])),  # non-numeric items survive try_int
    ],
)
def test_normalize_value_mirrors_pyinfra(value, expected):
    assert normalize_value(value) == expected


def test_example_constants_normalize_once():
    """The module-level pair main() and the apply deploy share."""
    assert server_sysctl.STRING_VALUE == "100000"
    assert server_sysctl.NORMALIZED_VALUE == 100000


# --- The demo-side sysctl-set operation ----------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        # set.json
        ("kern.max-files", 2000, "sysctl kern.max-files=2000"),
        # set_list.json
        ("kern.max-files", [2000, 3000], "sysctl kern.max-files='2000 3000'"),
        # set_needs_quotes.json
        ("kern.evil; whoami", "some value", "sysctl 'kern.evil; whoami'='some value'"),
    ],
)
async def test_sysctl_set_renders_pyinfra_fixture_commands(key, value, expected):
    string_value, _normalized = normalize_value(value)
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in sysctl_set(ctx, key, string_value)]

    assert ctx.changed
    assert ctx.reason == f"will set sysctl {key} to {string_value}"
    (cmd,) = commands
    assert isinstance(cmd, StringCommand)
    assert command_shell(cmd) == expected
    assert command_shell(cmd, sudo=True) == f"sudo {expected}"


async def test_sysctl_set_kwargs_absorb_pyinfra_globals():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd async for cmd in sysctl_set(ctx, KEY, "100000", _pyinfra_global="ignored")
    ]
    assert commands == [StringCommand("sysctl fs.file-max=100000")]


# --- The Sysctl skip gate (pyinfra's change condition) -------------------------


@pytest.mark.parametrize(
    ("value", "live", "expected"),
    [
        (2000, {}, True),  # set.json: absent key -> needed
        (2000, {"kern.max-files": 2000}, False),  # set_noop.json
        ("2000", {"kern.max-files": 2000}, False),  # set_noop_string_value.json
        (0, {"kern.max-files": 0}, False),  # set_noop_zero_int.json
        (  # set_noop_space_separated_list.json
            [1024, 65535],
            {"kern.max-files": [1024, 65535]},
            False,
        ),
        (  # set_noop_space_separated_string.json
            "1024 65535",
            {"kern.max-files": [1024, 65535]},
            False,
        ),
        (2000, {"kern.max-files": 3000}, True),  # different live value
    ],
)
def test_sysctl_needed_mirrors_pyinfra(value, live, expected):
    _string_value, normalized = normalize_value(value)
    assert sysctl_needed(live, "kern.max-files", normalized) is expected


# --- The pure persist edit: pyinfra files.line parity --------------------------


def test_persist_appends_the_line_when_absent():
    """pyinfra set_persist.json parity: no matching line -> the
    ``echo 'kern.max-files = 2000' >> /etc/sysctl.conf`` append."""
    assert sysctl_persist_edit("# comment\n", "kern.max-files", "2000") == (
        "# comment\nkern.max-files = 2000\n"
    )


def test_persist_of_an_absent_file_creates_the_line():
    """pyinfra's echo append creates an absent file; None content reads as
    empty here."""
    assert sysctl_persist_edit(None, "kern.max-files", "2000") == (
        "kern.max-files = 2000\n"
    )


def test_persist_uncomments_a_matching_line_in_place():
    """pyinfra's search-style ``^.*pattern.*$`` wrapping matches commented
    lines too; the sed normalizes every match to the replacement."""
    assert sysctl_persist_edit(CONF_CONTENT, KEY, "100000") == DESIRED


@pytest.mark.parametrize(
    "spelling",
    [
        "fs.file-max=100000",
        "fs.file-max    =   100000",
        "fs.file-max\t=\t100000",
    ],
)
def test_persist_normalizes_whitespace_spellings(spelling):
    """The ``[[:space:]]*`` on both sides of the ``=`` matches zero or more
    spaces/tabs; every spelling becomes ``key = value``."""
    assert sysctl_persist_edit(f"{spelling}\n", KEY, "100000") == (
        "fs.file-max = 100000\n"
    )


def test_persist_normalizes_every_matching_line():
    """pyinfra's sed replaces ALL matching lines, not just the first."""
    current = "# fs.file-max = 100000\nfs.file-max=100000\n"
    assert sysctl_persist_edit(current, KEY, "100000") == (
        "fs.file-max = 100000\nfs.file-max = 100000\n"
    )


def test_persist_leaves_a_stale_value_line_alone():
    """pyinfra parity: the pattern includes the value, so ``key = other``
    never matches -- the stale line survives beside the appended one and
    the last assignment wins at boot."""
    current = "fs.file-max = 90000\n"
    assert sysctl_persist_edit(current, KEY, "100000") == (
        "fs.file-max = 90000\nfs.file-max = 100000\n"
    )


def test_persist_matches_a_longer_value_sharing_the_prefix():
    """pyinfra's unanchored pattern: ``fs.file-max = 1000000`` contains
    ``fs.file-max = 100000`` and is normalized like any other match."""
    assert sysctl_persist_edit("fs.file-max = 1000000\n", KEY, "100000") == (
        "fs.file-max = 100000\n"
    )


def test_persist_key_and_value_are_matched_literally():
    """The documented hardening: re.escape keeps the '.' in fs.file-max
    literal, where pyinfra's raw BRE interpolation would match any
    character -- the near-miss line survives and the exact line appends."""
    assert sysctl_persist_edit("fsXfile-max = 100000\n", KEY, "100000") == (
        "fsXfile-max = 100000\nfs.file-max = 100000\n"
    )


def test_persist_is_a_fixpoint_and_normalizes_the_trailing_newline():
    assert sysctl_persist_edit(DESIRED, KEY, "100000") == DESIRED
    once = sysctl_persist_edit(CONF_CONTENT.rstrip("\n"), KEY, "100000")
    assert once == DESIRED
    assert sysctl_persist_edit(once, KEY, "100000") == once


# --- The spool transform factory ----------------------------------------------


def test_make_transform_freezes_desired_content_and_is_fixpoint():
    transform = make_transform(DESIRED)
    assert transform(None) == DESIRED
    assert transform("stale spool text") == DESIRED
    assert transform(transform(None)) == transform(None)


# --- Through files.edit: materialization --------------------------------------


async def test_edit_yields_a_put_for_the_absent_spool():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    transform = make_transform(DESIRED)
    commands = [cmd async for cmd in files.edit(ctx, SPOOL_PATH, transform)]

    assert ctx.changed
    assert ctx.reason == f"content {SPOOL_PATH} is absent, will create from the transform"
    (put,) = commands
    assert isinstance(put, PutCommand)
    assert put.dest == SPOOL_PATH
    assert read_source_bytes(put.src) == DESIRED.encode()


async def test_edit_is_a_no_op_when_the_spool_already_matches():
    host = SimulatedHost(name="h", remote_files={SPOOL_PATH: DESIRED.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    transform = make_transform(DESIRED)
    commands = [cmd async for cmd in files.edit(ctx, SPOOL_PATH, transform)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({SPOOL_PATH})"
    assert commands == []


# --- End to end: the three-run recipe ------------------------------------------


async def test_apply_run_plans_and_runs_the_elevated_sysctl():
    plans, report, hosts = await run(
        INVENTORY, server_sysctl.apply, host_factory=seeded_factory
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert "will set sysctl fs.file-max to 100000" in format_plan(plans)

    assert report is not None
    assert "$ sudo sysctl fs.file-max=100000" in format_log(report)
    # The simulated StringCommand has no in-memory effect; the persist
    # file is untouched until run 3 installs the spool.
    assert hosts[0].sysctls[KEY] == 90000
    assert hosts[0].remote_files[CONF_PATH] == CONF_CONTENT.encode()


async def test_apply_lockstep_across_two_hosts():
    _plans, report, hosts = await run(
        TWO_HOSTS, server_sysctl.apply, host_factory=seeded_factory
    )
    assert report is not None
    assert [entry.name for entry in report.log] == ["Set sysctl", "Set sysctl"]
    assert len(hosts) == 2


async def test_spool_run_plan_and_apply_log():
    plans, report, hosts = await run(
        INVENTORY, server_sysctl.spool, host_factory=seeded_factory
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert f"content {SPOOL_PATH} is absent" in format_plan(plans)

    assert report is not None
    assert "$ scp <stream> -> /tmp/sysctl.conf" in format_log(report)
    assert hosts[0].remote_files[SPOOL_PATH] == DESIRED.encode()
    # The root-owned original is untouched until run 3 installs the spool.
    assert hosts[0].remote_files[CONF_PATH] == CONF_CONTENT.encode()


async def test_full_recipe_spans_three_runs_and_closes_both_gates():
    # Run 1: the elevated sysctl applies the live value.
    _plans, report, hosts = await run(
        INVENTORY, server_sysctl.apply, host_factory=seeded_factory
    )
    host = hosts[0]
    assert report is not None
    assert "$ sudo sysctl fs.file-max=100000" in format_log(report)

    # Run 2: the edit creates the writable spool file (unprivileged scp).
    _plans, _report, _hosts = await run(
        INVENTORY, server_sysctl.spool, host_factory=lambda entry: host
    )
    # The simulated put stores content only; seed the stat row the real
    # filesystem would now have for the spool file.
    host.file_stats[SPOOL_PATH] = {"mode": 644}

    # Run 3: the elevated move installs it over the root-owned target.
    _plans, report, _hosts = await run(
        INVENTORY, server_sysctl.install, host_factory=lambda entry: host
    )
    assert report is not None
    log = format_log(report)
    assert "$ sudo rm -rf /etc/sysctl.conf" in log
    assert "$ sudo mv /tmp/sysctl.conf /etc" in log
    assert [entry.name for entry in report.log] == ["Install sysctl.conf"]
    assert host.remote_files == {CONF_PATH: DESIRED.encode()}
    assert SPOOL_PATH not in host.file_stats

    # The simulated StringCommand has no in-memory effect; emulate what
    # the live sysctl did on a real host and prove both script-level skip
    # gates close: a re-run snapshots and skips every run.
    host.sysctls[KEY] = 100000
    assert sysctl_needed(host.sysctls, KEY, server_sysctl.NORMALIZED_VALUE) is False
    assert sysctl_persist_edit(DESIRED, KEY, "100000") == DESIRED


async def test_dry_run_sends_nothing():
    plans, report, hosts = await run(
        INVENTORY, server_sysctl.spool, dry=True, host_factory=seeded_factory
    )
    assert report is None
    assert plans["web1"][0].changed
    assert SPOOL_PATH not in hosts[0].remote_files


async def test_duplicate_spool_requests_plan_vs_apply():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool sysctl.conf",
            path=SPOOL_PATH,
            transform=server_sysctl.SPOOL_TRANSFORMS[host.name],
        )
        yield op(
            files.edit,
            name="Re-spool sysctl.conf",
            path=SPOOL_PATH,
            transform=server_sysctl.SPOOL_TRANSFORMS[host.name],
        )

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=seeded_factory
    )
    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Spool sysctl.conf"].changed
    assert not entries["Re-spool sysctl.conf"].changed


async def test_factory_call_inside_the_deploy_raises_determinism_error():
    """A transform built fresh in the deploy generator has a new identity each
    phase, so the apply-phase request diverges from the prepare plan -- the
    factory must be called once, outside the generator."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool sysctl.conf",
            path=SPOOL_PATH,
            transform=make_transform(DESIRED),
        )

    with pytest.raises(DeterminismError):
        await run(INVENTORY, deploy, host_factory=seeded_factory)


async def test_spool_and_install_in_one_deploy_raises_at_prepare():
    """The documented multi-run constraint (files.move's always-raise rule):
    prepare never sends, so the spool the edit would create does not exist
    when the move materializes."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool sysctl.conf",
            path=SPOOL_PATH,
            transform=server_sysctl.SPOOL_TRANSFORMS[host.name],
        )
        yield op(
            files.move,
            name="Install sysctl.conf",
            src=SPOOL_PATH,
            dest="/etc",
            overwrite=True,
            sudo=True,
        )

    with pytest.raises(ValueError, match=r"src /tmp/sysctl\.conf does not exist"):
        await run(INVENTORY, deploy, host_factory=seeded_factory)