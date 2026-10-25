"""Fact namespaces (design doc section 5).

Facts are asynchronous queryable resources, not yielded by generators. A
fact class implements a ``process`` coroutine returning the raw fact value;
the real implementation will gather it over asyncssh (rather than pyinfra's
Paramiko + gevent stack). Facts are phase-unaware; the ``FactCache`` that
wraps them is phase-aware, and the driver controls cache lifetime.

Facts are grouped into namespace modules mirroring the operation namespaces,
so ``apt.AptPackages`` sits beside ``apt.packages`` and ``files.FileLink``
beside ``files.link``. The classes are re-exported here, so callers can keep
writing ``from reemote5.facts import AptPackages, FileLink``.

The POC has no SSH transport: each ``process`` reads the in-memory state of
a simulated host through a duck-typed seam (``packages``/``links``, provided
by the test framework's ``SimulatedHost`` in ``tests/simulated_host.py``)
and raises ``NotImplementedError`` for any host lacking it. The library
never imports the test-framework class itself.
"""

from . import apt, files
from .apt import AptPackages
from .files import FileLink

__all__ = ["AptPackages", "FileLink", "apt", "files"]
