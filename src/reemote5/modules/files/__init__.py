"""The ``files`` module namespace.

Re-exports everything from ``files.py`` so the operations (``files.link``,
``files.file``, ``files.put``, ``files.get``, ``files.edit``) and the facts
(``files.FileLink``, ``files.FileExists``, ``files.LocalSha1``,
``files.RemoteFileSha1``, ``files.FileContents``) are reachable through the
package name alone.
"""

from . import files
from .files import (
    FileCommand,
    FileContents,
    FileExists,
    FileLink,
    GetCommand,
    LinkCommand,
    LocalSha1,
    PutCommand,
    RemoteFileSha1,
    edit,
    file,
    get,
    link,
    put,
)

__all__ = [
    "FileCommand",
    "FileContents",
    "FileExists",
    "FileLink",
    "GetCommand",
    "LinkCommand",
    "LocalSha1",
    "PutCommand",
    "RemoteFileSha1",
    "edit",
    "file",
    "files",
    "get",
    "link",
    "put",
]
