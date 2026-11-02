"""Self-contained module namespaces.

Each subpackage combines the facts and operations of one namespace in a
single source, so ``modules/apk/apk.py`` holds the apk fact classes
(``ApkExplicitPackages``, ``ApkPackages``, ``ApkRepositories``),
``modules/apt/apt.py`` holds the apt fact classes (``AptKeys``,
``AptPackages``, ``AptUpgradable``, ``AptAutoremovable``),
``modules/dnf/dnf.py`` holds the dnf fact classes
(``DnfPackages``, ``DnfRepositories``, ``DnfEnabledModules``,
``DnfDisabledModules``) beside the ``packages`` operation and the typed
command descriptors it yields, ``modules/files/files.py`` holds
``FileLink``/``FileExists`` beside the ``link``/``file`` operations,
``modules/hardware/hardware.py`` holds the ``BlockDevices``, ``CpuInfo``,
``Cpus``, ``DiskStats``, ``DmiInfo``, ``Ipv4Addrs``, ``Ipv6Addrs``,
``LsBlk``, ``NetworkDevices`` and ``SmbiosInfo`` facts, and
``modules/server/server.py`` holds the systemd facts beside the ``service``
and ``wait_service`` operations. The subpackages are re-exported here, so a
deploy can keep writing
``from reemote5.modules import apk, apt, dnf, files, hardware, server`` and read
``apk.ApkPackages`` / ``apt.AptKeys`` / ``dnf.packages`` / ``files.link`` /
``hardware.BlockDevices`` / ``server.service``.
"""

from . import apk, apt, dnf, files, hardware, server

__all__ = ["apk", "apt", "dnf", "files", "hardware", "server"]
