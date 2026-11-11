# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.put``: sha1-based idempotent transfer of IO-like sources.

The source is always a seekable IO-like object on the control machine -- a
path string is deliberately not supported (unlike the baseline's ``str | IO[Any]``
signature). Covers the content-hash comparison (the stream's locally computed
sha1 vs remote ``RemoteFileSha1``), the non-shell ``PutCommand`` descriptor and
its loggable rendering, the ``send_command`` dispatch onto the host's
``upload_file`` seam, the rewind-and-normalize reading rule for repeated
materializations, and the ``SimulatedHost`` ``remote_files`` state that makes a
repeat put a no-op across a fresh per-step ``FactCache``.
"""

import hashlib
import io
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import format_log, format_plan, run, send_command
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import (
    PutCommand,
    RemoteFileSha1,
    _sha1_of_source,
    read_source_bytes,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

REMOTE_DEST = "/tmp/reemote-put.txt"

CONTENT = b"hello from reemote\n"
CONTENT_SHA1 = hashlib.sha1(CONTENT).hexdigest()


@pytest.fixture
def src_stream():
    """An IO-like source on the control machine with known content."""
    return io.BytesIO(CONTENT)


def put_deploy(src, dest: str = REMOTE_DEST):
    """Deploy shape used below: one put of the given stream.

    The stream is captured once and reused by every ``deploy(host)``
    invocation, which is what keeps the prepare and apply request sequences
    equal: ``OperationRequest`` compares an IO object by
    identity, so a deploy must pass the *same* stream in both phases (define
    it outside the generator, as here).
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.put, name="Upload message", src=src, dest=dest)

    return deploy


# --- The operation: sha1 vs sha1 ------------------------------------------------


