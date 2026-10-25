"""Facts (design doc section 5).

Facts are asynchronous queryable resources, not yielded by generators. A
fact class implements a ``process`` coroutine returning the raw fact value;
the real implementation will gather it over asyncssh (rather than pyinfra's
Paramiko + gevent stack). Facts are phase-unaware; the ``FactCache`` that
wraps them is phase-aware, and the driver controls cache lifetime.
"""

from .hosts import SimulatedHost


class AptPackages:
    """The set of installed apt package names."""

    @classmethod
    async def process(cls, host: SimulatedHost) -> set[str]:
        # Stand-in for `dpkg-query` run over asyncssh; the POC reads the
        # simulated host state directly instead of connecting anywhere.
        return set(host.packages)


class FileLink:
    """The target of a symlink, or None if the link does not exist."""

    @classmethod
    async def process(cls, host: SimulatedHost, path: str) -> str | None:
        # Stand-in for `readlink` run over asyncssh; the POC reads the
        # simulated host state directly instead of connecting anywhere.
        return host.links.get(path)


__all__ = ["AptPackages", "FileLink"]
