# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Griffe extension hiding fact-entry and descriptor fields from the API.

Every fact entry type (``ApkPackageInfo``, ``AptKey``, ``BlockDevice``,
``GitConfigItem``, ...) is a ``TypedDict`` whose fields are documented once
in the class docstring's Google ``Attributes:`` table. Rendering the fields
again as member entries only duplicates that prose, so this extension drops
them by *kind* at collection time -- no per-field name filters in
``mkdocs.yml`` are needed, and a new entry type is covered automatically.

The same applies to the internal command descriptors (``Command``
subclasses such as ``DnfInstallCommand``): their dataclass fields already
show in the class signature, so the member entries are dropped too.

Registered through the Python handler's ``extensions`` option in
``mkdocs.yml``; griffe loads the file by path and instantiates the
``Extension`` subclass it finds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from griffe import Extension

if TYPE_CHECKING:
    import ast

    from griffe import Class, ObjectNode


class HideDataFields(Extension):
    """Drop the attribute members of TypedDicts and command descriptors."""

    def on_class_members(
        self,
        *,
        node: ast.AST | ObjectNode,
        cls: Class,
        agent: Any,
        **kwargs: Any,
    ) -> None:
        """Remove a covered class's fields once its members have been visited.

        Args:
            node: The visited class node.
            cls: The collected class object.
            agent: The analysis agent running the collection.
            **kwargs: Forward-compatibility with future griffe hooks.
        """
        # Bases are unresolved expressions during static analysis, so match
        # on their textual tail: ``TypedDict`` (also ``typing.TypedDict``)
        # for fact entries, and the ``Command`` descriptor family, whose
        # concrete classes all end in ``Command`` (``DnfInstallCommand``
        # bases ``Command`` directly, ``PutCommand`` bases ``TransferCommand``).
        tails = {str(base).split(".")[-1] for base in cls.bases}
        if "TypedDict" not in tails and not any(
            tail.endswith("Command") for tail in tails
        ):
            return
        for name, member in list(cls.members.items()):
            # Properties are attributes too in griffe, but neither family has
            # any; the label guard keeps this hook safe if one ever gains one.
            if member.is_attribute and "property" not in member.labels:
                del cls.members[name]
