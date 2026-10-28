"""The ``files`` module namespace.

Re-exports everything from ``files.py`` so both ``files.link``/``files.file``
(the operations) and ``files.FileLink``/``files.FileExists`` (the facts) are
reachable through the package name alone.
"""

from . import files
from .files import (
    FileCommand,
    FileExists,
    FileLink,
    LinkCommand,
    file,
    link,
)

__all__ = [
    "FileCommand",
    "FileExists",
    "FileLink",
    "LinkCommand",
    "file",
    "files",
    "link",
]
