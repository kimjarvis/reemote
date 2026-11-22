# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline proofs of the ``SimulatedHost`` seams.

The action-transport seam (#16): ``SimulatedHost`` used to expose only the
fact/command seams, so a ``gather_action(..., Shell, ...)`` against one
reached the real asyncssh transport. The class now overrides
``_run_shell``: every shell is recorded verbatim in ``ran_shells`` and the
answer comes from the ``shell_results`` map keyed by the exact shell
string -- the command-keyed convention of ``find_results``/``grep_files``.
An unseeded command is a hard error (``UnseededShellResult``), so a new
action read can never silently pass.

Covered: a seeded ``Shell`` gather returns the canned record offline (no
socket, no asyncssh), an unseeded command raises ``UnseededShellResult``
naming the shell to seed, the recorder accumulates every shell verbatim
across gathers, a passwordless-elevated gather rides the same seam (its
``sudo`` rendering is the key), and ``Script`` -- which delegates to
``Shell.process`` -- answers from the seam keyed by its rendered
``sh -c`` wrapper.

The removal seams (#18): ``remove_file``/``remove_link`` clear the
``remote_files`` content store alongside the stat stores, mirroring
``remove_tree``, so a simulated ``rm -f`` stops ``FileContents`` and
``RemoteFileSha1`` answering for the path -- and a follow-up
content-idempotent ``files.install`` of the removed bytes is not wrongly
skipped.
"""

from collections.abc import Generator

import pytest

from reemote import Script, Shell, files, gather_action, gather_fact, op, run
from reemote.core import OperationRequest
from reemote.hosts import Host
from reemote.modules.files.files import FileContents, RemoteFileSha1
from tests.simulated_host import SimulatedHost, UnseededShellResult

PROBE = "/usr/sbin/apache2 -v"


def make_host(**privilege: str) -> SimulatedHost:
    """One simulated host, optionally carrying an inventory privilege block."""
    return SimulatedHost.from_inventory(
        {
            "host": "sim1",
            "family": "Ubuntu",
            "connection": {"address": "192.0.2.10"},
            "privilege": dict(privilege),
        }
    )


async def test_seeded_shell_gather_answers_offline():
    host = make_host()
    host.shell_results[PROBE] = {
        "exit_status": 0,
        "stdout": "Server version: Apache/2.4.58 (Ubuntu)\n",
        "stderr": "",
    }

    results = await gather_action([host], Shell, command=PROBE)

    # The seeded record comes back whole, as the library-owned Result.
    assert results["sim1"] == {
        "exit_status": 0,
        "stdout": "Server version: Apache/2.4.58 (Ubuntu)\n",
        "stderr": "",
    }
    assert host.ran_shells == [PROBE]


async def test_unseeded_command_is_a_hard_error():
    host = make_host()

    with pytest.raises(UnseededShellResult) as excinfo:
        await gather_action([host], Shell, command="bespoke-tool --version")

    # The error names the shell to seed and the seam to seed it in.
    message = str(excinfo.value)
    assert "bespoke-tool --version" in message
    assert "shell_results" in message
    assert host.ran_shells == ["bespoke-tool --version"]


async def test_recorder_accumulates_every_shell_verbatim():
    host = make_host()
    host.shell_results["first --probe"] = {
        "exit_status": 0,
        "stdout": "",
        "stderr": "",
    }
    host.shell_results["second --probe"] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "boom",
    }

    first = await gather_action([host], Shell, command="first --probe")
    second = await gather_action([host], Shell, command="second --probe")

    assert first["sim1"]["exit_status"] == 0
    assert second["sim1"]["exit_status"] == 1
    assert second["sim1"]["stderr"] == "boom"
    assert host.ran_shells == ["first --probe", "second --probe"]


async def test_sudo_gather_rides_the_same_seam():
    """The escalated rendering is the key: the sudo command, not the bare one."""
    host = make_host(sudo_user="root")
    host.shell_results["sudo id -u"] = {
        "exit_status": 0,
        "stdout": "root\n",
        "stderr": "",
    }

    results = await gather_action([host], Shell, command="id -u", sudo=True)

    assert results["sim1"]["stdout"] == "root\n"
    assert host.ran_shells == ["sudo id -u"]


async def test_script_rides_the_seam_via_its_rendered_wrapper():
    """Script delegates to Shell.process, so the key is its full sh -c wrapper.

    The failed first gather doubles as the workflow demo: the recorder shows
    the exact key to seed, the seed makes the second gather pass.
    """
    host = make_host()

    with pytest.raises(UnseededShellResult):
        await gather_action([host], Script, path="/tmp/setup.sh")

    wrapper = host.ran_shells[0]
    assert wrapper.startswith("sh -c ")
    host.shell_results[wrapper] = {"exit_status": 0, "stdout": "ran\n", "stderr": ""}

    results = await gather_action([host], Script, path="/tmp/setup.sh")

    assert results["sim1"]["stdout"] == "ran\n"
    assert host.ran_shells == [wrapper, wrapper]


# --- The removal seams clear the content store (#18) -------------------------

INVENTORY = [{"host": "sim1", "family": "Ubuntu", "connection": {"address": "192.0.2.10"}}]
REMOVED = "/etc/reemote-removed.conf"
REMOVED_CONTENT = "# managed by reemote\n"


def seed_content(host: SimulatedHost) -> None:
    """A root-owned regular file at REMOVED: bytes plus both stat seams."""
    data = REMOVED_CONTENT.encode()
    host.remote_files[REMOVED] = data
    host.existing_files.add(REMOVED)
    host.file_stats[REMOVED] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(data),
    }


async def test_remove_file_clears_the_content_store():
    """The simulated rm -f takes the remote_files bytes with it (the
    remove_tree store order): FileContents and RemoteFileSha1 stop
    answering for the path, as the real test -e guard fails after rm -f."""
    host = make_host()
    seed_content(host)

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.file, name="Remove config", path=REMOVED, present=False, sudo=True
        )

    _plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    assert report is not None
    assert report.log[0].changed
    assert REMOVED not in host.remote_files
    assert REMOVED not in host.existing_files
    assert REMOVED not in host.file_stats

    contents = await gather_fact([host], FileContents, REMOVED)
    digests = await gather_fact([host], RemoteFileSha1, REMOVED)
    assert contents["sim1"] is None
    assert digests["sim1"] is None


async def test_reinstall_of_removed_content_is_not_skipped():
    """The multi-operation consequence: a files.install whose content equals
    the removed bytes must still install after the removal -- a stale
    FileContents read-back would wrongly skip it and leave the path absent.
    The apply log's per-step FactCache reads the removal's fresh state."""
    host = make_host()
    seed_content(host)

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.file, name="Remove config", path=REMOVED, present=False, sudo=True
        )
        yield op(
            files.install,
            name="Re-install config",
            content=REMOVED_CONTENT,
            dest=REMOVED,
            sudo=True,
        )

    _plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Remove config"].changed
    assert entries["Re-install config"].changed
    assert host.remote_files[REMOVED] == REMOVED_CONTENT.encode()


async def test_remove_link_clears_seeded_content():
    """A link path can hold seeded bytes too: files.link's rm -f takes them
    with it, so no content read-back answers for the removed name."""
    host = make_host()
    target = "/etc/reemote-target.conf"
    host.remote_files[REMOVED] = REMOVED_CONTENT.encode()
    host.links[REMOVED] = target
    host.link_stats[REMOVED] = {
        "mode": 777,
        "user": "root",
        "group": "root",
        "link_target": target,
    }

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.link, name="Remove link", path=REMOVED, present=False, sudo=True
        )

    _plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    assert report is not None
    assert report.log[0].changed
    assert REMOVED not in host.remote_files
    assert REMOVED not in host.links
    assert REMOVED not in host.link_stats
