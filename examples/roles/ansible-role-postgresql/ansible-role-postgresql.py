# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the PostgreSQL Ansible role (``geerlingguy.postgresql``).

The role at ``https://github.com/geerlingguy/ansible-role-postgresql`` installs
PostgreSQL from the distribution packages, initializes the cluster, writes
``postgresql.conf`` / ``pg_hba.conf``, starts the service, then manages
databases, users and object privileges. This program is the whole default
(RedHat) path expressed as reemote deploys, actions and one handler. Every
role variable is read from the inventory entry's extra keys (reemote's
``host_vars`` mechanism), so ``postgresql_users`` and friends live beside
``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``\"RedHat\"`` OS family (CentOS Stream 9 or 10).
Every request carries ``when=is_redhat``; the Alpine and Ubuntu hosts in the
shared inventory are never contacted. The role's ``include_vars`` dispatch
(``vars/RedHat-<major>.yml``, skipped for Fedora/Amazon) collapses to the
frozen ``REDHAT_VARS`` table, resolved from a ``LinuxDistribution`` gather in
``main()``. ``postgresql_version`` is informational on RedHat (only Debian
daemon names interpolate it), so the table pins it but nothing reads it.

The library gaps this migration found are CLOSED on main
--------------------------------------------------------

* **#27** -- ``dnf.packages(exclude=...)`` is the ``yum: exclude=`` parity
  kwarg; the role's ``exclude: python-unversioned-command`` rides the install
  command directly instead of smuggling ``--exclude`` through
  ``extra_install_args``.
* **#28** -- the PostgreSQL facts re-read elevated when peer auth denies the
  login user, so ``PostgresqlRoles`` and the operations' fact reads work
  against the role's peer-only ``pg_hba.conf`` with an inventory
  ``privilege.sudo_user = "postgres"`` view.
* **#29** -- ``postgresql.privilege`` covers the ``postgresql_privs`` object
  types this role documents (table/schema/sequence/function/database/
  tablespace/default_privs, ``ALL_IN_SCHEMA``/``ALL_DEFAULT``, grant_option,
  target_roles, session_role). The exotic types (procedure, language, group,
  foreign_data_wrapper, foreign_server, type, parameter) are the deliberate
  scope boundary of that closed issue: an item naming one is rejected
  control-side with a message citing #29, not silently dropped.
* **#30** -- ``server.FipsEnabled`` backs the role's
  ``ansible_facts.fips | ternary('scram-sha-256', 'md5')`` auth-method default.
* **#31** -- ``postgresql.role(bypassrls=...)`` is the tri-state
  ``BYPASSRLS``/``NOBYPASSRLS`` role_attr_flags parity flag.
* **#32** -- ``dnf.packages(enable_repos=...)`` is the additive
  ``yum: enablerepo=`` parity kwarg (``repos=`` would disable every other
  repo -- a different Ansible spelling).
* **#44** (the former GAP-1) -- ``systemd.service(sleep=)`` is the ``service``
  module's ``sleep:`` parity: it splits a restart into ``systemctl stop``, a
  raw ``sleep N`` and ``systemctl start``. ``build_handler`` passes ``sleep=5``,
  the role's own ``handlers/main.yml`` value.
* **#45** (the former GAP-2) -- ``postgresql.role``'s eight attribute options
  are all tri-state (``bool | None``), so on an existing role only the flags a
  request lists are reconciled and an unlisted attribute is left alone -- the
  Ansible ``role_attr_flags`` partial semantics, natively. ``build_users`` and
  ``build_users_props`` now pass exactly the listed flags; the former
  ``PostgresqlRoles`` catalog freeze and its unlisted-flag fill are gone.

Why this shape
--------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role's
``stat`` + ``register`` + ``when: not …exists`` initdb gate and its
``become_user: postgres`` tasks read mutable state / switch run-as users, so
they move out of the deploys: the stat gate is an elevated ``Shell`` action
(the data directory is 0700 postgres -- the unprivileged ``FileExists`` fact
cannot traverse it), and ``initdb`` is a ``Shell`` action on a postgres-privilege
inventory view. The handler is the two-deploy split:
the notifying requests carry names, ``changed_hosts`` freezes the notified
set between runs, and ``service_handler`` restarts only those hosts.

Deliberate deviations from Ansible semantics
--------------------------------------------

Recorded at the point of use and in
``docs/examples/roles/ansible-role-postgresql.md``:

* credentials are out of scope (the namespace credentials decision):
  ``password``/``encrypted``/``login_password``/``VALID UNTIL`` are dropped;
  every connection rides peer auth over the unix socket as the OS postgres
  user (``privilege.sudo_user`` from the inventory). ``PGOPTIONS``
  (password_encryption) and ``no_log`` are moot with no password to protect.
* ``login_host`` is ignored: Ansible's ``login_unix_socket`` wins over
  ``login_host`` in psycopg2, so the effective connection is always the
  socket (``psql_host=<socket dir>``).
