# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.get``: sha1-based idempotent download into an IO-like dest.

The mirror of ``test_files_put.py``: the destination is always a seekable,
writable IO-like object on the control machine -- a local filename is
deliberately not supported. Covers the content-hash comparison (remote
``RemoteFileSha1`` vs ``LocalSha1`` of the destination's current bytes), the
non-shell ``GetCommand`` descriptor and its loggable rendering, the
``send_command`` dispatch onto the host's ``download_file`` seam, the
write-and-rewind rule (``write_dest_bytes``) for binary and text streams, the
"download blind" plan when the remote source is absent, and the
``SimulatedHost`` ``remote_files`` content store that makes a repeat get a
no-op across a fresh per-step ``FactCache`` -- plus a put-then-get round trip
through that single store.
"""

import hashlib
import io
from collections.abc import Generator

import pytest

from reemote5.core import FactCache, OpContext, OperationRequest, op
from reemote5.drivers import format_log, format_plan, gather_fact, run, send_command
from reemote5.hosts import Host
from reemote5.modules import files
from reemote5.modules.files.files import GetCommand, LocalSha1, write_dest_bytes
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

REMOTE_SRC = "/home/kjarvis/reemote5-get.txt"

CONTENT = b"hello from the remote host\n"
CONTENT_SHA1 = hashlib.sha1(CONTENT).hexdigest()


@pytest.fixture
def dest_stream():
    """An empty IO-like destination on the control machine."""
    return io.BytesIO()


def seeded_host(entry: dict) -> SimulatedHost:
    """A host factory whose remote filesystem already holds CONTENT at src."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files[REMOTE_SRC] = CONTENT
    return host


def get_deploy(dest, src: str = REMOTE_SRC):
    """Deploy shape used below: one get of the remote ``src`` into ``dest``.

    The stream is captured once and reused by every ``deploy(host)``
    invocation, which is what keeps the prepare and apply request sequences
    equal: ``OperationRequest`` compares an IO object by identity,
    so a deploy must pass the *same* stream in both phases (define it outside
    the generator, as here).
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.get, name="Download message", src=src, dest=dest)

    return deploy


# --- The operation: remote sha1 vs the destination's sha1 -----------------------


async def test_get_downloads_when_dest_is_empty(dest_stream):
    plans, report, hosts = await run(
        INVENTORY, get_deploy(dest_stream), host_factory=seeded_host
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert planned.reason == (
        f"local copy missing or differs from remote {REMOTE_SRC}"
    )
    assert planned.commands == (GetCommand(src=REMOTE_SRC, dest=dest_stream),)

    # The simulated scp wrote the remote content into the destination stream.
    assert dest_stream.getvalue() == CONTENT
    assert hosts[0].remote_files == {REMOTE_SRC: CONTENT}

    assert f"local copy missing or differs from remote {REMOTE_SRC}" in format_plan(
        plans
    )
    assert report is not None
    log = format_log(report)
    assert f"$ scp {REMOTE_SRC} -> <stream>" in log


async def test_repeat_get_is_a_no_op(dest_stream):
    """A second run against the *same* host and destination plans no change:
    the first download filled ``dest``, so the fresh fact cache reads a
    matching hash back -- and the identical request sequence (same stream
    object, compared by identity) passes the determinism guard."""
    shared = seeded_host(INVENTORY[0])

    def factory(entry: dict) -> SimulatedHost:
        return shared

    deploy = get_deploy(dest_stream)
    first_plans, _, _ = await run(INVENTORY, deploy, host_factory=factory)
    assert first_plans["web1"][0].changed
    assert dest_stream.getvalue() == CONTENT

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    (planned,) = plans["web1"]
    assert not planned.changed
    assert planned.reason == f"local copy already up to date (from {REMOTE_SRC})"
    assert planned.commands == ()
    assert report is not None
    assert not report.changed
    assert not report.log[0].changed
    assert hosts[0] is shared


async def test_second_get_in_the_same_deploy_is_a_no_op(dest_stream):
    """Within one run, the second get's fresh apply-phase FactCache sees the
    bytes the first get downloaded -- while the prepare plan, materialized
    against pre-deployment state (an empty destination), reports both."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.get, name="Download message", src=REMOTE_SRC, dest=dest_stream)
        yield op(
            files.get, name="Re-download message", src=REMOTE_SRC, dest=dest_stream
        )

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=seeded_host
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the dest was empty for both
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Download message"].changed
    # The apply phase is where the per-step cache recreation shows: the second
    # get reads back the content the first get wrote into the destination.
    assert not entries["Re-download message"].changed
    assert (
        entries["Re-download message"].reason
        == f"local copy already up to date (from {REMOTE_SRC})"
    )
    assert dest_stream.getvalue() == CONTENT
    assert len(hosts[0].remote_files) == 1


async def test_get_redownloads_when_dest_content_differs(dest_stream):
    """Stale destination content (drift) is a change, exactly like emptiness."""
    dest_stream.write(b"stale local content that never matches\n")

    plans, report, hosts = await run(
        INVENTORY, get_deploy(dest_stream), host_factory=seeded_host
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert report is not None
    # The download replaced the stale bytes with the remote content.
    assert dest_stream.getvalue() == CONTENT
    assert hosts[0].remote_files == {REMOTE_SRC: CONTENT}


async def test_get_plans_a_download_when_remote_is_absent(dest_stream):
    """Like pyinfra, an absent remote source still plans a download (there is
    no local copy to match); against a real host the scp then fails if the file
    truly is not there. The simulated get leaves the empty dest untouched."""

    def factory(entry: dict) -> SimulatedHost:
        return SimulatedHost.from_inventory(entry)  # remote_files empty

    plans, report, hosts = await run(
        INVENTORY, get_deploy(dest_stream), host_factory=factory
    )

    (planned,) = plans["web1"]
    assert planned.changed
    assert planned.commands == (GetCommand(src=REMOTE_SRC, dest=dest_stream),)
    assert report is not None
    assert dest_stream.getvalue() == b""
    assert hosts[0].remote_files == {}


# --- The destination: written and rewound, binary or text -----------------------


async def test_local_sha1_of_an_empty_dest(dest_stream):
    """An empty destination hashes as the sha1 of no bytes, so it never matches
    a present remote file and the first get always downloads."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    values = await gather_fact([host], LocalSha1, dest_stream)
    assert values["web1"] == hashlib.sha1(b"").hexdigest()


async def test_write_dest_bytes_replaces_and_rewinds():
    """The shared writing rule: truncate the old content, write, rewind."""
    sink = io.BytesIO(b"old, much longer content to be replaced")
    write_dest_bytes(sink, CONTENT)
    assert sink.getvalue() == CONTENT
    assert sink.tell() == 0  # rewound so the caller reads from the start


async def test_write_dest_bytes_decodes_for_a_text_stream():
    """A text stream receives the bytes decoded as UTF-8 (read_source_bytes'
    inverse), so a later LocalSha1 re-encodes to the identical digest."""
    text = io.StringIO()
    write_dest_bytes(text, CONTENT)
    assert text.getvalue() == CONTENT.decode("utf-8")
    assert hashlib.sha1(text.getvalue().encode("utf-8")).hexdigest() == CONTENT_SHA1


async def test_named_dest_renders_its_name_in_the_log(tmp_path):
    """A real file object carries ``name``, so the rendering shows the path and
    the downloaded bytes land in the file."""
    path = tmp_path / "sink.bin"
    with path.open("w+b") as handle:
        plans, _, _ = await run(
            INVENTORY, get_deploy(handle), host_factory=seeded_host
        )
    (planned,) = plans["web1"]
    assert str(planned.commands[0]) == f"scp {REMOTE_SRC} -> {path}"
    assert path.read_bytes() == CONTENT


# --- The driver: non-shell dispatch ---------------------------------------------


async def test_send_command_dispatches_get_to_execute_command_seam(dest_stream):
    host = seeded_host(INVENTORY[0])
    cmd = GetCommand(src=REMOTE_SRC, dest=dest_stream)
    await send_command(host, cmd)
    assert dest_stream.getvalue() == CONTENT


async def test_send_command_dispatches_transfer_to_download_file(dest_stream):
    """Without the simulated seam, a TransferCommand rides ``download_file``."""

    class DownloadOnlyHost(Host):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.downloads: list[tuple[str, object]] = []

        async def download_file(self, remote: str, dest) -> None:
            self.downloads.append((remote, dest))

    host = DownloadOnlyHost(name="web1")
    cmd = GetCommand(src=REMOTE_SRC, dest=dest_stream)
    await send_command(host, cmd)
    assert host.downloads == [(REMOTE_SRC, dest_stream)]


async def test_download_file_without_address_raises_not_implemented(dest_stream):
    """The pytest boundary: a plain Host with no address never connects."""
    host = Host.from_inventory(INVENTORY[0])
    with pytest.raises(NotImplementedError):
        await host.download_file(REMOTE_SRC, dest_stream)


async def test_get_command_materializes_through_the_op_context(dest_stream):
    """``get`` yields the descriptor only when the hashes differ."""
    host = seeded_host(INVENTORY[0])
    cache = FactCache(host, phase="prepare")
    ctx = OpContext(host=host, facts=cache)
    commands = [cmd async for cmd in files.get(ctx, REMOTE_SRC, dest_stream)]
    assert commands == [GetCommand(src=REMOTE_SRC, dest=dest_stream)]
    assert ctx.changed
    assert ctx.reason == (
        f"local copy missing or differs from remote {REMOTE_SRC}"
    )


# --- Composition: one content store backs both directions -----------------------


async def test_put_then_get_round_trips(dest_stream):
    """A put writes the source stream into the simulated remote filesystem and
    a following get reads the very same bytes back into another stream -- the
    single ``remote_files`` content store makes the round trip exact."""
    source = io.BytesIO(CONTENT)

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.put, name="Upload", src=source, dest=REMOTE_SRC)
        yield op(files.get, name="Download", src=REMOTE_SRC, dest=dest_stream)

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )

    assert report is not None
    # The apply phase ran the put (filling remote_files) then the get (draining
    # it into dest), each against a fresh per-step fact cache.
    assert hosts[0].remote_files == {REMOTE_SRC: CONTENT}
    assert dest_stream.getvalue() == CONTENT
    assert {entry.name: entry.changed for entry in report.log} == {
        "Upload": True,
        "Download": True,
    }
    assert len(plans["web1"]) == 2
