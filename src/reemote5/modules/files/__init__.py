# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``files`` module namespace.

Re-exports everything from ``files.py`` so the operations (``files.link``,
``files.file``, ``files.put``, ``files.get``, ``files.edit``, ``files.move``,
``files.copy``) and the facts
(``files.FileLink``, ``files.FileExists``,
``files.RemoteFileSha1``, ``files.FileContents``, ``files.File``,
``files.Directory``, ``files.Link``) are reachable through the package name
alone.
"""

from . import files
from .files import (
    CopyCommand,
    Directory,
    File,
    FileCommand,
    FileContents,
    FileExists,
    FileLink,
    GetCommand,
    Link,
    LinkCommand,
    MoveCommand,
    PutCommand,
    RemoteFileSha1,
    RemoveTreeCommand,
    copy,
    edit,
    file,
    get,
    link,
    move,
    put,
)

__all__ = [
    "CopyCommand",
    "Directory",
    "File",
    "FileCommand",
    "FileContents",
    "FileExists",
    "FileLink",
    "GetCommand",
    "Link",
    "LinkCommand",
    "MoveCommand",
    "PutCommand",
    "RemoteFileSha1",
    "RemoveTreeCommand",
    "copy",
    "edit",
    "file",
    "files",
    "get",
    "link",
    "move",
    "put",
]
