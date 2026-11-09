# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the server.security_limit example-only recipe (spool-and-install).

pyinfra's ``server.security_limit`` is not ported as a bespoke operation --
the developer chose the example-only recipe: ``security_limit_edit`` and
``limits_install`` in
``examples/modules/server/server_security_limit.py`` reproduce pyinfra's
``files.line`` lookup on a ``FileContents`` snapshot, the generic
``files.edit`` primitive spools the desired limits.conf to a writable
path, and the demo-side stateless ``limits_install`` operation (an
elevated raw ``StringCommand``) installs it root-owned ``0644`` --
deliberately NOT ``files.move``, whose ``mv`` would preserve the spool's
owner and mode on a stock system file. These tests import that code
straight from the example module, so the shipped demonstration is what
is verified.

Covers the transform against pyinfra's own operation fixture (the
``root/hard/memlock/unlimited`` append), the anchored ``files.line``
match parity -- value not in the pattern, every match normalized, item
prefixes included, commented lines and other domains not -- the
documented ``re.escape`` hardening, the fixpoint and trailing-newline
normalization, the ``files.edit`` materialization, the install command's
plain and elevated renderings, the one-deploy recipe end to end through
``SimulatedHost``, the script-level skip gate on re-run, and the
``DeterminismError`` a factory call inside the deploy generator trips.
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
    send_command,
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
    / "server_security_limit.py"
)
_spec = importlib.util.spec_from_file_location(
    "server_security_limit_example", _example_path
)
security_limit_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(security_limit_example)
limit_line = security_limit_example.limit_line
security_limit_edit = security_limit_example.security_limit_edit
make_transform = security_limit_example.make_transform
limits_install = security_limit_example.limits_install

DOMAIN = "*"
LIMIT_TYPE = "soft"
ITEM = "nofile"
VALUE = 1024
LIMITS_FILE = "/etc/security/limits.conf"
SPOOL_PATH = "/tmp/limits.conf"

# The fixture limits.conf: a stale hard limit and another domain among
# the header comments (none of them matches the anchored soft/nofile/*
# pattern, so the desired line appends).
LIMITS_CONTENT = "# /etc/security/limits.conf\nroot hard nofile 65535\n"
DESIRED = "# /etc/security/limits.conf\nroot hard nofile 65535\n*\tsoft\tnofile\t1024\n"
LIMIT_LINE = "*\tsoft\tnofile\t1024\n"

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

# Stable spool transforms for the end-to-end deploys, exactly as main()
# fills them between the snapshot and the run (the same callable objects
# in both phases -- the determinism guard compares them by value).
security_limit_example.SPOOL_TRANSFORMS["web1"] = make_transform(DESIRED)
security_limit_example.SPOOL_TRANSFORMS["web2"] = make_transform(DESIRED)

# The rendered install one-liner, asserted verbatim below; verified
# against the shipped example, not hand-computed.
INSTALL_SCRIPT = (
    "sh -c 'mv -f /tmp/limits.conf /etc/security/limits.conf"
    " && chown root:root /etc/security/limits.conf"
    " && chmod 0644 /etc/security/limits.conf'"
)


