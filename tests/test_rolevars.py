# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for typed role variables (design doc section 8; layout: design/role_vars.md).

Covers ``reemote.rolevars``: ``resolve_vars`` (the precedence layering --
model defaults < the entry's ``roles`` mapping for the role < the call-site
``overrides``), ``_check_fields`` and its ``RoleVarsError`` on a key that names
no field, the frozen ``VarsSet`` and its two read forms (``for_host`` by
``Host``, ``__getitem__`` by name), and the immutability that keeps a resolved
model hashable so it can reach ``op()``.
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


class PhpVars(BaseModel):
    """A dependency role whose NAME nests the dependent role's below."""

    model_config = ConfigDict(frozen=True)

    webserver_daemon: str | None = None
    enable_php_fpm: bool = False


class PhpXdebugVars(BaseModel):
    """A dependent role keyed ``php_xdebug`` -- a longer name under ``php``."""

    model_config = ConfigDict(frozen=True)

    version: str = "3.3.2"
    cli_disable: bool = False


def entry(host: str = "web1", **roles: dict) -> dict:
    """One validated inventory entry carrying each ``roles`` kwarg under ``roles``.

    ``entry(apache={"listen_port": 8080})`` is the operator's nested layout:
    role name -> that role's variables, spelled with the model's bare names.
    """
    return {"host": host, "family": "Ubuntu", "connection": {}, "roles": dict(roles)}


# --- precedence: defaults < roles entry < overrides ----------------------------


def test_defaults_apply_when_the_inventory_carries_nothing():
    vs = resolve_vars(ApacheVars, "apache", [entry()])

    assert vs["web1"] == ApacheVars()
    assert vs["web1"].listen_port == 80
    assert vs["web1"].server_name == "localhost"
    assert vs["web1"].modules == ()


def test_the_roles_mapping_binds_bare_field_names():
    vs = resolve_vars(ApacheVars, "apache", [entry(apache={"listen_port": 8080})])

    # The role name keys the mapping; the keys inside are the bare field names.
    assert vs["web1"].listen_port == 8080
    # An unbound field keeps its default.
    assert vs["web1"].server_name == "localhost"


def test_other_roles_variables_are_ignored():
    # The entry's ``nginx`` mapping belongs to another role, so it neither
    # binds nor raises -- each nested mapping is its own namespace.
    vs = resolve_vars(
        ApacheVars,
        "apache",
        [entry(apache={"listen_port": 8080}, nginx={"worker_processes": 4})],
    )

    assert vs["web1"].listen_port == 8080


def test_two_roles_resolve_side_by_side_from_one_inventory():
    inventory = [entry(apache={"listen_port": 8080}, nginx={"worker_processes": 4})]

    apache = resolve_vars(ApacheVars, "apache", inventory)
    nginx = resolve_vars(NginxVars, "nginx", inventory)

    assert apache["web1"].listen_port == 8080
    assert nginx["web1"].worker_processes == 4


def test_overrides_win_over_inventory_and_defaults():
    vs = resolve_vars(
        ApacheVars,
        "apache",
        [entry(apache={"listen_port": 8080})],
        overrides={"listen_port": 9000},
    )

    # ``overrides`` use the same BARE field names and are merged last.
    assert vs["web1"].listen_port == 9000


def test_overrides_apply_to_every_host_uniformly():
    inventory = [entry("web1"), entry("web2")]

    vs = resolve_vars(
        ApacheVars, "apache", inventory, overrides={"server_name": "example.com"}
    )

    assert vs["web1"].server_name == "example.com"
    assert vs["web2"].server_name == "example.com"


# --- _check_fields: a misspelled key is reported, not dropped ------------------


def test_unknown_key_raises_role_vars_error():
    # ``listen_prot`` names no field: a spelling mistake, reported rather than
    # silently defaulted.
    with pytest.raises(RoleVarsError, match="is not a variable of role"):
        resolve_vars(ApacheVars, "apache", [entry(apache={"listen_prot": 8080})])


def test_role_vars_error_names_the_offending_key_and_valid_fields():
    with pytest.raises(RoleVarsError) as excinfo:
        resolve_vars(ApacheVars, "apache", [entry(apache={"listen_prot": 8080})])

    message = str(excinfo.value)
    assert "listen_prot" in message
    # Every valid field name is listed (bare, as the operator writes them) so
    # the typo is obvious at a glance.
    assert "listen_port" in message
    assert "server_name" in message
    assert "modules" in message


