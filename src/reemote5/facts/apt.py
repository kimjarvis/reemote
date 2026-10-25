"""The ``apt`` fact namespace (design doc section 5).

Exposes ``AptPackages``, the fact read by ``apt.packages`` to learn which
packages are already installed. Like every fact it is phase-unaware: the
``process`` coroutine just gathers the raw value.
"""

from ..hosts import Host


class AptPackages:
    """The set of installed apt package names."""

    @classmethod
    async def process(cls, host: Host) -> set[str]:
        packages = getattr(host, "packages", None)
        if packages is None:
            raise NotImplementedError(
                "real SSH fact gathering is not implemented: it will run "
                "`dpkg-query` over asyncssh using host.connection/host.session"
            )
        # Stand-in for `dpkg-query` run over asyncssh; the POC reads the
        # simulated host state directly instead of connecting anywhere.
        return set(packages)


__all__ = ["AptPackages"]
