"""Tests for the ``server.locale`` migration: a locale.gen recipe on files.edit.

pyinfra's ``server.locale`` is not ported as a bespoke operation -- the
developer chose the example-only recipe: the ``locale_edit`` function and
``make_transform`` factory in ``examples/modules/server/server_op_locale.py``
compute the desired ``/etc/locale.gen`` content from a ``FileContents`` +
``Locales`` snapshot (pyinfra's line lookup and change condition), the generic
``files.edit`` primitive spools it to a writable path, and an elevated
``files.move`` plus the demo-side ``locale_gen`` operation (a raw
``StringCommand``) install and regenerate. These tests import that code
straight from the example module, so the shipped demonstration is what is
verified.

Covers the pure edit against pyinfra's own operation-test fixtures (the
``add.json`` uncomment; ``remove.json``'s expected sed is stale upstream -- it
shows the add branch -- so the remove assertions follow the current pyinfra
source), the ``ValueError`` validations carrying pyinfra's ``OperationError``
messages, the ``Locales``-based skip gate, the ``files.edit`` materialization,
the two-run spool-and-install recipe end to end through ``SimulatedHost``
including lockstep ordering and ``locale-gen`` elevation, script-level
idempotence, and the ``DeterminismError`` a factory call inside the deploy
generator would trip.
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
    / "server_op_locale.py"
)
_spec = importlib.util.spec_from_file_location("server_op_locale_example", _example_path)
server_locale = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server_locale)
locale_edit = server_locale.locale_edit
locale_needed = server_locale.locale_needed
make_transform = server_locale.make_transform
locale_gen = server_locale.locale_gen

LOCALE = "fr_FR.UTF-8"
GEN_PATH = "/etc/locale.gen"
SPOOL_PATH = "/tmp/locale.gen"

# pyinfra's add.json fixture shape: the definition line commented out among
# unrelated lines, the locale absent from the Locales fact.
GEN_CONTENT = "# en_US.UTF-8 UTF-8\n# fr_FR.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n"
DESIRED_ADD = "# en_US.UTF-8 UTF-8\nfr_FR.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n"

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]
TWO_HOSTS = [
    *INVENTORY,
    {"host": "web2", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]

# Stable spool transforms for the end-to-end deploys, exactly as main() fills
# them between the snapshots and run 1 (the same callable objects in both
# phases -- the determinism guard compares them by value).
server_locale.SPOOL_TRANSFORMS["web1"] = make_transform(DESIRED_ADD)
server_locale.SPOOL_TRANSFORMS["web2"] = make_transform(DESIRED_ADD)


def seeded_factory(entry: dict) -> SimulatedHost:
    """A host holding the fixture locale.gen and pyinfra's add.json Locales."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files = {GEN_PATH: GEN_CONTENT.encode()}
    host.file_stats = {GEN_PATH: {"mode": 644}}
    host.directory_stats = {"/etc": {"mode": 755}}
    host.locales = ["C", "C.UTF-8", "en_US.UTF-8", "POSIX"]
    return host


# --- The pure edit: pyinfra fixture parity ------------------------------------


def test_add_uncomments_the_definition_line():
    """pyinfra add.json parity: '# fr_FR.UTF-8 UTF-8' becomes the enabled
    line, every other line preserved."""
    assert locale_edit(GEN_CONTENT, LOCALE, present=True) == DESIRED_ADD


def test_remove_comments_the_definition_line():
    current = "fr_FR.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n"
    assert locale_edit(current, LOCALE, present=False) == (
        "# fr_FR.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n"
    )


def test_remove_of_a_commented_line_double_comments():
    """pyinfra's remove branch prepends '# ' to the *matching* line verbatim
    (upstream remove.json's expected sed shows the add branch -- a stale
    fixture); the current source double-comments an already commented line.
    The Locales skip gate never schedules this twice."""
    assert locale_edit("# fr_FR.UTF-8 UTF-8\n", LOCALE, present=False) == (
        "# # fr_FR.UTF-8 UTF-8\n"
    )


def test_add_strips_every_comment_prefix():
    """pyinfra's `matching_line.replace("# ", "")` verbatim: a doubly
    commented line is fully enabled in one edit."""
    assert locale_edit("# # fr_FR.UTF-8 UTF-8\n", LOCALE, present=True) == (
        "fr_FR.UTF-8 UTF-8\n"
    )


def test_missing_locale_raises_pyinfra_operation_error_message():
    with pytest.raises(ValueError, match=r"Locale de_DE\.UTF-8 not found in /etc/locale\.gen"):
        locale_edit(GEN_CONTENT, "de_DE.UTF-8", present=True)


