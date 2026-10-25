"""Facts (design doc section 5).

Facts are synchronous queryable resources, not yielded by generators.
A fact class implements a ``process`` classmethod returning the raw fact
value. Facts are phase-unaware; the ``FactCache`` that wraps them is
phase-aware, and the driver controls cache lifetime.
"""

from .hosts import SimulatedHost


class AptPackages:
    """The set of installed apt package names."""

    @classmethod
    def process(cls, host: SimulatedHost) -> set[str]:
        # A real implementation would run `dpkg-query` over SSH here.
        return set(host.packages)


class FileLink:
    """The target of a symlink, or None if the link does not exist."""

    @classmethod
    def process(cls, host: SimulatedHost, path: str) -> str | None:
        # A real implementation would run `readlink` over SSH here.
        return host.links.get(path)


__all__ = ["AptPackages", "FileLink"]
