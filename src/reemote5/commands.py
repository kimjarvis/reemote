"""Command descriptors (design doc section 4.5).

Operations yield command descriptors, not strings, so the driver can
distinguish command types and handle them appropriately. Descriptors stay
dumb; the *driver* maps them onto host actions via ``send_command``.

This module holds the base ``Command``, the raw ``StringCommand`` and the
``TransferCommand`` base for descriptors that copy bytes over the connection
instead of running shell text; concrete typed descriptors live beside the
operations that yield them (``modules.dnf.dnf``, ``modules.files.files``).
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


__all__ = ["Command", "StringCommand", "TransferCommand"]