def test_absent_file_raises_the_same_not_found_error():
    """pyinfra's FindInFile reports an absent file as no matches, hitting the
    same OperationError; None content reads identically here."""
    with pytest.raises(ValueError, match="Locale fr_FR.UTF-8 not found"):
        locale_edit(None, LOCALE, present=True)


def test_multiple_matches_raise():
    current = "fr_FR.UTF-8 UTF-8\nfr_FR.UTF-8 ISO-8859-1\n"
    with pytest.raises(
        ValueError, match=r"Multiple locales matches for fr_FR\.UTF-8 in /etc/locale\.gen"
    ):
        locale_edit(current, LOCALE, present=True)


def test_match_requires_trailing_whitespace():
    """pyinfra's grep pattern demands whitespace after the locale name, so a
    bare name with no encoding column is not a definition line."""
    with pytest.raises(ValueError, match="not found"):
        locale_edit("fr_FR.UTF-8\n", LOCALE, present=True)


def test_locale_is_matched_literally():
    """The documented hardening: re.escape keeps the '.' literal, where
    pyinfra's raw BRE interpolation would match any character."""
    with pytest.raises(ValueError, match="not found"):
        locale_edit("fr_FRXUTF-8 UTF-8\n", LOCALE, present=True)


def test_other_lines_preserved_and_trailing_newline_normalized():
    current = GEN_CONTENT.rstrip("\n")
    assert locale_edit(current, LOCALE, present=True) == DESIRED_ADD


# --- The Locales skip gate (pyinfra's change condition) ------------------------


@pytest.mark.parametrize(
    ("installed", "present", "expected"),
    [
        (["C", "en_US.UTF-8"], True, True),  # enable: absent -> needed
        (["C", "fr_FR.UTF-8"], True, False),  # enable: present -> skip
        (["C", "fr_FR.UTF-8"], False, True),  # disable: present -> needed
        (["C", "en_US.UTF-8"], False, False),  # disable: absent -> skip
    ],
)
def test_locale_needed_mirrors_pyinfra(installed, present, expected):
    assert locale_needed(installed, LOCALE, present) is expected


# --- The spool transform factory ----------------------------------------------


def test_make_transform_freezes_desired_content_and_is_fixpoint():
    transform = make_transform(DESIRED_ADD)
    assert transform(None) == DESIRED_ADD
    assert transform("stale spool text") == DESIRED_ADD
    assert transform(transform(None)) == transform(None)


# --- The demo-side locale-gen operation ----------------------------------------


async def test_locale_gen_is_stateless_and_yields_locale_gen():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in locale_gen(ctx)]

    assert ctx.changed
    assert ctx.reason == "will regenerate the installed locales with locale-gen"
    assert commands == [StringCommand("locale-gen")]


def test_locale_gen_renders_plain_and_elevated():
    cmd = StringCommand("locale-gen")
    assert command_shell(cmd) == "locale-gen"
    assert command_shell(cmd, sudo=True) == "sudo locale-gen"


async def test_locale_gen_kwargs_absorb_pyinfra_globals():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in locale_gen(ctx, _pyinfra_global="ignored")]
    assert commands == [StringCommand("locale-gen")]


# --- Through files.edit: materialization --------------------------------------


async def test_edit_yields_a_put_for_the_absent_spool():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    transform = make_transform(DESIRED_ADD)
    commands = [cmd async for cmd in files.edit(ctx, SPOOL_PATH, transform)]

    assert ctx.changed
    assert ctx.reason == f"content {SPOOL_PATH} is absent, will create from the transform"
    (put,) = commands
    assert isinstance(put, PutCommand)
    assert put.dest == SPOOL_PATH
    assert read_source_bytes(put.src) == DESIRED_ADD.encode()