def seeded_factory(entry: dict) -> SimulatedHost:
    """A host holding the fixture limits.conf (no spool yet)."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files = {LIMITS_FILE: LIMITS_CONTENT.encode()}
    host.file_stats = {LIMITS_FILE: {"mode": 644}}
    return host


# --- The pure transform: pyinfra files.line parity -----------------------------


def test_transform_pyinfra_fixture_appends_the_memlock_line():
    """pyinfra server.security_limit fixture parity: domain root, type
    hard, item memlock, value "unlimited", no matching line -> the
    tab-formatted ``echo 'root\\thard\\tmemlock\\tunlimited' >>`` append
    (the value is a string, as in the fixture)."""
    assert security_limit_edit("", "root", "hard", "memlock", "unlimited") == (
        "root\thard\tmemlock\tunlimited\n"
    )


def test_transform_of_an_absent_file_creates_the_line():
    """pyinfra's echo append creates an absent file; None content reads
    as empty here."""
    assert security_limit_edit(None, DOMAIN, LIMIT_TYPE, ITEM, VALUE) == LIMIT_LINE


def test_transform_appends_when_nothing_matches():
    assert security_limit_edit(LIMITS_CONTENT, DOMAIN, LIMIT_TYPE, ITEM, VALUE) == DESIRED


def test_transform_corrects_a_stale_value_in_place():
    """pyinfra parity: the value is NOT part of the pattern, so a stale
    matching line is replaced wholesale, not kept beside an append."""
    assert (
        security_limit_edit("* soft nofile 65535\n", DOMAIN, LIMIT_TYPE, ITEM, VALUE)
        == LIMIT_LINE
    )


def test_transform_normalizes_a_valueless_matching_line():
    """The pattern ends in item + ``.*$``: a line without a value still
    matches and gets the full replacement."""
    assert security_limit_edit("*   soft\tnofile\n", DOMAIN, LIMIT_TYPE, ITEM, VALUE) == (
        LIMIT_LINE
    )


def test_transform_replaces_every_matching_line():
    """pyinfra's sed replaces ALL matching lines, not just the first."""
    current = "* soft nofile 65535\n*\tsoft\tnofile\t999\n"
    assert security_limit_edit(current, DOMAIN, LIMIT_TYPE, ITEM, VALUE) == (
        "*\tsoft\tnofile\t1024\n*\tsoft\tnofile\t1024\n"
    )


def test_transform_keeps_a_commented_line_and_appends():
    """The anchored pattern does not match a commented-out line (unlike
    the unanchored sysctl-style match); pyinfra appends beside it."""
    assert security_limit_edit("# * soft nofile 1024\n", DOMAIN, LIMIT_TYPE, ITEM, VALUE) == (
        "# * soft nofile 1024\n" + LIMIT_LINE
    )


def test_transform_keeps_another_domain_line_alone():
    """The domain is part of the pattern: ``root soft nofile`` never
    matches the ``*`` request and survives."""
    assert security_limit_edit("root\tsoft\tnofile\t1024\n", DOMAIN, LIMIT_TYPE, ITEM, VALUE) == (
        "root\tsoft\tnofile\t1024\n" + LIMIT_LINE
    )


def test_transform_matches_an_item_prefix():
    """pyinfra's ``.*$`` tail: ``nofilex`` contains ``nofile`` at the
    match position and is normalized like any other match."""
    assert security_limit_edit("* soft nofilex 5\n", DOMAIN, LIMIT_TYPE, ITEM, VALUE) == (
        LIMIT_LINE
    )


def test_transform_fields_are_matched_literally():
    """The documented hardening: re.escape keeps the ``*`` domain
    literal, where pyinfra's raw BRE interpolation makes it a
    quantifier -- the near-miss line survives and the exact line
    appends."""
    assert security_limit_edit(". soft nofile 5\n", DOMAIN, LIMIT_TYPE, ITEM, VALUE) == (
        ". soft nofile 5\n" + LIMIT_LINE
    )


def test_transform_is_a_fixpoint_and_normalizes_the_trailing_newline():
    assert security_limit_edit(DESIRED, DOMAIN, LIMIT_TYPE, ITEM, VALUE) == DESIRED
    once = security_limit_edit(LIMITS_CONTENT.rstrip("\n"), DOMAIN, LIMIT_TYPE, ITEM, VALUE)
    assert once == DESIRED
    assert security_limit_edit(once, DOMAIN, LIMIT_TYPE, ITEM, VALUE) == once


def test_limit_line_is_tab_formatted():
    assert limit_line("root", "hard", "memlock", "unlimited") == (
        "root\thard\tmemlock\tunlimited"
    )


# --- The spool transform factory ----------------------------------------------


def test_make_transform_freezes_desired_content_and_is_fixpoint():
    transform = make_transform(DESIRED)
    assert transform(None) == DESIRED
    assert transform("stale spool text") == DESIRED
    assert transform(transform(None)) == transform(None)


# --- The demo-side install operation -------------------------------------------


async def test_limits_install_renders_the_elevated_sh_install():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd async for cmd in limits_install(ctx, SPOOL_PATH, LIMITS_FILE)
    ]

    assert ctx.changed
    assert ctx.reason == f"will install {SPOOL_PATH} over {LIMITS_FILE}"
    (cmd,) = commands
    assert isinstance(cmd, StringCommand)
    assert command_shell(cmd) == INSTALL_SCRIPT
    assert command_shell(cmd, sudo=True) == f"sudo {INSTALL_SCRIPT}"


