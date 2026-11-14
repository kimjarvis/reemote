# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.edit``: the generic content-transform primitive.

``files.edit`` reads a remote file's text with the ``FileContents`` fact,
applies a caller-supplied pure ``transform``, and writes the result back with a
``PutCommand`` only when it differs -- the reusable building block for
per-format edits without a dedicated operation (the ``/etc/hosts`` case now
has one: ``server.etc_hosts``, ported as a spool-and-install operation).

Covers the ``FileContents`` fact over both transport paths (the real-transport
marker/``cat`` form and the ``SimulatedHost`` ``remote_files`` seam), the
operation's content-equality idempotence (rewrite, create-when-absent, no-op at
a transform fixpoint), the non-shell ``PutCommand`` it yields, end-to-end
``run``/plan/log behaviour including the second-edit no-op that the fresh
per-step ``FactCache`` enables, an ``/etc/hosts``-style transform proving the
motivating use case, and the ``DeterminismError`` a transform built fresh
inside the deploy generator would trip.
"""

import io
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import (
    DeterminismError,
    format_log,
    format_plan,
    gather_fact,
    run,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import (
    FileContents,
    PutCommand,
    read_source_bytes,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

REMOTE_PATH = "/tmp/reemote-edit.txt"


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout


class CannedFallbackHost(Host):
    """Canned stdout for both read tiers, recording the elevated calls.

    ``run_shell`` answers the unprivileged read and ``run_shell_privileged``
    the fallback re-read, so a test can prove both that the fallback fires
    for an unreadable file and that it never fires for a readable one.
    """

    def __init__(self, stdout: str, privileged_stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self._privileged_stdout = privileged_stdout
        self.privileged_calls: list[tuple[str, bool]] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout

    async def run_shell_privileged(
        self, shell: str, *, sudo: bool = False, su: bool = False, doas: bool = False
    ) -> str:
        self.privileged_calls.append((shell, sudo))
        return self._privileged_stdout


# --- Module-level transforms (stable, hashable identity across phases) --------


def append_marker(current: str | None) -> str:
    """Idempotent transform: ensure a single managed marker line is present."""
    text = current or ""
    lines = text.splitlines()
    if "# managed by reemote" in lines:
        return text
    lines.append("# managed by reemote")
    return "\n".join(lines) + "\n"


def identity(current: str | None) -> str:
    """A fixpoint transform: returns the content unchanged ("" when absent)."""
    return current or ""


def add_db_host(current: str | None) -> str:
    """An ``/etc/hosts``-style edit: key one line by IP, leaving others intact.

    Mirrors what the ported ``server.etc_hosts`` operation does, expressed
    purely as a transform: drop any existing line for the IP, append the
    desired one, keep every other line. Idempotent, so a second run is a no-op.
    """
    text = current or ""
    kept = [line for line in text.splitlines() if not line.startswith("10.0.0.5 ")]
    kept.append("10.0.0.5 db.internal db")
    return "\n".join(kept) + "\n"


# --- The FileContents fact: real transport -----------------------------------


async def test_file_contents_parses_the_marked_real_transport_output():
    """Over the real transport the fact strips the marker and returns content."""
    host = CannedShellHost(f"{FileContents.MARKER}\nline one\nline two\n")
    assert await FileContents.process(host, "/etc/hosts") == "line one\nline two\n"


async def test_file_contents_absent_without_the_marker():
    """A missing file produces no marker, so the fact reports None, not ""."""
    host = CannedShellHost("")
    assert await FileContents.process(host, "/etc/hosts") is None


async def test_file_contents_present_but_empty_returns_empty_string():
    """An existing empty file leads with the marker and no content -> ""."""
    host = CannedShellHost(f"{FileContents.MARKER}\n")
    assert await FileContents.process(host, "/etc/hosts") == ""


def test_file_contents_command_guards_on_existence():
    """The command cats the file only behind a ``test -e`` guard and leads with
    the marker, so absence stays distinguishable from empty."""
    command = FileContents.command(REMOTE_PATH)
    assert command.startswith(f"test -e {REMOTE_PATH}")
    assert FileContents.MARKER in command
    assert f"cat -- {REMOTE_PATH}" in command


def test_file_contents_command_marks_a_failed_cat():
    """The cat's failure arm prints the UNREADABLE marker, so a present but
    unreadable file stays distinguishable from an empty one on stdout alone."""
    command = FileContents.command(REMOTE_PATH)
    assert (
        f"cat -- {REMOTE_PATH} || printf '{FileContents.UNREADABLE}\\n'" in command
    )


async def test_file_contents_falls_back_to_an_elevated_read():
    """A present-but-unreadable file (marker + UNREADABLE line) triggers one
    elevated re-read with sudo, so a 0640 service-owned file reads its real
    content instead of appearing empty."""
    host = CannedFallbackHost(
        f"{FileContents.MARKER}\n{FileContents.UNREADABLE}\n",
        f"{FileContents.MARKER}\nsecret = true\n",
    )
    assert await FileContents.process(host, "/etc/app.conf") == "secret = true\n"
    assert host.privileged_calls == [(FileContents.command("/etc/app.conf"), True)]


async def test_file_contents_readable_file_never_elevates():
    """A successful unprivileged cat returns the content directly -- the
    fallback is for unreadable files only."""
    host = CannedFallbackHost(f"{FileContents.MARKER}\nhello\n", "unused")
    assert await FileContents.process(host, "/etc/app.conf") == "hello\n"
    assert host.privileged_calls == []


async def test_file_contents_empty_file_never_elevates():
    """Marker-only stdout is an empty file, not an unreadable one: it returns
    the empty string without an elevated re-read."""
    host = CannedFallbackHost(f"{FileContents.MARKER}\n", "unused")
    assert await FileContents.process(host, "/etc/app.conf") == ""
    assert host.privileged_calls == []


async def test_file_contents_absent_file_never_elevates():
    """No marker means absence: None, with no elevated re-read."""
    host = CannedFallbackHost("", "unused")
    assert await FileContents.process(host, "/etc/app.conf") is None
    assert host.privileged_calls == []


# --- The FileContents fact: simulated seam -----------------------------------


async def test_file_contents_reads_the_simulated_seam():
    """A SimulatedHost stores content in ``remote_files``; the fact decodes it
    back, and an absent path reads as None."""
    host = SimulatedHost(name="h", remote_files={REMOTE_PATH: b"hello\n"})
    assert await FileContents.process(host, REMOTE_PATH) == "hello\n"
    assert await FileContents.process(host, "/absent") is None


async def test_file_contents_seam_falls_back_for_an_unreadable_file():
    """The mode-aware seam: a 0640 root:dd-agent file the login user cannot
    read returns the unreadable sentinel unprivileged, and ``process``'s
    shared fallback re-reads it elevated -- offline, exactly as over SSH.
    Removing the fallback would leak the sentinel and fail this test."""
    host = SimulatedHost(
        name="h",
        connection={"username": "kim"},
        remote_files={REMOTE_PATH: b"secret = true\n"},
    )
    host.file_stats[REMOTE_PATH] = {
        "user": "root",
        "group": "dd-agent",
        "mode": 640,
        "size": len(b"secret = true\n"),
    }
    assert not host.login_can_read(REMOTE_PATH)
    assert await FileContents.process(host, REMOTE_PATH) == "secret = true\n"


async def test_file_contents_seam_reads_a_group_member_directly():
    """A login user in the file's group reads through the group bit -- the
    unprivileged seam read succeeds and no fallback is needed."""
    host = SimulatedHost(
        name="h",
        connection={"username": "kim"},
        remote_files={REMOTE_PATH: b"secret = true\n"},
    )
    host.os_users["kim"] = {"group": "kim", "groups": ["dd-agent"]}
    host.file_stats[REMOTE_PATH] = {
        "user": "root",
        "group": "dd-agent",
        "mode": 640,
        "size": len(b"secret = true\n"),
    }
    assert host.login_can_read(REMOTE_PATH)
    assert await FileContents.process(host, REMOTE_PATH) == "secret = true\n"


def test_login_can_read_models_the_posix_read_bits():
    """Owner, group (primary plus supplementary) and other read bits in POSIX
    order; root bypasses the check; a path with no metadata row reads as
    readable (the legacy content-only seam)."""
    host = SimulatedHost(name="h", connection={"username": "kim"})
    path = "/etc/app.conf"
    assert host.login_can_read(path)  # no row: legacy login-readable seam

    host.file_stats[path] = {"user": "root", "group": "dd-agent", "mode": 640}
    assert not host.login_can_read(path)  # other: 0640 has no other-read bit

    host.os_users["kim"] = {"group": "kim", "groups": ["dd-agent"]}
    assert host.login_can_read(path)  # supplementary group membership

    host.os_users["kim"] = {"group": "dd-agent", "groups": []}
    assert host.login_can_read(path)  # primary group membership

    host.file_stats[path] = {"user": "root", "group": "root", "mode": 600}
    assert not host.login_can_read(path)  # owner-only mode, kim is not root

    host.file_stats[path] = {"user": "kim", "group": "root", "mode": 600}
    assert host.login_can_read(path)  # the owner read bit

    host.file_stats[path] = {"user": "root", "group": "root", "mode": 644}
    assert host.login_can_read(path)  # the other read bit

    root = SimulatedHost(name="r", connection={"username": "root"})
    root.file_stats[path] = {"user": "root", "group": "root", "mode": 600}
    assert root.login_can_read(path)  # root bypasses the check


async def test_gather_fact_reads_file_contents_across_hosts():
    host = SimulatedHost.from_inventory(INVENTORY[0])
    host.remote_files[REMOTE_PATH] = b"seeded\n"
    values = await gather_fact([host], FileContents, REMOTE_PATH)
    assert values["web1"] == "seeded\n"


# --- The operation: content-equality idempotence -----------------------------


async def test_edit_rewrites_when_the_transform_changes_the_content():
    host = SimulatedHost(name="h", remote_files={REMOTE_PATH: b"original\n"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.edit(ctx, REMOTE_PATH, append_marker)]

    assert ctx.changed
    assert ctx.reason == f"content {REMOTE_PATH} differs from the transform, will rewrite"
    (put,) = commands
    assert isinstance(put, PutCommand)
    assert put.dest == REMOTE_PATH
    assert read_source_bytes(put.src).decode() == "original\n# managed by reemote\n"


async def test_edit_creates_an_absent_file():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.edit(ctx, REMOTE_PATH, append_marker)]

    assert ctx.changed
    assert ctx.reason == (
        f"content {REMOTE_PATH} is absent, will create from the transform"
    )
    (put,) = commands
    assert read_source_bytes(put.src).decode() == "# managed by reemote\n"


async def test_edit_is_a_no_op_at_a_transform_fixpoint():
    """Content already equal to its transform yields nothing and no change."""
    host = SimulatedHost(
        name="h", remote_files={REMOTE_PATH: b"# managed by reemote\n"}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.edit(ctx, REMOTE_PATH, append_marker)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({REMOTE_PATH})"
    assert commands == []


# --- End to end through the driver -------------------------------------------


def edit_deploy(path: str = REMOTE_PATH):
    """One edit, then a second edit of the same path with the same transform.

    The transform is a module-level function, so both phases -- and both
    requests -- carry the identical callable, keeping the prepare and apply
    request sequences equal (``OperationRequest`` compares it by value).
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.edit, name="Edit file", path=path, transform=append_marker)
        yield op(files.edit, name="Re-edit file", path=path, transform=append_marker)

    return deploy