async def test_edit_is_a_no_op_when_the_spool_already_matches():
    host = SimulatedHost(name="h", remote_files={SPOOL_PATH: DESIRED_ADD.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    transform = make_transform(DESIRED_ADD)
    commands = [cmd async for cmd in files.edit(ctx, SPOOL_PATH, transform)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({SPOOL_PATH})"
    assert commands == []


# --- End to end: the two-run recipe --------------------------------------------


async def test_spool_run_plan_and_apply_log():
    plans, report, hosts = await run(
        INVENTORY, server_locale.spool, host_factory=seeded_factory
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert f"content {SPOOL_PATH} is absent" in format_plan(plans)

    assert report is not None
    assert "$ scp <stream> -> /tmp/locale.gen" in format_log(report)
    assert hosts[0].remote_files[SPOOL_PATH] == DESIRED_ADD.encode()
    # The root-owned original is untouched until run 2 installs the spool.
    assert hosts[0].remote_files[GEN_PATH] == GEN_CONTENT.encode()


async def test_full_recipe_spans_two_runs_and_regenerates():
    # Run 1: the edit creates the writable spool file (unprivileged scp).
    _plans, _report, hosts = await run(
        INVENTORY, server_locale.spool, host_factory=seeded_factory
    )
    host = hosts[0]
    # The simulated put stores content only; seed the stat row the real
    # filesystem would now have for the spool file.
    host.file_stats[SPOOL_PATH] = {"mode": 644}

    # Run 2: the elevated move installs it over the root-owned target, then
    # locale-gen regenerates. The factory returns the *same* simulated host
    # so run 1's state carries over.
    _plans, report, _hosts = await run(
        INVENTORY, server_locale.install, host_factory=lambda entry: host
    )
    assert report is not None
    log = format_log(report)
    assert "$ sudo rm -rf /etc/locale.gen" in log
    assert "$ sudo mv /tmp/locale.gen /etc" in log
    assert "$ sudo locale-gen" in log
    assert [entry.name for entry in report.log] == [
        "Install locale.gen",
        "Regenerate locales",
    ]
    assert host.remote_files == {GEN_PATH: DESIRED_ADD.encode()}
    assert SPOOL_PATH not in host.file_stats

    # The simulated StringCommand has no in-memory effect; emulate what
    # locale-gen did on a real host and prove the script-level skip gate
    # closes: a re-run snapshots, finds the locale installed, and skips.
    host.locales.append(LOCALE)
    assert locale_needed(host.locales, LOCALE, present=True) is False


async def test_install_lockstep_runs_every_move_before_any_locale_gen():
    def factory(entry: dict) -> SimulatedHost:
        host = seeded_factory(entry)
        # The spool already exists (run 1 happened): seed it directly.
        host.remote_files[SPOOL_PATH] = DESIRED_ADD.encode()
        host.file_stats[SPOOL_PATH] = {"mode": 644}
        return host

    _plans, report, hosts = await run(
        TWO_HOSTS, server_locale.install, host_factory=factory
    )
    assert report is not None
    # Step-major log: both hosts finish the move step before either
    # regenerates -- locale-gen never reads a half-installed fleet.
    assert [entry.name for entry in report.log] == [
        "Install locale.gen",
        "Install locale.gen",
        "Regenerate locales",
        "Regenerate locales",
    ]
    for host in hosts:
        assert host.remote_files == {GEN_PATH: DESIRED_ADD.encode()}


async def test_dry_run_sends_nothing():
    plans, report, hosts = await run(
        INVENTORY, server_locale.spool, dry=True, host_factory=seeded_factory
    )
    assert report is None
    assert plans["web1"][0].changed
    assert SPOOL_PATH not in hosts[0].remote_files


async def test_duplicate_spool_requests_plan_vs_apply():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool locale.gen",
            path=SPOOL_PATH,
            transform=server_locale.SPOOL_TRANSFORMS[host.name],
        )
        yield op(
            files.edit,
            name="Re-spool locale.gen",
            path=SPOOL_PATH,
            transform=server_locale.SPOOL_TRANSFORMS[host.name],
        )

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=seeded_factory
    )
    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Spool locale.gen"].changed
    assert not entries["Re-spool locale.gen"].changed


async def test_factory_call_inside_the_deploy_raises_determinism_error():
    """A transform built fresh in the deploy generator has a new identity each
    phase, so the apply-phase request diverges from the prepare plan -- the
    factory must be called once, outside the generator."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool locale.gen",
            path=SPOOL_PATH,
            transform=make_transform(DESIRED_ADD),
        )

    with pytest.raises(DeterminismError):
        await run(INVENTORY, deploy, host_factory=seeded_factory)


async def test_spool_and_install_in_one_deploy_raises_at_prepare():
    """The documented two-run constraint (files.move's always-raise rule):
    prepare never sends, so the spool the edit would create does not exist
    when the move materializes."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool locale.gen",
            path=SPOOL_PATH,
            transform=server_locale.SPOOL_TRANSFORMS[host.name],
        )
        yield op(
            files.move,
            name="Install locale.gen",
            src=SPOOL_PATH,
            dest="/etc",
            overwrite=True,
            sudo=True,
        )

    with pytest.raises(ValueError, match=r"src /tmp/locale\.gen does not exist"):
        await run(INVENTORY, deploy, host_factory=seeded_factory)