"""The ``dnf`` fact namespace (design doc section 5).

Exposes ``DnfPackages``, the fact read by ``dnf.packages`` to learn which
packages are already installed. Like every fact it is phase-unaware: the
``process`` coroutine just gathers the raw value.
"""

from ..hosts import Host


class DnfPackages:
    """The set of installed dnf package names."""

    #: Shell used to list installed package names over SSH.
    COMMAND = "rpm -qa --queryformat '%{NAME}\n'"

    @classmethod
    async def process(cls, host: Host) -> set[str]:
        packages = getattr(host, "packages", None)
        if packages is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return set(packages)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return {line.strip() for line in stdout.splitlines() if line.strip()}


__all__ = ["DnfPackages"]
