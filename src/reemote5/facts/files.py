"""The ``files`` fact namespace (design doc section 5).

Exposes ``FileLink``, the fact read by ``files.link`` to learn the current
target of a symlink. Like every fact it is phase-unaware: the ``process``
coroutine just gathers the raw value.
"""

from ..hosts import Host


class FileLink:
    """The target of a symlink, or None if the link does not exist."""

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        links = getattr(host, "links", None)
        if links is None:
            raise NotImplementedError(
                "real SSH fact gathering is not implemented: it will run "
                "`readlink` over asyncssh using host.connection/host.session"
            )
        # Stand-in for `readlink` run over asyncssh; the POC reads the
        # simulated host state directly instead of connecting anywhere.
        return links.get(path)


__all__ = ["FileLink"]