async def test_second_edit_is_a_no_op_in_the_apply_log():
    """The prepare plan materializes both edits against pre-deployment state, so
    both show changed; only the apply log -- where the ``FactCache`` is
    recreated per lockstep step -- shows the second edit reading back what the
    first wrote and reporting no change."""
    plans, report, hosts = await run(
        INVENTORY, edit_deploy(), host_factory=SimulatedHost.from_inventory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the file was absent for both
    assert f"content {REMOTE_PATH} is absent" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Edit file"].changed
    assert not entries["Re-edit file"].changed
    assert entries["Re-edit file"].reason == (
        f"content already up to date ({REMOTE_PATH})"
    )

    # The upload landed in the simulated remote filesystem.
    assert hosts[0].remote_files == {REMOTE_PATH: b"# managed by reemote\n"}

    log = format_log(report)
    assert f"$ scp <stream> -> {REMOTE_PATH}" in log


async def test_edit_of_existing_content_rewrites_once_then_settles():
    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.remote_files = {REMOTE_PATH: b"seed line\n"}
        return host

    _plans, report, hosts = await run(
        INVENTORY, edit_deploy(), host_factory=factory
    )
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Edit file"].changed
    assert not entries["Re-edit file"].changed
    assert hosts[0].remote_files == {
        REMOTE_PATH: b"seed line\n# managed by reemote\n"
    }


async def test_edit_hosts_style_transform():
    """The motivating use case: an ``/etc/hosts`` entry managed purely as a
    transform, with no bespoke operation -- idempotent across two edits."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.edit, name="Add db host", path="/etc/hosts", transform=add_db_host)
        yield op(files.edit, name="Re-add db host", path="/etc/hosts", transform=add_db_host)

    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.remote_files = {"/etc/hosts": b"127.0.0.1 localhost\n"}
        return host

    _plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Add db host"].changed
    assert not entries["Re-add db host"].changed
    assert hosts[0].remote_files == {
        "/etc/hosts": b"127.0.0.1 localhost\n10.0.0.5 db.internal db\n"
    }


# --- The driver: non-shell dispatch ------------------------------------------


async def test_send_command_dispatches_the_edit_put_to_the_seam():
    """The ``PutCommand`` an edit yields writes through the simulated seam."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    stream = io.StringIO("# managed by reemote\n")
    await send_command(host, PutCommand(src=stream, dest=REMOTE_PATH))
    assert host.remote_files == {REMOTE_PATH: b"# managed by reemote\n"}


# --- The determinism constraint on the transform -----------------------------


async def test_transform_built_inside_the_deploy_raises_determinism_error():
    """A lambda created fresh in the deploy generator has a new identity each
    phase, so the apply-phase request diverges from the prepare plan -- exactly
    the constraint that a stream object must be module-level for put/get."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Edit file",
            path=REMOTE_PATH,
            transform=lambda current: (current or "") + "# managed\n",
        )

    with pytest.raises(DeterminismError):
        await run(INVENTORY, deploy, host_factory=SimulatedHost.from_inventory)
