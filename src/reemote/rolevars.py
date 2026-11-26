# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Typed role variables (design doc section 8; layout: design/role_vars.md).

A role's static inputs are a frozen ``pydantic`` model that the role module
defines -- one field per knob, each with a default, and with UNPREFIXED field
names. This module is the framework that turns an inventory into one resolved
model per host; the models themselves belong to each role, not here.

``resolve_vars(cls, role, inventory, *, overrides)`` builds a ``VarsSet``:
for every inventory entry it layers::

    model defaults  <  the entry's ``roles`` mapping for ``role``  <  ``overrides``

and validates the result into ``cls``. The binding channel is the entry's
first-class ``roles`` key -- a mapping of role name to that role's variables,
written with the model's bare field names::

    {"host": "ubuntu", "family": "Ubuntu", "connection": {...},
     "roles": {"apache": {"listen_port": 8080}}}

so ``roles.apache.listen_port`` in the inventory sets ``ApacheVars.listen_port``.
A role name the program never resolves is simply unused, so one inventory can
carry several roles' variables side by side without collision -- each nested
mapping is its own namespace, and no key-prefix discipline is needed. A key
that names no field of ``cls`` is a spelling mistake, not something to ignore,
so ``_check_fields`` raises ``RoleVarsError`` naming the offending key and
every valid field name.

``overrides`` are uniform for the whole invocation (the call-site level): a role
factory closes over one ``VarsSet`` and sees no single host, so a value that must
vary per host belongs in the inventory's ``roles`` mapping, not in ``overrides``.

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
    """Raised when a role variable key names no field of the role's model.

    A key under ``roles.<role>`` that does not bind is a spelling mistake in
    the inventory -- ``listen_prot`` for ``listen_port`` -- and silently
    ignoring it would leave the role running on a default the operator meant
    to override. The message names the offending key and every valid field
    name of the role. Subclasses ``ValueError`` like ``InventoryError``, the
    other inventory-validation failure.
    """


@dataclass(frozen=True)
class VarsSet[V: BaseModel]:
    """One resolved variable model per host, plus the role name that bound them.

    ``values`` maps a host name to its resolved model; ``resolve_vars`` builds it
    as a read-only ``MappingProxyType``, so a ``VarsSet`` can be shared across
    every host a role runs on without one host seeing another's values.
    ``for_host`` reads by ``Host`` (the form a per-host generator uses) and
    ``__getitem__`` by host name (the form a test or a direct lookup uses); both
    raise ``KeyError`` for a host the inventory did not carry.
    """

    role: str
    values: Mapping[str, V]  # host name -> resolved model

    def for_host(self, host: Host) -> V:
        """The resolved model for ``host`` (keyed by ``host.name``)."""
        return self.values[host.name]

    def __getitem__(self, host_name: str) -> V:
        """The resolved model for the host named ``host_name``."""
        return self.values[host_name]


def resolve_vars[V: BaseModel](
    cls: type[V],
    role: str,
    inventory: list[dict],
    *,
    overrides: Mapping[str, Any] | None = None,
) -> VarsSet[V]:
    """Resolve one ``cls`` model per inventory entry into a ``VarsSet``.

    ``role`` is the name under which the inventory's ``roles`` mapping keys
    this role's variables (by convention the factory's own name, e.g.
    ``"apache"``). Precedence, lowest to highest: the model's own field
    defaults, then the entry's ``roles[role]`` mapping (checked by
    ``_check_fields``), then the call-site ``overrides``. Each entry is
    validated through ``InventoryEntry`` first, so a malformed ``roles``
    mapping fails as the schema's ``ValidationError`` at the inventory layer,
    and the merged mapping is then validated into ``cls`` -- a bad *value*
    raises pydantic's ``ValidationError`` naming the field, while a bad *key*
    raises ``RoleVarsError`` from ``_check_fields``.

    ``overrides`` apply to every host uniformly (see the module docstring);
    per-host variation lives in the inventory's ``roles`` mapping. The
    returned ``values`` mapping is immutable.
    """
    values: dict[str, V] = {}
    for entry in inventory:
        validated = InventoryEntry.model_validate(entry)
        raw = _check_fields(cls, role, validated.roles.get(role, {}))
        values[entry["host"]] = cls.model_validate({**raw, **(overrides or {})})
    return VarsSet(role, MappingProxyType(values))


def _check_fields(
    cls: type[BaseModel], role: str, source: Mapping[str, Any]
) -> dict[str, Any]:
    """Return ``source`` verbatim after checking every key names a field of ``cls``.

    The keys arrive bare -- ``roles.<role>`` is already the role's own
    namespace, so there is no prefix to strip -- which lets the typo rule
    cover the whole mapping. A key that is not a field of ``cls`` raises
    ``RoleVarsError`` rather than being dropped, so a misspelled variable is
    reported instead of silently defaulting. The check is hand-rolled rather
    than delegated to pydantic so the error stays a ``RoleVarsError`` (a
    ``ValueError``, in the inventory-validation family) whatever the model's
    ``model_config`` says about extras, and so the message lists every valid
    name. Nested mappings (a ``vhosts`` item, say) are not checked here; they
    are validated into their own models by ``cls.model_validate``.
    """
    known = set(cls.model_fields)
    for key in source:
        if key not in known:
            raise RoleVarsError(
                f"{key!r} is not a variable of role {role!r}; expected one of "
                f"{', '.join(sorted(known))}"
            )
    return dict(source)


__all__ = [
    "RoleVarsError",
    "VarsSet",
    "resolve_vars",
]