def test_role_vars_error_is_a_value_error():
    # Subclasses ValueError like InventoryError, the other inventory failure.
    assert issubclass(RoleVarsError, ValueError)


def test_a_bad_value_raises_validation_error_not_role_vars_error():
    # A bad *key* is RoleVarsError; a bad *value* is pydantic's ValidationError.
    with pytest.raises(ValidationError):
        resolve_vars(ApacheVars, "apache", [entry(apache={"listen_port": "not-an-int"})])


def test_old_prefixed_keys_are_now_ordinary_extra():
    # The layout's one silent hazard (design D1): a top-level ``apache_`` key
    # is no longer a binding channel -- it stays raw extra and the defaults apply.
    inventory = [
        {"host": "web1", "family": "Ubuntu", "connection": {}, "apache_listen_port": 8080}
    ]

    vs = resolve_vars(ApacheVars, "apache", inventory)

    assert vs["web1"].listen_port == 80


# --- role names nest: php and php_xdebug share no key namespace -----------------


def test_nested_role_names_resolve_independently():
    # ``php_xdebug`` is the dependent role of ``php``: one more specific name,
    # its own nested mapping. Under the old flat layout this pair needed the
    # ``nested_prefixes`` escape; under ``roles`` each mapping is a namespace.
    inventory = [
        entry(php={"enable_php_fpm": True}, php_xdebug={"version": "3.4.0"})
    ]

    php = resolve_vars(PhpVars, "php", inventory)
    xdebug = resolve_vars(PhpXdebugVars, "php_xdebug", inventory)

    assert php["web1"].enable_php_fpm is True
    assert php["web1"].webserver_daemon is None
    assert xdebug["web1"].version == "3.4.0"


def test_a_nested_role_name_does_not_weaken_the_typo_check():
    # A key inside the parent's own mapping that names no parent field is
    # still a spelling mistake, whatever other roles the entry carries.
    with pytest.raises(RoleVarsError, match="webserver_damon"):
        resolve_vars(
            PhpVars,
            "php",
            [entry(php={"webserver_damon": "nginx"}, php_xdebug={"version": "3.4.0"})],
        )


# --- hashability: a collection field coerces to a tuple -----------------------


def test_a_list_value_coerces_to_a_tuple_and_stays_hashable():
    vs = resolve_vars(
        ApacheVars, "apache", [entry(apache={"modules": ["rewrite", "ssl"]})]
    )

    assert vs["web1"].modules == ("rewrite", "ssl")
    # The frozen model with a tuple field hashes, so it can reach op().
    hash(vs["web1"])


# --- per-host resolution and the two read forms -------------------------------


def test_each_host_resolves_its_own_values():
    inventory = [
        entry("web1", apache={"listen_port": 8080}),
        entry("web2", apache={"listen_port": 9090}),
    ]

    vs = resolve_vars(ApacheVars, "apache", inventory)

    assert vs["web1"].listen_port == 8080
    assert vs["web2"].listen_port == 9090


def test_for_host_reads_by_host_object():
    vs = resolve_vars(ApacheVars, "apache", [entry(apache={"listen_port": 8080})])

    assert vs.for_host(Host(name="web1")) == vs["web1"]
    assert vs.for_host(Host(name="web1")).listen_port == 8080


def test_a_host_the_inventory_did_not_carry_raises_key_error():
    vs = resolve_vars(ApacheVars, "apache", [entry()])

    with pytest.raises(KeyError):
        vs["web9"]
    with pytest.raises(KeyError):
        vs.for_host(Host(name="web9"))


def test_the_role_name_is_recorded_on_the_vars_set():
    vs = resolve_vars(ApacheVars, "apache", [entry()])

    assert vs.role == "apache"
    assert isinstance(vs, VarsSet)


def test_an_empty_inventory_resolves_to_no_values():
    vs = resolve_vars(ApacheVars, "apache", [])

    assert dict(vs.values) == {}


# --- immutability -------------------------------------------------------------


def test_the_values_mapping_is_read_only():
    vs = resolve_vars(ApacheVars, "apache", [entry()])

    with pytest.raises(TypeError):
        vs.values["web1"] = ApacheVars()


def test_the_vars_set_is_frozen():
    vs = resolve_vars(ApacheVars, "apache", [entry()])

    with pytest.raises(dataclasses.FrozenInstanceError):
        vs.role = "other"
