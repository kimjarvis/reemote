"""Command descriptors (design doc section 4.5).

Operations yield command descriptors, not strings, so the driver can
distinguish command types and handle them appropriately. Descriptors stay
dumb; the *driver* maps them onto host actions via ``send_command``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """Base class for all command descriptors."""


@dataclass(frozen=True)
class StringCommand(Command):
    """A raw shell command (would be sent over SSH in a real implementation)."""

    command: str


@dataclass(frozen=True)
class AptInstallCommand(Command):
    packages: tuple[str, ...]

    def shell(self) -> str:
        return f"apt-get install -y {' '.join(self.packages)}"


@dataclass(frozen=True)
class AptRemoveCommand(Command):
    packages: tuple[str, ...]

    def shell(self) -> str:
        return f"apt-get remove -y {' '.join(self.packages)}"


@dataclass(frozen=True)
class LinkCommand(Command):
    src: str
    target: str | None = None  # None means "remove the link at src"

    def shell(self) -> str:
        if self.target is None:
            return f"rm -f {self.src}"
        return f"ln -sf {self.target} {self.src}"


__all__ = [
    "AptInstallCommand",
    "AptRemoveCommand",
    "Command",
    "LinkCommand",
    "StringCommand",
]
