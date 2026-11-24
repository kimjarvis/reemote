# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for typed role variables (design doc section 8).

Covers ``reemote.rolevars``: ``resolve_vars`` (the precedence layering --
model defaults < the entry's prefixed ``extra`` keys < the call-site
``overrides``), ``_bind``'s prefix stripping and its ``RoleVarsError`` on a
prefixed key that names no field, the frozen ``VarsSet`` and its two read
forms (``for_host`` by ``Host``, ``__getitem__`` by name), and the immutability
that keeps a resolved model hashable so it can reach ``op()``.
"""

import dataclasses

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from reemote import Host, RoleVarsError, VarsSet, resolve_vars


class ApacheVars(BaseModel):
    """A role's static inputs: UNPREFIXED field names, each with a default.

    Frozen so the resolved model stays hashable -- a factory closes over its
    ``VarsSet`` and ``op()`` rejects unhashable request arguments, so a
    collection field is a ``tuple``, never a ``list``.
    """

    model_config = ConfigDict(frozen=True)

    listen_port: int = 80
    server_name: str = "localhost"
    modules: tuple[str, ...] = ()


class NginxVars(BaseModel):
    """A second role's model, to prove two roles coexist in one inventory."""

    model_config = ConfigDict(frozen=True)

    worker_processes: int = 1


def entry(host: str = "web1", **extra) -> dict:
    """One validated inventory entry carrying ``extra`` as raw extra keys."""
    return {"host": host, "family": "Ubuntu", "connection": {}, **extra}


# --- precedence: defaults < prefixed extra < overrides ------------------------


def test_defaults_apply_when_the_inventory_carries_nothing():
    vs = resolve_vars(ApacheVars, "apache_", [entry()])

    assert vs["web1"] == ApacheVars()
    assert vs["web1"].listen_port == 80
    assert vs["web1"].server_name == "localhost"
    assert vs["web1"].modules == ()


def test_prefixed_extra_binds_and_strips_the_prefix():
    vs = resolve_vars(ApacheVars, "apache_", [entry(apache_listen_port=8080)])

    # The prefix binds the key to the field; the field sees the bare name.
    assert vs["web1"].listen_port == 8080
    # An unbound field keeps its default.
    assert vs["web1"].server_name == "localhost"


def test_keys_without_the_prefix_are_ignored():
    # ``nginx_worker_processes`` belongs to another role (or stays raw extra),
    # so it neither binds nor raises.
    vs = resolve_vars(
        ApacheVars,
        "apache_",
        [entry(apache_listen_port=8080, nginx_worker_processes=4)],
    )

    assert vs["web1"].listen_port == 8080


def test_two_roles_resolve_side_by_side_from_one_inventory():
    inventory = [entry(apache_listen_port=8080, nginx_worker_processes=4)]

    apache = resolve_vars(ApacheVars, "apache_", inventory)
    nginx = resolve_vars(NginxVars, "nginx_", inventory)

    assert apache["web1"].listen_port == 8080
    assert nginx["web1"].worker_processes == 4


def test_overrides_win_over_inventory_and_defaults():
    vs = resolve_vars(
        ApacheVars,
        "apache_",
        [entry(apache_listen_port=8080)],
        overrides={"listen_port": 9000},
    )

    # ``overrides`` use the BARE field name and are merged last.
    assert vs["web1"].listen_port == 9000


def test_overrides_apply_to_every_host_uniformly():
    inventory = [entry("web1"), entry("web2")]

    vs = resolve_vars(
        ApacheVars, "apache_", inventory, overrides={"server_name": "example.com"}
    )

    assert vs["web1"].server_name == "example.com"
    assert vs["web2"].server_name == "example.com"


# --- _bind: a misspelled prefixed key is reported, not dropped ----------------


def test_unknown_prefixed_key_raises_role_vars_error():
    # ``apache_listen_prot`` carries the prefix but names no field: a spelling
    # mistake, reported rather than silently defaulted.
    with pytest.raises(RoleVarsError, match="is not a variable of ApacheVars"):
        resolve_vars(ApacheVars, "apache_", [entry(apache_listen_prot=8080)])


def test_role_vars_error_names_the_offending_key_and_valid_fields():
    with pytest.raises(RoleVarsError) as excinfo:
        resolve_vars(ApacheVars, "apache_", [entry(apache_listen_prot=8080)])

    message = str(excinfo.value)
    assert "apache_listen_prot" in message
    # Every valid prefixed field name is listed so the typo is obvious.
    assert "apache_listen_port" in message
    assert "apache_server_name" in message
    assert "apache_modules" in message


def test_role_vars_error_is_a_value_error():
    # Subclasses ValueError like InventoryError, the other inventory failure.
    assert issubclass(RoleVarsError, ValueError)


def test_a_bad_value_raises_validation_error_not_role_vars_error():
    # A bad *key* is RoleVarsError; a bad *value* is pydantic's ValidationError.
    with pytest.raises(ValidationError):
        resolve_vars(ApacheVars, "apache_", [entry(apache_listen_port="not-an-int")])


# --- hashability: a collection field coerces to a tuple -----------------------


def test_a_list_value_coerces_to_a_tuple_and_stays_hashable():
    vs = resolve_vars(
        ApacheVars, "apache_", [entry(apache_modules=["rewrite", "ssl"])]
    )

    assert vs["web1"].modules == ("rewrite", "ssl")
    # The frozen model with a tuple field hashes, so it can reach op().
    hash(vs["web1"])


# --- per-host resolution and the two read forms -------------------------------


def test_each_host_resolves_its_own_values():
    inventory = [
        entry("web1", apache_listen_port=8080),
        entry("web2", apache_listen_port=9090),
    ]

    vs = resolve_vars(ApacheVars, "apache_", inventory)

    assert vs["web1"].listen_port == 8080
    assert vs["web2"].listen_port == 9090


def test_for_host_reads_by_host_object():
    vs = resolve_vars(ApacheVars, "apache_", [entry(apache_listen_port=8080)])

    assert vs.for_host(Host(name="web1")) == vs["web1"]
    assert vs.for_host(Host(name="web1")).listen_port == 8080


def test_a_host_the_inventory_did_not_carry_raises_key_error():
    vs = resolve_vars(ApacheVars, "apache_", [entry()])

    with pytest.raises(KeyError):
        vs["web9"]
    with pytest.raises(KeyError):
        vs.for_host(Host(name="web9"))


def test_the_prefix_is_recorded_on_the_vars_set():
    vs = resolve_vars(ApacheVars, "apache_", [entry()])

    assert vs.prefix == "apache_"
    assert isinstance(vs, VarsSet)


def test_an_empty_inventory_resolves_to_no_values():
    vs = resolve_vars(ApacheVars, "apache_", [])

    assert dict(vs.values) == {}


# --- immutability -------------------------------------------------------------


def test_the_values_mapping_is_read_only():
    vs = resolve_vars(ApacheVars, "apache_", [entry()])

    with pytest.raises(TypeError):
        vs.values["web1"] = ApacheVars()


def test_the_vars_set_is_frozen():
    vs = resolve_vars(ApacheVars, "apache_", [entry()])

    with pytest.raises(dataclasses.FrozenInstanceError):
        vs.prefix = "other"