async def test_limits_install_kwargs_absorb_pyinfra_globals():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in limits_install(
            ctx, SPOOL_PATH, LIMITS_FILE, _pyinfra_global="ignored"
        )
    ]
    assert commands == [StringCommand(INSTALL_SCRIPT)]


async def test_limits_install_is_a_no_op_on_the_simulated_host():
    """The seam accepts a raw StringCommand without effect, like the
    sysctl/locale recipe steps: the simulated target file stays
    untouched while the render proves the real command."""
    host = SimulatedHost(name="h", remote_files={LIMITS_FILE: LIMITS_CONTENT.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    (cmd,) = [c async for c in limits_install(ctx, SPOOL_PATH, LIMITS_FILE)]
    await send_command(host, cmd)
    assert host.remote_files == {LIMITS_FILE: LIMITS_CONTENT.encode()}


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
    commands = [cmd async for cmd in files.edit(ctx, SPOOL_PATH, make_transform(DESIRED))]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({SPOOL_PATH})"
    assert commands == []


# --- End to end: the one-deploy recipe ------------------------------------------


async def test_full_recipe_plans_and_applies_both_steps():
    plans, report, hosts = await run(
        INVENTORY, security_limit_example.deploy, host_factory=seeded_factory
    )

    spool_plan, install_plan = plans["web1"]
    assert spool_plan.changed
    assert install_plan.changed
    plan = format_plan(plans)
    assert f"content {SPOOL_PATH} is absent" in plan
    assert "will install /tmp/limits.conf" in plan

    assert report is not None
    log = format_log(report)
    assert "$ scp <stream> -> /tmp/limits.conf" in log
    assert f"$ sudo {INSTALL_SCRIPT}" in log
    assert [entry.name for entry in report.log] == [
        "Spool limits.conf",
        "Install limits.conf",
    ]
    # The spool really holds the edited content; the simulated raw
    # StringCommand has no in-memory effect, so the target stays as seeded.
    assert hosts[0].remote_files[SPOOL_PATH] == DESIRED.encode()
    assert hosts[0].remote_files[LIMITS_FILE] == LIMITS_CONTENT.encode()


async def test_lockstep_across_two_hosts():
    _plans, report, hosts = await run(
        TWO_HOSTS, security_limit_example.deploy, host_factory=seeded_factory
    )
    assert report is not None
    assert [entry.name for entry in report.log] == [
        "Spool limits.conf",
        "Spool limits.conf",
        "Install limits.conf",
        "Install limits.conf",
    ]
    assert len(hosts) == 2
    for host in hosts:
        assert host.remote_files[SPOOL_PATH] == DESIRED.encode()


async def test_dry_run_sends_nothing():
    plans, report, hosts = await run(
        INVENTORY,
        security_limit_example.deploy,
        dry=True,
        host_factory=seeded_factory,
    )
    assert report is None
    assert plans["web1"][0].changed
    assert SPOOL_PATH not in hosts[0].remote_files


def test_script_level_rerun_gate_closes():
    """The controller-side idempotence of main(): once the file carries
    the line, the transform is a fixpoint and the host is skipped -- no
    deploy runs at all."""
    current = security_limit_edit(LIMITS_CONTENT, DOMAIN, LIMIT_TYPE, ITEM, VALUE)
    assert current == DESIRED
    again = security_limit_edit(current, DOMAIN, LIMIT_TYPE, ITEM, VALUE)
    assert again == current  # the gate compares equal -> pending list empty


# --- The determinism constraint on the transform --------------------------------


async def test_factory_call_inside_the_deploy_raises_determinism_error():
    """A transform built fresh in the deploy generator has a new identity
    each phase, so the apply-phase request diverges from the prepare
    plan -- the factory must be called once, outside the generator."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool limits.conf",
            path=SPOOL_PATH,
            transform=make_transform(DESIRED),
        )

    with pytest.raises(DeterminismError):
        await run(INVENTORY, deploy, host_factory=seeded_factory)
