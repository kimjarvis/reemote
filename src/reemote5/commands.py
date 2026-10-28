"""Command descriptors (design doc section 4.5).

Operations yield command descriptors, not strings, so the driver can
distinguish command types and handle them appropriately. Descriptors stay
dumb; the *driver* maps them onto host actions via ``send_command``.

This module holds the base ``Command`` and the raw ``StringCommand``; typed
descriptors live beside the operations that yield them
(``modules.dnf.dnf``, ``modules.files.files``).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """Base class for all command descriptors."""


@dataclass(frozen=True)
class StringCommand(Command):
    """A raw shell command (would be sent over SSH in a real implementation)."""

    command: str


__all__ = ["Command", "StringCommand"]