async def test_put_uploads_when_remote_is_absent(src_stream):
    plans, report, hosts = await run(
        INVENTORY, put_deploy(src_stream), host_factory=SimulatedHost.from_inventory
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert planned.reason == (
        f"file {REMOTE_DEST} missing or differs from the local source"
    )
    assert planned.commands == (PutCommand(src=src_stream, dest=REMOTE_DEST),)

    # The simulated scp stored the source content at the remote path.
    assert hosts[0].remote_files == {REMOTE_DEST: CONTENT}

    assert f"file {REMOTE_DEST} missing or differs" in format_plan(plans)
    assert report is not None
    log = format_log(report)
    assert f"$ scp <stream> -> {REMOTE_DEST}" in log


async def test_repeat_put_is_a_no_op(src_stream):
    """A second run against the *same* host state plans no change: the upload
    mutated ``remote_files``, so the fresh fact cache reads the matching hash
    back -- and the identical request sequence (same stream object, compared
    by identity) passes the determinism guard."""
    shared = SimulatedHost.from_inventory(INVENTORY[0])

    def factory(entry: dict) -> SimulatedHost:
        return shared

    deploy = put_deploy(src_stream)
    first_plans, _, _ = await run(INVENTORY, deploy, host_factory=factory)
    assert first_plans["web1"][0].changed

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    (planned,) = plans["web1"]
    assert not planned.changed
    assert planned.reason == f"file already up to date ({REMOTE_DEST})"
    assert planned.commands == ()
    assert report is not None
    assert not report.changed
    assert not report.log[0].changed
    assert hosts[0] is shared


async def test_second_put_in_the_same_deploy_is_a_no_op(src_stream):
    """Within one run, the second put's fresh apply-phase FactCache sees the
    first put's upload -- while the prepare plan, materialized against
    pre-deployment state, still reports both as changed."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.put, name="Upload message", src=src_stream, dest=REMOTE_DEST)
        yield op(files.put, name="Re-upload message", src=src_stream, dest=REMOTE_DEST)

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the remote was absent for both
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Upload message"].changed
    # The apply phase is where the per-step cache recreation shows: the
    # second put reads back the hash the first put recorded.
    assert not entries["Re-upload message"].changed
    assert (
        entries["Re-upload message"].reason
        == f"file already up to date ({REMOTE_DEST})"
    )
    assert len(hosts[0].remote_files) == 1


async def test_put_reuploads_when_remote_content_differs(src_stream):
    """Stale remote content (drift) is a change, exactly like absence."""

    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.remote_files = {REMOTE_DEST: b"stale content that never matches\n"}
        return host

    plans, report, hosts = await run(
        INVENTORY, put_deploy(src_stream), host_factory=factory
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert report is not None
    assert hosts[0].remote_files == {REMOTE_DEST: CONTENT}


# --- The source: IO-like only, rewound and normalized -----------------------------


async def test_sha1_of_source_hashes_the_stream_content(src_stream):
    """The local hash reads the control-machine stream, not the host."""
    assert _sha1_of_source(src_stream) == CONTENT_SHA1


async def test_read_source_bytes_rewinds_and_normalizes():
    """The shared reading rule: rewind to 0, encode text as UTF-8."""
    stream = io.BytesIO(CONTENT)
    stream.read(5)  # a partially consumed stream still hashes fully
    assert read_source_bytes(stream) == CONTENT

    text = io.StringIO("hello from reemote\n")
    assert read_source_bytes(text) == CONTENT


async def test_repeated_materializations_hash_identically(src_stream):
    """Prepare and apply read the same object; each read must rewind first."""
    first = _sha1_of_source(src_stream)
    second = _sha1_of_source(src_stream)
    assert first == second == CONTENT_SHA1


async def test_text_stream_source_hashes_as_utf8():
    stream = io.StringIO("hello from reemote\n")
    assert _sha1_of_source(stream) == CONTENT_SHA1


async def test_named_stream_renders_its_name_in_the_log(tmp_path):
    """A real file object carries ``name``, so the rendering shows the path."""
    path = tmp_path / "message.txt"
    path.write_bytes(CONTENT)
    with path.open("rb") as handle:
        plans, _, _ = await run(
            INVENTORY, put_deploy(handle), host_factory=SimulatedHost.from_inventory
        )
    (planned,) = plans["web1"]
    assert str(planned.commands[0]) == f"scp {path} -> {REMOTE_DEST}"


async def test_remote_sha1_reads_the_remote_files_seam():
    host = SimulatedHost.from_inventory(INVENTORY[0])
    cache = FactCache(host, phase="prepare")
    assert await cache.get(RemoteFileSha1, REMOTE_DEST) is None
    host.remote_files[REMOTE_DEST] = CONTENT
    # A fresh cache re-reads the seam (the per-step recreation property) and
    # derives the digest from the stored content.
    cache = FactCache(host, phase="apply")
    assert await cache.get(RemoteFileSha1, REMOTE_DEST) == CONTENT_SHA1


# --- The driver: non-shell dispatch ----------------------------------------------


async def test_send_command_dispatches_put_to_execute_command_seam(src_stream):
    host = SimulatedHost.from_inventory(INVENTORY[0])
    cmd = PutCommand(src=src_stream, dest=REMOTE_DEST)
    await send_command(host, cmd)
    assert host.remote_files == {REMOTE_DEST: CONTENT}


async def test_send_command_dispatches_transfer_to_upload_file(src_stream):
    """Without the simulated seam, a TransferCommand rides ``upload_file``."""

    class UploadOnlyHost(Host):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.uploads: list[tuple[object, str]] = []

        async def upload_file(self, src, remote: str) -> None:
            self.uploads.append((src, remote))

    host = UploadOnlyHost(name="web1")
    cmd = PutCommand(src=src_stream, dest=REMOTE_DEST)
    await send_command(host, cmd)
    assert host.uploads == [(src_stream, REMOTE_DEST)]


async def test_upload_file_without_address_raises_not_implemented(src_stream):
    """The pytest boundary: a plain Host with no address never connects."""
    host = Host.from_inventory(INVENTORY[0])
    with pytest.raises(NotImplementedError):
        await host.upload_file(src_stream, REMOTE_DEST)


async def test_put_command_materializes_through_the_op_context(src_stream):
    """``put`` yields the descriptor only when the hashes differ."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    cache = FactCache(host, phase="prepare")
    ctx = OpContext(host=host, facts=cache)
    commands = [cmd async for cmd in files.put(ctx, src_stream, REMOTE_DEST)]
    assert commands == [PutCommand(src=src_stream, dest=REMOTE_DEST)]
    assert ctx.changed
    assert ctx.reason == (
        f"file {REMOTE_DEST} missing or differs from the local source"
    )
