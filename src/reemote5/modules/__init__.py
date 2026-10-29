"""Self-contained module namespaces.

Each subpackage combines the facts and operations of one namespace in a
single source, so ``modules/dnf/dnf.py`` holds the dnf fact classes
(``DnfPackages``, ``DnfRepositories``, ``DnfEnabledModules``,
``DnfDisabledModules``) beside the ``packages`` operation and the typed
command descriptors it yields, ``modules/files/files.py`` holds
``FileLink``/``FileExists`` beside the ``link``/``file`` operations, and
``modules/server/server.py`` holds the systemd facts beside the ``service``
and ``wait_service`` operations. The subpackages are re-exported here, so a
deploy can keep writing ``from reemote5.modules import dnf, files, server``
and read ``dnf.packages`` / ``files.link`` / ``server.service``.
"""

from . import dnf, files, server

__all__ = ["dnf", "files", "server"]
