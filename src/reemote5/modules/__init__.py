"""Self-contained module namespaces.

Each subpackage combines the facts and operations of one namespace in a
single source, so ``modules/dnf/dnf.py`` holds the dnf fact classes
(``DnfPackages``, ``DnfRepositories``, ``DnfEnabledModules``,
``DnfDisabledModules``) beside the ``packages`` operation and the typed
command descriptors it yields, and ``modules/files/files.py`` holds
``FileLink``/``FileExists`` beside the ``link``/``file`` operations. The
subpackages are re-exported here, so a deploy can keep writing
``from reemote5.modules import dnf, files`` and read ``dnf.packages`` /
``files.link``.
"""

from . import dnf, files

__all__ = ["dnf", "files"]
