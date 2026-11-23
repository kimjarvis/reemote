# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Typed role variables (design doc section 8).

A role's static inputs are a frozen ``pydantic`` model that the role module
defines -- one field per knob, each with a default, and with UNPREFIXED field
names. This module is the framework that turns an inventory into one resolved
model per host; the models themselves belong to each role, not here.

``resolve_vars(cls, prefix, inventory, *, overrides)`` builds a ``VarsSet``:
for every inventory entry it layers::

    model defaults  <  the entry's prefixed ``extra`` keys  <  ``overrides``

and validates the result into ``cls``. The ``prefix`` is what binds an inventory
key to a field: ``apache_listen_port: 8080`` in the inventory sets
``ApacheVars.listen_port``. Keys that do not start with the prefix are ignored
-- they belong to another role, or stay available as the raw ``host.extra`` --
so one inventory can carry several roles' variables side by side without
collision. A key that *does* carry the prefix but names no field is a spelling
mistake, not something to ignore, so ``_bind`` raises ``RoleVarsError`` naming
the offending key and every valid prefixed field name.

``overrides`` are uniform for the whole invocation (the call-site level): a role
factory closes over one ``VarsSet`` and sees no single host, so a value that must
vary per host belongs in the inventory's ``extra`` channel, not in ``overrides``.

The resolved model is frozen so it stays hashable -- a factory closes over its
``VarsSet``, and ``op()`` rejects unhashable request arguments, so a variable
that reaches an operation must be a ``tuple`` or a scalar, never a ``list`` or a
``dict`` (design doc section 8.1).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from .hosts import Host, InventoryEntry


class RoleVarsError(ValueError):
    """Raised when an inventory key carries a role's prefix but names no field.

    A prefixed key that does not bind is a spelling mistake in the inventory --
    ``apache_listen_prot`` for ``apache_listen_port`` -- and silently ignoring it
    would leave the role running on a default the operator meant to override. The
    message names the offending key and every valid prefixed field name, so the
    typo is obvious at a glance. Subclasses ``ValueError`` like ``InventoryError``,
    the other inventory-validation failure.
    """


@dataclass(frozen=True)
class VarsSet[V: BaseModel]:
    """One resolved variable model per host, plus the prefix that bound them.

    ``values`` maps a host name to its resolved model; ``resolve_vars`` builds it
    as a read-only ``MappingProxyType``, so a ``VarsSet`` can be shared across
    every host a role runs on without one host seeing another's values.
    ``for_host`` reads by ``Host`` (the form a per-host generator uses) and
    ``__getitem__`` by host name (the form a test or a direct lookup uses); both
    raise ``KeyError`` for a host the inventory did not carry.
    """

    prefix: str
    values: Mapping[str, V]  # host name -> resolved model

    def for_host(self, host: Host) -> V:
        """The resolved model for ``host`` (keyed by ``host.name``)."""
        return self.values[host.name]

    def __getitem__(self, host_name: str) -> V:
        """The resolved model for the host named ``host_name``."""
        return self.values[host_name]


def resolve_vars[V: BaseModel](
    cls: type[V],
    prefix: str,
    inventory: list[dict],
    *,
    overrides: Mapping[str, Any] | None = None,
) -> VarsSet[V]:
    """Resolve one ``cls`` model per inventory entry into a ``VarsSet``.

    Precedence, lowest to highest: the model's own field defaults, then the
    entry's prefixed ``extra`` keys (bound and stripped by ``_bind``), then the
    call-site ``overrides``. Each entry is validated through ``InventoryEntry``
    first, so the ``extra`` channel is exactly the keys the inventory schema did
    not claim, and the merged mapping is then validated into ``cls`` -- a bad
    *value* raises pydantic's ``ValidationError`` naming the field, while a bad
    *key* raises ``RoleVarsError`` from ``_bind``.

    ``overrides`` apply to every host uniformly (see the module docstring);
    per-host variation lives in the inventory. The returned ``values`` mapping is
    immutable.
    """
    values: dict[str, V] = {}
    for entry in inventory:
        extra = InventoryEntry.model_validate(entry).model_extra or {}
        raw = {**_bind(cls, prefix, extra), **(overrides or {})}
        values[entry["host"]] = cls.model_validate(raw)
    return VarsSet(prefix, MappingProxyType(values))


def _bind(
    cls: type[BaseModel], prefix: str, source: Mapping[str, Any]
) -> dict[str, Any]:
    """Take only ``cls``'s prefixed keys from ``source`` and strip the prefix.

    Keys not starting with ``prefix`` are skipped: they belong to another role, or
    to the raw ``host.extra`` channel. A prefixed key whose bare name is not a
    field of ``cls`` raises ``RoleVarsError`` rather than being dropped, so a
    misspelled variable is reported instead of silently defaulting.
    """
    known = set(cls.model_fields)
    bound: dict[str, Any] = {}
    for key, value in source.items():
        if not key.startswith(prefix):
            continue
        bare = key[len(prefix):]
        if bare not in known:
            raise RoleVarsError(
                f"{key!r} is not a variable of {cls.__name__}; expected one of "
                f"{', '.join(sorted(prefix + f for f in known))}"
            )
        bound[bare] = value
    return bound


__all__ = [
    "RoleVarsError",
    "VarsSet",
    "resolve_vars",
]