* a duplicate user name fails fast instead of Ansible's last-item-wins, and a
  ``state: absent`` user is not created first (the role's users.yml creates
  every user before users_props.yml applies state). ``role_attr_flags``
  partial reconciliation is now native (#45), not a control-side fill.
* ``postgresql_db``'s ``encoding: UTF-8`` default is normalized to the
  catalog spelling ``UTF8`` (PostgreSQL accepts both on CREATE; the fact
  reports ``UTF8``).
* ``fail_on_role`` is absorbed (explicitly out of scope per #29); the
  deprecated ``priv`` setting still prints the role's debug warning.
* ``ansible_ssh_pipelining`` is moot (reemote owns its transport).
* the psycopg2 Python library is still installed for parity even though
  reemote drives the ``psql`` CLI, not psycopg2.

Control-side dependencies: reemote plus **Jinja2** (the two templates are
pre-rendered in ``main()``; the import lives inside the render functions).
The target needs ``dnf``/``systemd`` and the PostgreSQL packages the role
installs -- stock on any RHEL-family host.

Run it with::

    uv run --with jinja2 python examples/roles/ansible-role-postgresql/ansible-role-postgresql.py
"""

import asyncio
import json
import posixpath
import re
import shlex
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FipsEnabled,
    Host,
    HostConnectionError,
    InventoryError,
    LinuxDistribution,
    OperationRequest,
    PrivilegeError,
    Shell,
    changed_hosts,
    compose,
    dnf,
    files,
    format_log,
    format_plan,
    gather_action,
    gather_fact,
    op,
    postgresql,
    run,
    service_handler,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/RedHat-<major>.yml) -------------

#: The frozen ``include_vars`` dispatch: vars/RedHat-9.yml and
#: vars/RedHat-10.yml (identical but for the informational version). A major
#: outside this table is rejected before any host is mutated.
REDHAT_VARS: dict[int, dict[str, Any]] = {
    9: {
        "version": "13",
        "data_dir": "/var/lib/pgsql/data",
        "bin_path": "/usr/bin",
        "config_path": "/var/lib/pgsql/data",
        "daemon": "postgresql",
        "packages": ("postgresql", "postgresql-server", "postgresql-contrib"),
        "socket_mode": "0755",
        "python_library": "python3-psycopg2",
    },
    10: {
        "version": "18",
        "data_dir": "/var/lib/pgsql/data",
        "bin_path": "/usr/bin",
        "config_path": "/var/lib/pgsql/data",
        "daemon": "postgresql",
        "packages": ("postgresql", "postgresql-server", "postgresql-contrib"),
        "socket_mode": "0755",
        "python_library": "python3-psycopg2",
    },
}

#: The role defaults this migration honours (``defaults/main.yml``). Keys the
#: RedHat vars file overrides (python library, socket mode, paths, daemon,
#: packages) are deliberately absent: ``resolved_vars`` fills them from
#: ``REDHAT_VARS`` unless the user pins them in ``host.extra``.
ROLE_DEFAULTS: dict[str, Any] = {
    "postgresql_enablerepo": "",
    "postgresql_restarted_state": "restarted",
    "postgresql_user": "postgres",
    "postgresql_group": "postgres",
    "postgresql_auth_method": None,
    "postgresql_unix_socket_directories": ("/var/run/postgresql",),
    "postgresql_service_state": "started",
    "postgresql_service_enabled": True,
    "postgresql_global_config_options": None,
    "postgresql_hba_entries": None,
    "postgresql_databases": (),
    "postgresql_users": (),
    "postgresql_privs": (),
}

#: The requests that ``notify: restart postgresql`` (initialize.yml's profile
#: template, configure.yml's lineinfile loop and pg_hba template). Names are
#: the notification channel: ``changed_hosts`` selects them from the reports.
NOTIFY_RESTART = frozenset(
    {
        "Set PostgreSQL environment variables.",
        "Configure global settings.",
        "Configure host based authentication.",
    }
)

#: templates/postgres.sh.j2, verbatim.
POSTGRES_SH_TEMPLATE = (
    "export PGDATA={{ postgresql_data_dir }}\n"
    "export PATH=$PATH:{{ postgresql_bin_path }}\n"
)

#: templates/pg_hba.conf.j2, verbatim. The empty ``default('')`` fields leave
#: extra/trailing spaces on local rows -- reproduced byte-exactly.
PG_HBA_TEMPLATE = (
    "{{ ansible_managed | comment }}\n"
    "# PostgreSQL Client Authentication Configuration File\n"
    "# ===================================================\n"
    "#\n"
    "# See: https://www.postgresql.org/docs/current/static/auth-pg-hba-conf.html\n"
    "\n"
    "{% for client in postgresql_hba_entries %}\n"
    "{{ client.type }} {{ client.database }} {{ client.user }} "
    "{{ client.address|default('') }} {{ client.ip_address|default('') }} "
    "{{ client.ip_mask|default('') }} {{ client.auth_method }} "
    "{{ client.auth_options|default(\"\") }}\n"
    "{% endfor %}\n"
)

#: Ansible ``role_attr_flags`` tokens -> (option, value). BYPASSRLS is the
#: #31 parity flag; the six others are the #45 partial-reconcile flags. Only
#: the tokens an item lists reach ``postgresql.role`` -- an unlisted attribute
#: is left alone on an existing role and falls back to its ``CREATE ROLE``
#: default on an absent one, so no control-side fill table is needed.
ROLE_FLAG_TOKENS: dict[str, tuple[str, bool]] = {
    "SUPERUSER": ("superuser", True),
    "NOSUPERUSER": ("superuser", False),
    "CREATEDB": ("createdb", True),
    "NOCREATEDB": ("createdb", False),
    "CREATEROLE": ("createrole", True),
    "NOCREATEROLE": ("createrole", False),
    "INHERIT": ("inherit", True),
    "NOINHERIT": ("inherit", False),
    "LOGIN": ("login", True),
    "NOLOGIN": ("login", False),
    "REPLICATION": ("replication", True),
    "NOREPLICATION": ("replication", False),
    "BYPASSRLS": ("bypassrls", True),
    "NOBYPASSRLS": ("bypassrls", False),
}

#: The ``postgresql_privs`` object types ``postgresql.privilege`` covers
#: (#29). Anything else is that closed issue's deliberate scope boundary.
PRIVS_TYPES = frozenset(
    {"table", "schema", "sequence", "function", "database", "tablespace", "default_privs"}
)

#: Ansible's ``postgresql_restarted_state`` -> ``service_handler`` action.
HANDLER_ACTIONS = {"restarted": "restarted", "reloaded": "reloaded", "started": "running"}


# --- pure helpers -------------------------------------------------------------



def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data and stays a stable module-level function,
    so both deploy phases reach the same eligibility decision. A skipped
    request is never materialized, so the Alpine and Ubuntu hosts in the shared
    inventory are not contacted at all.
    """
    return host.family == "RedHat"


def _ansible_bool(value: Any, key: str) -> bool:
    """Ansible's ``| bool`` filter: YAML 1.1 booleans, case-insensitive."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{key} must be a boolean, got {value!r}")
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off"}:
            return False
    raise ValueError(
        f"{key} must be a boolean (yes/no/true/false/on/off/1/0), got {value!r}"
    )


def _ansible_comment(text: Any) -> str:
    """Ansible's ``| comment`` filter, default plain style.

    ``"Ansible managed"`` becomes ``"# Ansible managed"``; an empty line
    becomes a bare ``#``. Only the default style the role's template uses.
    """
    return "\n".join(("# " + line).rstrip() for line in str(text).splitlines())


def _jinja_env() -> Any:
    """The Ansible template environment: trim_blocks, keep_trailing_newline.

    Jinja2 is imported here, not at module scope: importing the example (the
    offline test suite does) must not require it. ``comment`` is an Ansible
    filter, registered by hand.
    """
    from jinja2 import Environment, StrictUndefined

    env = Environment(
        trim_blocks=True, keep_trailing_newline=True, undefined=StrictUndefined
    )
    env.filters["comment"] = _ansible_comment
    return env


def render_profile(data_dir: str, bin_path: str) -> str:
    """Pre-render templates/postgres.sh.j2 control-side (recipe: Jinja2)."""
    return (
        _jinja_env()
        .from_string(POSTGRES_SH_TEMPLATE)
        .render(postgresql_data_dir=data_dir, postgresql_bin_path=bin_path)
    )


def render_pg_hba(entries: list[dict[str, Any]]) -> str:
    """Pre-render templates/pg_hba.conf.j2 control-side, byte-exactly."""
    return (
        _jinja_env()
        .from_string(PG_HBA_TEMPLATE)
        .render(ansible_managed="Ansible managed", postgresql_hba_entries=entries)
    )


def make_conf_transform(
    option: str, value: str, present: bool = True
) -> Callable[[str | None], str]:
    """One ``lineinfile`` task as a pure, idempotent transform.

    Ansible: ``regexp="^#?{{ option }}.+$"``, ``line="{{ option }} = '{{ value }}'"``.
    ``state: present`` replaces the LAST matching line (appending when none
    matches); ``state: absent`` removes ALL matching lines. The option is
    interpolated into the regex raw, exactly as the role does. Built once in
    ``main()`` so both deploy phases share the same function object.
    """
    pattern = re.compile("^#?" + option + ".+$", re.MULTILINE)
    line = f"{option} = '{value}'"

    def transform(text: str | None) -> str:
        content = text if text is not None else ""
        lines = content.splitlines()
        matches = [i for i, ln in enumerate(lines) if pattern.search(ln)]
        if not present:
            if not matches:
                return content
            kept = [ln for i, ln in enumerate(lines) if i not in set(matches)]
            return "\n".join(kept) + "\n" if kept else ""
        if matches:
            last = matches[-1]
            if lines[last] == line:
                return content
            lines[last] = line
        else:
            lines.append(line)
        return "\n".join(lines) + "\n"

    return transform


def parse_role_flags(value: Any) -> tuple[tuple[str, bool], ...] | None:
    """Ansible ``role_attr_flags``: comma-separated [NO]FLAG tokens.

    Returns the listed (option, bool) pairs -- ONLY the listed ones, which is
    exactly what ``postgresql.role`` reconciles now that its attribute options
    are tri-state (#45): an unlisted flag is left alone on an existing role.
    ``None`` (omitted) stays None: presence-only, touch nothing.
    """
    if value is None:
        return None
    if isinstance(value, str):
        tokens = value.split(",")
    elif isinstance(value, (list, tuple)):
        tokens = [str(token) for token in value]
    else:
        raise TypeError(f"role_attr_flags must be a string or list, got {value!r}")
    flags: dict[str, bool] = {}
    for token in tokens:
        name = token.strip().upper()
        if not name:
            continue
        if name not in ROLE_FLAG_TOKENS:
            raise ValueError(f"unknown role_attr_flags token {token!r}")
        option, flag = ROLE_FLAG_TOKENS[name]
        if option in flags and flags[option] != flag:
            raise ValueError(f"contradictory role_attr_flags for {option}")
        flags[option] = flag
    return tuple(sorted(flags.items()))


def _psql_conn(item: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """The psql connection kwargs every community.postgresql task shares.

    Deviation: ``login_host`` is ignored -- Ansible's ``login_unix_socket``
    wins over ``login_host`` in psycopg2, so the effective connection is the
    unix socket (peer auth). ``login_password`` is dropped (credentials
    boundary); the run-as user comes from the inventory privilege view.
    """
    port = item.get("port")
    if port is not None:
        port = int(port)
    return {
        "psql_user": item.get("login_user") or settings["postgresql_user"],
        "psql_host": item.get("login_unix_socket")
        or settings["postgresql_unix_socket_directories"][0],
        "psql_port": port,
    }


def normalize_users(settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """The role's ``postgresql_users`` items, validated and flattened.

    Deviation: a duplicate name fails fast (Ansible loops twice over the same
    list -- users.yml creates, users_props.yml reconciles -- so a duplicate
    silently applies last-item-wins; here it is a configuration error).
    """
    users: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in settings["postgresql_users"]:
        if not isinstance(item, dict):
            raise TypeError(f"postgresql_users items must be mappings, got {item!r}")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"postgresql_users item needs a name, got {item!r}")
        if name in seen:
            raise ValueError(f"duplicate postgresql_users name {name!r}")
        seen.add(name)
        state = item.get("state", "present")
        if state not in ("present", "absent"):
            raise ValueError(f"user {name}: state must be present/absent, got {state!r}")
        users.append(
            {
                "name": name,
                "present": state == "present",
                "flags": parse_role_flags(item.get("role_attr_flags")),
                "psql_database": item.get("login_db") or item.get("db"),
                "deprecated_priv": "priv" in item,
                **_psql_conn(item, settings),
            }
        )
    return tuple(users)


def normalize_databases(settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """The role's ``postgresql_databases`` items with the task defaults.

    Deviation: the ``UTF-8`` encoding default is normalized to the catalog
    spelling ``UTF8`` (PostgreSQL accepts both on CREATE DATABASE; the
    ``PostgresqlDatabases`` fact reports ``UTF8``).
    """
    databases: list[dict[str, Any]] = []
    for item in settings["postgresql_databases"]:
        if not isinstance(item, dict):
            raise TypeError(
                f"postgresql_databases items must be mappings, got {item!r}"
            )
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"postgresql_databases item needs a name, got {item!r}")
        state = item.get("state", "present")
        if state not in ("present", "absent"):
            raise ValueError(
                f"database {name}: state must be present/absent, got {state!r}"
            )
        databases.append(
            {
                "name": name,
                "present": state == "present",
                "owner": item.get("owner") or settings["postgresql_user"],
                "template": item.get("template") or "template0",
                "encoding": (item.get("encoding") or "UTF-8").replace("-", "").upper(),
                "lc_collate": item.get("lc_collate") or "en_US.UTF-8",
                "lc_ctype": item.get("lc_ctype") or "en_US.UTF-8",
                **_psql_conn(item, settings),
            }
        )
    return tuple(databases)


def normalize_privs(settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """The role's ``postgresql_privs`` items, mapped onto postgresql.privilege.

    Reproduces the community.postgresql.postgresql_privs argument handling:
    ``login_db`` (or the role's ``db`` alias) is required, ``roles`` is
    comma-split, ``privs`` is comma-split into a tuple of names (a lone ``ALL``
    stays the bare string the operation expands), ``type`` defaults to
    ``table``, ``objs`` defaults to the
    login_db for databases, ``ALL_IN_SCHEMA`` becomes ``all_in_schema`` (the
    schema defaulting to public), ``ALL_DEFAULT`` becomes every default-privs
    class except schemas, function argument lists turn ``:`` into ``,``, and
    table/sequence/function objects are schema-qualified. ``fail_on_role`` is
    absorbed (out of scope per #29); an exotic type is rejected citing #29.
    """
    privs: list[dict[str, Any]] = []
    for item in settings["postgresql_privs"]:
        if not isinstance(item, dict):
            raise TypeError(f"postgresql_privs items must be mappings, got {item!r}")
        login_db = item.get("login_db") or item.get("db")
        if not isinstance(login_db, str) or not login_db:
            raise ValueError(f"postgresql_privs item needs login_db (or db): {item!r}")
        roles = item.get("roles")
        if isinstance(roles, str):
            role_tuple = tuple(r.strip() for r in roles.split(",") if r.strip())
        elif isinstance(roles, (list, tuple)):
            role_tuple = tuple(str(r) for r in roles)
        else:
            role_tuple = ()
        if not role_tuple:
            raise ValueError(f"postgresql_privs for {login_db}: roles is required")
        obj_type = item.get("type") or "table"
        if obj_type not in PRIVS_TYPES:
            raise ValueError(
                f"postgresql_privs type {obj_type!r} is beyond reemote's "
                f"postgresql.privilege scope (the deliberate boundary of closed "
                f"issue #29); supported: {', '.join(sorted(PRIVS_TYPES))}"
            )
        raw_privs = item.get("privs")
        if isinstance(raw_privs, str):
            tokens = [t.strip() for t in raw_privs.split(",") if t.strip()]
        elif isinstance(raw_privs, (list, tuple)):
            tokens = [
                t.strip() for p in raw_privs for t in str(p).split(",") if t.strip()
            ]
        else:
            tokens = []
        if not tokens:
            raise ValueError(f"postgresql_privs for {login_db}: privs is required")
        # reemote's postgresql.privilege takes a single privilege name, the
        # string "ALL" (expanded to the type's full set), or an iterable of
        # names -- NOT Ansible's comma-joined string. A lone ALL stays the bare
        # string so the operation expands it; anything else becomes a tuple of
        # tokens (hashable for op()).
        privileges: str | tuple[str, ...] = (
            "ALL" if len(tokens) == 1 and tokens[0].upper() == "ALL" else tuple(tokens)
        )
        schema = item.get("schema")
        if schema is not None and obj_type in ("database", "tablespace", "schema"):
            raise ValueError(
                f"postgresql_privs type {obj_type!r} does not take a schema"
            )
        objs = item.get("objs")
        objects: tuple[str, ...] | None = None
        all_in_schema: str | None = None
        if obj_type == "database":
            objs = objs or login_db
            objects = (objs,) if isinstance(objs, str) else tuple(objs)
        elif objs == "ALL_IN_SCHEMA":
            if obj_type not in ("table", "sequence", "function"):
                raise ValueError(
                    f"ALL_IN_SCHEMA is not valid for type {obj_type!r}"
                )
            all_in_schema = schema or "public"
        elif objs == "ALL_DEFAULT":
            if obj_type != "default_privs":
                raise ValueError("ALL_DEFAULT is only valid for type default_privs")
            objects = ("tables", "sequences", "functions", "types")
        elif obj_type == "default_privs":
            if objs is not None:
                objects = (
                    tuple(o.strip() for o in objs.split(","))
                    if isinstance(objs, str)
                    else tuple(objs)
                )
            all_in_schema = schema
        elif objs is None:
            raise ValueError(
                f"postgresql_privs for {login_db}: objs is required for type {obj_type!r}"
            )
        else:
            obj_list = (
                [o.strip() for o in objs.split(",")]
                if isinstance(objs, str)
                else [str(o) for o in objs]
            )
            if obj_type == "function":
                obj_list = [o.replace(":", ",") for o in obj_list]
            if obj_type in ("table", "sequence", "function"):
                qual = schema or "public"
                obj_list = [o if "." in o else f"{qual}.{o}" for o in obj_list]
            objects = tuple(obj_list)
        state = item.get("state", "present")
        if state not in ("present", "absent"):
            raise ValueError(
                f"postgresql_privs for {login_db}: state must be present/absent"
            )
        target_roles = item.get("target_roles")
        if isinstance(target_roles, str):
            target_roles = tuple(r.strip() for r in target_roles.split(",") if r.strip())
        elif isinstance(target_roles, (list, tuple)):
            target_roles = tuple(str(r) for r in target_roles)
        privs.append(
            {
                "roles": role_tuple,
                "privileges": privileges,
                "object_type": obj_type,
                "objects": objects,
                "all_in_schema": all_in_schema,
                "present": state == "present",
                "grant_option": _ansible_bool(
                    item.get("grant_option", False), "grant_option"
                ),
                "target_roles": target_roles,
                "session_role": item.get("session_role"),
                "psql_database": login_db,
                **_psql_conn(item, settings),
            }
        )
    return tuple(privs)


def role_settings(host: Host) -> dict[str, Any]:
    """Resolve a host's role variables: defaults overlaid with ``host.extra``.

    Validates the scalars before any host is contacted, so a bad value fails
    fast with a clear message rather than mid-deploy.
    """
    settings = {**ROLE_DEFAULTS, **host.extra}
    settings["postgresql_service_enabled"] = _ansible_bool(
        settings["postgresql_service_enabled"], "postgresql_service_enabled"
    )
    state = settings["postgresql_service_state"]
    if state not in ("started", "stopped"):
        raise ValueError(
            f"postgresql_service_state must be started/stopped, got {state!r}"
        )
    restarted = settings["postgresql_restarted_state"]
    if restarted not in HANDLER_ACTIONS:
        raise ValueError(
            f"postgresql_restarted_state must be one of "
            f"{', '.join(sorted(HANDLER_ACTIONS))}, got {restarted!r}"
        )
    auth = settings["postgresql_auth_method"]
    if auth is not None and auth not in ("md5", "scram-sha-256"):
        raise ValueError(f"postgresql_auth_method must be md5/scram-sha-256, got {auth!r}")
    sockets = settings["postgresql_unix_socket_directories"]
    if isinstance(sockets, str):
        sockets = [sockets]
    if not isinstance(sockets, (list, tuple)) or not sockets:
        raise ValueError("postgresql_unix_socket_directories must be a non-empty list")
    for socket_dir in sockets:
        if not isinstance(socket_dir, str) or not socket_dir.startswith("/"):
            raise ValueError(
                f"unix socket directory must be an absolute path, got {socket_dir!r}"
            )
    settings["postgresql_unix_socket_directories"] = tuple(sockets)
    for key in ("postgresql_user", "postgresql_group", "postgresql_enablerepo"):
        if not isinstance(settings[key], str):
            raise TypeError(f"{key} must be a string, got {settings[key]!r}")
    return settings


def resolved_vars(
    settings: dict[str, Any], major: int, fips: bool
) -> dict[str, Any]:
    """The plan's static half: the frozen vars table plus the resolved defaults.

    ``major`` is the gathered ``LinuxDistribution`` major version (the
    ``include_vars`` dispatch key); ``fips`` is the gathered ``FipsEnabled``
    (the ``ansible_facts.fips | ternary(...)`` auth-method default, #30).
    Everything here is decided control-side before the deploys run.
    """
    if major not in REDHAT_VARS:
        raise ValueError(
            f"no RedHat vars pinned for major version {major}; "
            f"supported: {tuple(sorted(REDHAT_VARS))}"
        )
    rv = REDHAT_VARS[major]
    data_dir = settings.get("postgresql_data_dir") or rv["data_dir"]
    bin_path = settings.get("postgresql_bin_path") or rv["bin_path"]
    config_path = settings.get("postgresql_config_path") or rv["config_path"]
    socket_dirs = settings["postgresql_unix_socket_directories"]
    auth_method = settings["postgresql_auth_method"] or (
        "scram-sha-256" if fips else "md5"
    )
    raw_options = settings["postgresql_global_config_options"] or (
        {"option": "unix_socket_directories", "value": ",".join(socket_dirs)},
        {"option": "log_directory", "value": "log"},
    )
    options: list[tuple[str, str, bool]] = []
    for item in raw_options:
        if not isinstance(item, dict) or "option" not in item or "value" not in item:
            raise ValueError(
                f"postgresql_global_config_options item needs option/value: {item!r}"
            )
        state = item.get("state", "present")
        if state not in ("present", "absent"):
            raise ValueError(
                f"config option {item['option']}: state must be present/absent"
            )
        options.append((str(item["option"]), str(item["value"]), state == "present"))
    values = {option: value for option, value, _ in options}
    if "log_directory" not in values:
        raise ValueError(
            "postgresql_global_config_options must define log_directory "
            "(the role derives postgresql_effective_log_dir from it)"
        )
    log_dir = values["log_directory"]
    effective_log_dir = (
        log_dir if log_dir.startswith("/") else posixpath.join(data_dir, log_dir)
    )
    entries = settings["postgresql_hba_entries"]
    if entries is None:
        entries = [
            {"type": "local", "database": "all", "user": "postgres", "auth_method": "peer"},
            {"type": "local", "database": "all", "user": "all", "auth_method": "peer"},
            {"type": "host", "database": "all", "user": "all",
             "address": "127.0.0.1/32", "auth_method": auth_method},
            {"type": "host", "database": "all", "user": "all",
             "address": "::1/128", "auth_method": auth_method},
        ]
    if not isinstance(entries, (list, tuple)):
        raise TypeError("postgresql_hba_entries must be a list of mappings")
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str)
            for key in ("type", "database", "user", "auth_method")
        ):
            raise ValueError(
                f"postgresql_hba_entries item needs type/database/user/auth_method: {entry!r}"
            )
    enablerepo = settings["postgresql_enablerepo"]
    enable_repos = tuple(r.strip() for r in enablerepo.split(",") if r.strip()) or None
    return {
        "version": settings.get("postgresql_version") or rv["version"],
        "packages": tuple(settings.get("postgresql_packages") or rv["packages"]),
        "python_library": settings.get("postgresql_python_library") or rv["python_library"],
        "daemon": settings.get("postgresql_daemon") or rv["daemon"],
        "data_dir": data_dir,
        "bin_path": bin_path,
        "config_path": config_path,
        "socket_mode": settings.get("postgresql_unix_socket_directories_mode")
        or rv["socket_mode"],
        "socket_dirs": socket_dirs,
        "pg_user": settings["postgresql_user"],
        "pg_group": settings["postgresql_group"],
        "auth_method": auth_method,
        "enable_repos": enable_repos,
        "options": tuple(options),
        "log_dir": effective_log_dir,
        "hba_entries": list(entries),
        "service_running": settings["postgresql_service_state"] == "started",
        "service_enabled": settings["postgresql_service_enabled"],
        "handler_action": HANDLER_ACTIONS[settings["postgresql_restarted_state"]],
        "users": normalize_users(settings),
        "databases": normalize_databases(settings),
        "privs": normalize_privs(settings),
    }


# --- the deploy factories (close over the frozen per-host plans) --------------


def build_setup(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 1: setup-RedHat.yml's two yum tasks.

    ``exclude`` (#27, closed) rides the package install only -- the Python
    library task has no exclude, exactly like the role. ``enable_repos``
    (#32, closed) is the additive ``enablerepo`` parity kwarg; the role's
    ``default(omit, true)`` on the empty string becomes None.
    """

    def setup(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        yield op(
            dnf.packages,
            name="Ensure PostgreSQL packages are installed.",
            packages=plan["packages"],
            exclude=("python-unversioned-command",),
            enable_repos=plan["enable_repos"],
            sudo=True,
            when=is_redhat,
        )
        yield op(
            dnf.packages,
            name="Ensure PostgreSQL Python libraries are installed.",
            packages=(plan["python_library"],),
            enable_repos=plan["enable_repos"],
            sudo=True,
            when=is_redhat,
        )

    return setup


def build_initialize(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2: initialize.yml's profile template and data directory.

    The profile write notifies the restart handler (its name is in
    ``NOTIFY_RESTART``). The stat/initdb/log-dir tasks are NOT here: the stat
    gate and initdb are actions between deploys (mutable state / run-as
    postgres), and the log directory leads the configure deploy so it is
    created after initdb, exactly where the role creates it.
    """

    def initialize(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        yield op(
            files.install,
            name="Set PostgreSQL environment variables.",
            content=plan["profile_content"],
            dest="/etc/profile.d/postgres.sh",
            mode="0644",
            sudo=True,
            when=is_redhat,
        )
        yield op(
            files.directory,
            name="Ensure PostgreSQL data directory exists.",
            path=plan["data_dir"],
            user=plan["pg_user"],
            group=plan["pg_group"],
            mode="0700",
            sudo=True,
            when=is_redhat,
        )

    return initialize


def build_configure(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 3: the log directory, configure.yml's lineinfile loop, pg_hba
    template and unix socket directories.

    Each ``files.transform`` is one lineinfile item; the transform objects
    were built ONCE in ``main()`` (stable across both phases). The conf file
    exists by now -- initdb created it (or the cluster was already
    initialized). ``files.install``'s ``cp`` preserves the existing owner, so
    the postgres-owned postgresql.conf stays postgres-owned like lineinfile
    leaves it; pg_hba.conf is explicitly postgres:postgres 0600.
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        yield op(
            files.directory,
            name="Ensure PostgreSQL log directory exists.",
            path=plan["log_dir"],
            user=plan["pg_user"],
            group=plan["pg_group"],
            mode="0700",
            sudo=True,
            when=is_redhat,
        )
        for transform in plan["conf_transforms"]:
            yield op(
                files.transform,
                name="Configure global settings.",
                path=plan["conf_path"],
                transform=transform,
                mode="0644",
                sudo=True,
                when=is_redhat,
            )
        if plan["hba_content"] is not None:
            yield op(
                files.install,
                name="Configure host based authentication.",
                content=plan["hba_content"],
                dest=plan["hba_path"],
                mode="0600",
                user=plan["pg_user"],
                group=plan["pg_group"],
                sudo=True,
                when=is_redhat,
            )
        for socket_dir in plan["socket_dirs"]:
            yield op(
                files.directory,
                name="Ensure PostgreSQL unix socket dirs exist.",
                path=socket_dir,
                user=plan["pg_user"],
                group=plan["pg_group"],
                mode=plan["socket_mode"],
                sudo=True,
                when=is_redhat,
            )

    return configure


def build_service(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 4: main.yml's started-and-enabled service task."""

    def service(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        yield op(
            systemd.service,
            name="Ensure PostgreSQL is started and enabled on boot.",
            service=plan["daemon"],
            running=plan["service_running"],
            enabled=plan["service_enabled"],
            sudo=True,
            when=is_redhat,
        )

    return service


def build_users(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 5a: users.yml -- ensure the users exist, touch nothing else.

    Ansible's postgresql_user WITHOUT role_attr_flags/password is a
    presence-only no-op on an existing role and CREATE USER (server defaults)
    on an absent one. ``postgresql.role`` matches that natively now that its
    attribute options are tri-state (#45): a request listing no attribute is a
    no-op on an existing role and creates an absent one with the ``CREATE
    ROLE`` defaults. So this pass yields one presence-only request per present
    user and lets the operation's own ``PostgresqlRoles`` read decide CREATE vs
    no-op -- no frozen catalog and no explicit default flags. The reconcile
    pass (build_users_props) applies the listed flags.

    Deviation: a ``state: absent`` item is not created first (the role's
    users.yml creates every user before users_props.yml applies state).
    """

    def users(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        for user in plan["users"]:
            if not user["present"]:
                continue
            yield op(
                postgresql.role,
                name="Ensure PostgreSQL users are present.",
                role=user["name"],
                psql_user=user["psql_user"],
                psql_host=user["psql_host"],
                psql_port=user["psql_port"],
                sudo=True,
                when=is_redhat,
            )

    return users


def build_databases(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 5b: databases.yml -- the postgresql_db loop.

    The creation-only parameters (template/encoding/locales) are reported
    skipped on an existing database, mirroring postgresql_db's immutability;
    the owner is reconciled. Runs after users so an owning role exists.
    """

    def databases(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        for database in plan["databases"]:
            yield op(
                postgresql.database,
                name="Ensure PostgreSQL databases are present.",
                database=database["name"],
                present=database["present"],
                owner=database["owner"],
                template=database["template"],
                encoding=database["encoding"],
                lc_collate=database["lc_collate"],
                lc_ctype=database["lc_ctype"],
                psql_user=database["psql_user"],
                psql_host=database["psql_host"],
                psql_port=database["psql_port"],
                sudo=True,
                when=is_redhat,
            )

    return databases


def build_users_props(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 5c: users_props.yml -- attribute reconciliation and privileges.

    Ansible's role_attr_flags adjusts ONLY the listed flags, and
    ``postgresql.role``'s tri-state options now do exactly that (#45): this
    pass forwards the item's listed flags verbatim, so an unlisted attribute
    is left alone on an existing role, and an absent role (deploy 5a creates
    it with the ``CREATE ROLE`` defaults) reconciles only the listed flags.
    ``bypassrls`` rides the same listed-flag path (#31); ``connection_limit``
    is not a role_attr_flags token. An item without flags is presence-only:
    deploy 5a already ensured it.
    """

    def users_props(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        for user in plan["users"]:
            conn = {
                "psql_user": user["psql_user"],
                "psql_host": user["psql_host"],
                "psql_port": user["psql_port"],
                "psql_database": user["psql_database"],
            }
            if not user["present"]:
                yield op(
                    postgresql.role,
                    name="Ensure PostgreSQL users are configured correctly.",
                    role=user["name"],
                    present=False,
                    **conn,
                    sudo=True,
                    when=is_redhat,
                )
                continue
            if not user["flags"]:
                continue
            yield op(
                postgresql.role,
                name="Ensure PostgreSQL users are configured correctly.",
                role=user["name"],
                **dict(user["flags"]),
                **conn,
                sudo=True,
                when=is_redhat,
            )
        for priv in plan["privs"]:
            yield op(
                postgresql.privilege,
                name="Ensure PostgreSQL users privileges are configured correctly.",
                privileges=priv["privileges"],
                role=priv["roles"],
                object_type=priv["object_type"],
                objects=priv["objects"],
                all_in_schema=priv["all_in_schema"],
                present=priv["present"],
                grant_option=priv["grant_option"],
                target_roles=priv["target_roles"],
                session_role=priv["session_role"],
                psql_user=priv["psql_user"],
                psql_host=priv["psql_host"],
                psql_port=priv["psql_port"],
                psql_database=priv["psql_database"],
                sudo=True,
                when=is_redhat,
            )

    return users_props


def build_handler(
    plans: dict[str, Any], notified: frozenset[str]
) -> Callable[[Host], Generator[OperationRequest]]:
    """The handler: handlers/main.yml's ``restart postgresql``.

    Issue #44 closed the sleep gap: ``sleep=5`` is the role's own value, so
    ``systemd.service`` renders stop / ``sleep 5`` / start instead of one
    ``systemctl restart``. The pause is inert when the state is overridden to
    something other than ``restarted`` -- exactly as in Ansible, where
    ``sleep:`` only applies to a restart. The notified set is frozen between
    runs; an empty set yields an empty deploy, the converged no-op Ansible's
    handler gives for free.
    """

    def handler(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None or host.name not in notified:
            return
        yield from service_handler(
            (host.name,),
            (plan["daemon"],),
            action=plan["handler_action"],
            sleep=5,
            when=is_redhat,
        )(host)

    return handler


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot, which is what makes the determinism
    guard hold.
    """
    plans, _dry, _hosts = await run(inventory, deploy, dry=True)
    print(f"\n== {title} -- plan (prepare phase, --dry) ==")
    print(format_plan(plans))
    _plans, report, _hosts = await run(inventory, deploy)
    print(f"\n== {title} -- run log (apply phase) ==")
    assert report is not None
    print(format_log(report))
    return report


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        targets = [host for host in hosts if is_redhat(host)]
        skipped = [host.name for host in hosts if not is_redhat(host)]
        if skipped:
            print("\n== Skipped (when guard is_redhat): " + ", ".join(skipped) + " ==")
        if not targets:
            print("\nNo host in the RedHat family; nothing to deploy.")
            return

        # The role's variable resolution, before any host is contacted.
        settings = {host.name: role_settings(host) for host in targets}

        # The role's fact reads, frozen: LinuxDistribution supplies the
        # include_vars dispatch key (and guards the RedHat assumption),
        # FipsEnabled supplies the auth-method ternary (#30). gather_fact
        # does not close its hosts, so the finally does.
        plans: dict[str, Any] = {}
        try:
            distributions = await gather_fact(targets, LinuxDistribution)
            fips = await gather_fact(targets, FipsEnabled)
            for host in targets:
                info = distributions[host.name]
                name = str(info.get("name") or "")
                if info.get("family") != "RedHat" or name.startswith(
                    ("Fedora", "Amazon")
                ):
                    raise ValueError(
                        f"{host.name}: declared RedHat family but the distribution is "
                        f"{name!r} (family {info.get('family')!r}); the role's "
                        "RedHat vars dispatch excludes Fedora and Amazon"
                    )
                major = info.get("major")
                if not isinstance(major, int):
                    # TypeError (not ValueError) for a type check -- ruff
                    # TRY004; main()'s except tuple covers both.
                    raise TypeError(
                        f"{host.name}: cannot read the distribution major "
                        f"version from {info!r}"
                    )
                rv = resolved_vars(settings[host.name], major, bool(fips[host.name]))
                plans[host.name] = {
                    **rv,
                    "major": major,
                    "profile_content": render_profile(rv["data_dir"], rv["bin_path"]),
                    "hba_content": render_pg_hba(rv["hba_entries"])
                    if rv["hba_entries"]
                    else None,
                    "conf_path": posixpath.join(rv["config_path"], "postgresql.conf"),
                    "hba_path": posixpath.join(rv["config_path"], "pg_hba.conf"),
                    "conf_transforms": tuple(
                        make_conf_transform(option, value, present)
                        for option, value, present in rv["options"]
                    ),
                    "pg_version_path": posixpath.join(rv["data_dir"], "PG_VERSION"),
                    "initdb_command": f"{rv['bin_path']}/initdb -D {rv['data_dir']}",
                }
        finally:
            for host in targets:
                await host.aclose()

        # The stat register, frozen. Deviation from the stat->FileExists
        # recipe: the data directory is 0700 postgres, so the unprivileged
        # FileExists fact cannot traverse it -- the gate rides an elevated
        # Shell action instead (Ansible's stat ran under play-level become).
        for host in targets:
            plan = plans[host.name]
            result = (
                await gather_action(
                    [host],
                    Shell,
                    command=f"test -e {shlex.quote(plan['pg_version_path'])}",
                    sudo=True,
                )
            )[host.name]
            plan["initialized"] = result["exit_status"] == 0

        # The role's deprecated-privileges debug warning (users_props.yml).
        for host in targets:
            for user in plans[host.name]["users"]:
                if user["deprecated_priv"]:
                    print(
                        f"WARNING: PostgreSQL user {user['name']} uses deprecated "
                        "privileges settings. See "
                        "https://github.com/geerlingguy/ansible-role-postgresql/issues/254"
                    )

        print("\n== Frozen PostgreSQL plans ==")
        for host in targets:
            plan = plans[host.name]
            state = "initialized" if plan["initialized"] else "fresh, will initdb"
            print(
                f"{host.name}: PostgreSQL {plan['version']} (EL{plan['major']}) "
                f"-> {plan['data_dir']} ({state}), auth={plan['auth_method']}"
            )

        # The become_user: postgres view: a second inventory whose privilege
        # section names the run-as user. Run-as users come from the inventory,
        # never from an operation -- so the elevated initdb action and the
        # users/databases/privs deploy both ride this view.
        target_names = {host.name for host in targets}
        pg_inventory = [
            {
                **entry,
                "privilege": {
                    **entry.get("privilege", {}),
                    "sudo_user": settings[entry["host"]]["postgresql_user"],
                },
            }
            for entry in inventory
            if entry["host"] in target_names
        ]
        pg_hosts = [Host.from_inventory(entry) for entry in pg_inventory]
        pg_by_name = {host.name: host for host in pg_hosts}

        report_setup = await execute(
            inventory, build_setup(plans), "Deploy 1: install the PostgreSQL packages"
        )
        report_initialize = await execute(
            inventory,
            build_initialize(plans),
            "Deploy 2: profile environment and data directory",
        )

        # initialize.yml's command task: initdb as postgres, only where the
        # frozen stat gate says the cluster is absent. An action, not a
        # deploy request -- commands never run inside a deploy. The
        # postgres-privilege hosts serve only this elevated action, so the
        # finally closes them; Deploy 5 builds its own hosts from pg_inventory
        # and its postgresql operations read the role catalog themselves
        # (the elevated re-read of #28) -- no frozen catalog is needed now
        # that postgresql.role reconciles only the listed flags (#45).
        try:
            for host in targets:
                plan = plans[host.name]
                if plan["initialized"]:
                    continue
                result = (
                    await gather_action(
                        [pg_by_name[host.name]],
                        Shell,
                        command=plan["initdb_command"],
                        sudo=True,
                    )
                )[host.name]
                if result["exit_status"] != 0:
                    raise ValueError(
                        f"{host.name}: initdb failed (exit {result['exit_status']}): "
                        f"{result['stderr'].strip()}"
                    )
                print(f"\n== {host.name}: cluster initialized ({plan['data_dir']}) ==")
        finally:
            for host in pg_hosts:
                await host.aclose()

        report_configure = await execute(
            inventory,
            build_configure(plans),
            "Deploy 3: postgresql.conf, pg_hba.conf and socket directories",
        )
        report_service = await execute(
            inventory, build_service(plans), "Deploy 4: start and enable the service"
        )

        report_data = await execute(
            pg_inventory,
            compose(
                build_users(plans), build_databases(plans), build_users_props(plans)
            ),
            "Deploy 5: users, databases and privileges (as postgres)",
        )

        # The handler: only hosts whose notifying requests changed restart.
        notified = changed_hosts(report_initialize, NOTIFY_RESTART) | changed_hosts(
            report_configure, NOTIFY_RESTART
        )
        report_handler = await execute(
            inventory,
            build_handler(plans, notified),
            "Handler: restart postgresql where notified",
        )
        changed = sum(
            1
            for report in (
                report_setup,
                report_initialize,
                report_configure,
                report_service,
                report_data,
                report_handler,
            )
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across 5 deploys + handler ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, or a role variable /
        # distribution read is invalid. The message is the report.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ImportError as exc:
        # The template renderer is a control-side dependency.
        print(
            f"\nERROR: {exc} -- the templates need Jinja2; run with "
            "`uv run --with jinja2 python ...`",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema; the
        # message names the offending entry by its list index.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Every host-level failure is a typed error naming the host and the
        # failing command. Print that, no traceback, and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
