# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Regression tests for the raw ``StringCommand`` descriptor's shell rendering.

The ``server.locale`` recipe migration exposed a latent bug: ``StringCommand``
had neither ``shell()`` nor ``__str__``, so ``command_shell`` fell back to
``str(cmd)`` -- the dataclass repr -- and the real transport would have run
``StringCommand(command='locale-gen')`` as shell text. No library operation
yielded the raw descriptor before, so the fallback was never exercised.
``StringCommand.shell()`` now returns the command verbatim; these tests pin
the rendering, the elevation prefixes and both ``send_command`` paths.
"""

from types import SimpleNamespace

from reemote5 import Host, StringCommand
from reemote5.drivers import command_shell, send_command
from tests.simulated_host import SimulatedHost


def test_shell_returns_the_raw_text_verbatim():
    assert StringCommand("locale-gen").shell() == "locale-gen"
    # No re-quoting: the descriptor *is* the command line, metacharacters and
    # all -- the caller owns the shell text.
    assert StringCommand("echo 'a b' && true").shell() == "echo 'a b' && true"


def test_command_shell_renders_plain_and_elevated():
    cmd = StringCommand("locale-gen")
    assert command_shell(cmd) == "locale-gen"
    assert command_shell(cmd, sudo=True) == "sudo locale-gen"
    # root is sudo's own default target: collapses to a bare sudo.
    assert command_shell(cmd, sudo=True, sudo_user="root") == "sudo locale-gen"
    assert command_shell(cmd, sudo=True, sudo_user="ops") == "sudo -u ops locale-gen"
    assert command_shell(cmd, su=True) == "su -c locale-gen"
    # doas is passwordless and outermost: ``doas -n``, root collapsing to bare.
    assert command_shell(cmd, doas=True) == "doas -n locale-gen"
    assert command_shell(cmd, doas=True, doas_user="root") == "doas -n locale-gen"
    assert command_shell(cmd, doas=True, doas_user="ops") == "doas -n -u ops locale-gen"
    assert command_shell(cmd, doas=True, sudo=True) == "doas -n sudo locale-gen"


async def test_send_command_hands_the_raw_text_to_the_transport():
    class RecordingHost(Host):
        """A real-transport fake overriding the single ``_run_shell`` seam."""

        async def _run_shell(self, shell: str):
            self.shells.append(shell)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    host = RecordingHost(name="h")
    host.shells = []
    await send_command(host, StringCommand("locale-gen"))
    assert host.shells == ["locale-gen"]


async def test_simulated_seam_accepts_string_command_as_a_no_op():
    # The SimulatedHost execute_command arm accepts the raw descriptor but
    # models no effect for it (arbitrary shell has no in-memory semantics).
    host = SimulatedHost(name="h")
    await send_command(host, StringCommand("locale-gen"))
    assert host.remote_files == {}
    assert host.locales == []