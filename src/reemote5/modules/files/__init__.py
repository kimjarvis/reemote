"""The ``files`` module namespace.

Re-exports everything from ``files.py`` so the operations (``files.link``,
``files.file``, ``files.put``, ``files.get``) and the facts (``files.FileLink``,
``files.FileExists``, ``files.LocalSha1``, ``files.RemoteFileSha1``) are
reachable through the package name alone.
"""

from . import files
from .files import (
    FileCommand,
    FileExists,
    FileLink,
    GetCommand,
    LinkCommand,
    LocalSha1,
    PutCommand,
    RemoteFileSha1,
    file,
    get,
    link,
    put,
)

__all__ = [
    "FileCommand",
    "FileExists",
    "FileLink",
    "GetCommand",
    "LinkCommand",
    "LocalSha1",
    "PutCommand",
    "RemoteFileSha1",
    "file",
    "files",
    "get",
    "link",
    "put",
]
