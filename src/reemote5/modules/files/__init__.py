"""The ``files`` module namespace.

Re-exports everything from ``files.py`` so the operations (``files.link``,
``files.file``, ``files.put``, ``files.get``, ``files.edit``, ``files.move``)
and the facts
(``files.FileLink``, ``files.FileExists``, ``files.LocalSha1``,
``files.RemoteFileSha1``, ``files.FileContents``, ``files.File``,
``files.Directory``, ``files.Link``) are reachable through the package name
alone.
"""

from . import files
from .files import (
    Directory,
    File,
    FileCommand,
    FileContents,
    FileExists,
    FileLink,
    GetCommand,
    Link,
    LinkCommand,
    LocalSha1,
    MoveCommand,
    PutCommand,
    RemoteFileSha1,
    RemoveTreeCommand,
    edit,
    file,
    get,
    link,
    move,
    put,
)

__all__ = [
    "Directory",
    "File",
    "FileCommand",
    "FileContents",
    "FileExists",
    "FileLink",
    "GetCommand",
    "Link",
    "LinkCommand",
    "LocalSha1",
    "MoveCommand",
    "PutCommand",
    "RemoteFileSha1",
    "RemoveTreeCommand",
    "edit",
    "file",
    "files",
    "get",
    "link",
    "move",
    "put",
]
