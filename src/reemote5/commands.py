"""Command descriptors (design doc section 4.5).

Operations yield command descriptors, not strings, so the driver can
distinguish command types and handle them appropriately. Descriptors stay
dumb; the *driver* maps them onto host actions via ``send_command``.

This module holds the base ``Command``, the raw ``StringCommand``, the
``TransferCommand`` base for descriptors that copy bytes over the connection
instead of running shell text, and the ``WaitCommand`` base for descriptors
that poll the host until a condition holds; concrete typed descriptors live
beside the operations that yield them (``modules.dnf.dnf``,
``modules.files.files``, ``modules.server.server``).
"""

from dataclasses import dataclass

from .hosts import Host


@dataclass(frozen=True)
class Command:
    """Base class for all command descriptors."""


@dataclass(frozen=True)
class StringCommand(Command):
    """A raw shell command (would be sent over SSH in a real implementation)."""

    command: str


@dataclass(frozen=True)
class TransferCommand(Command):
    """Base for descriptors that transfer bytes rather than run shell text.

    A transfer has no ``shell()`` rendering -- it is not executed through the
    shell pipeline -- so ``send_command`` dispatches on this base and hands the
    host to ``transfer``, which performs the copy over the host's own
    transport. The base lives here (not beside a concrete descriptor) so the
    driver can dispatch on it without importing a namespace module, which
    would be circular: ``modules.files.files`` imports ``core``, and ``core``
    and the drivers sit above ``commands``.
    """

    async def transfer(self, host: Host) -> None:
        """Perform the transfer against ``host``; subclasses must override."""
        raise NotImplementedError(
            f"{type(self).__name__} does not implement transfer()"
        )


class WaitTimeoutError(RuntimeError):
    """Raised by a ``WaitCommand`` whose condition was not met within its timeout.

    Propagates out of ``send_command`` and therefore out of ``run_apply`` --
    a wait that never completes aborts the run, exactly as a failing shell
    command does via ``CommandError``.
    """


@dataclass(frozen=True)
class WaitCommand(Command):
    """Base for descriptors that poll the host until a condition holds.

    The second kind of non-shell descriptor, beside ``TransferCommand``: a
    wait has no ``shell()`` rendering -- nothing is sent as shell text.
    ``send_command`` dispatches on this base and hands the host to ``wait``,
    which polls (re-reading a fact directly, never through the per-step
    ``FactCache``, which would pin the first value) and sleeps between
    attempts until the condition holds or the timeout expires, raising
    ``WaitTimeoutError`` in the latter case. Like a transfer it is
    unprivileged: the ``sudo``/``su`` flags do not apply to a poll.

    Waiting lives in a descriptor rather than in the operation body because
    the prepare phase never sends: an operation that polled inside its own
    generator would spin forever against the prepare cache. The operation
    only *describes* the wait; the driver performs it at send time. The base
    lives here (not beside a concrete descriptor) for the same reason as
    ``TransferCommand``: the driver must dispatch on it without importing a
    namespace module.
    """

    async def wait(self, host: Host) -> str:
        """Block until the condition holds; return a human-readable outcome.

        Subclasses must override. Raises ``WaitTimeoutError`` when the
        condition is not met within the descriptor's timeout.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement wait()")


__all__ = [
    "Command",
    "StringCommand",
    "TransferCommand",
    "WaitCommand",
    "WaitTimeoutError",
]
