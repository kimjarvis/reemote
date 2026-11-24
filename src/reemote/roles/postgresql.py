# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The PostgreSQL role (migrated from ``geerlingguy.postgresql``).
# 
# Installs PostgreSQL from the distribution packages, initializes the cluster,
# writes ``postgresql.conf`` / ``pg_hba.conf``, starts the service, then manages
# databases, users and object privileges. :func:`postgresql` is the whole
# system-level task list and :func:`postgresql_data` the data plane;
# :class:`PostgresqlVars` is ``defaults/main.yml`` overlaid with the frozen
# ``vars/RedHat-<major>.yml`` dispatch, bound from the inventory's
# ``postgresql_``-prefixed keys by :func:`~reemote.rolevars.resolve_vars`.
# 
# Target: hosts in the ``"RedHat"`` OS family (CentOS Stream 9 or 10). The role's
# ``include_vars`` dispatch (``vars/RedHat-<major>.yml``, skipped for
# Fedora/Amazon) collapses to the frozen :data:`REDHAT_VARS` table, keyed by the
# ``LinuxDistribution`` major version. ``version`` is informational on RedHat
# (only the Debian daemon name interpolates it), so the table pins it but nothing
# reads it.
# 
# Two host views, so two passes
# -----------------------------
# 
# This is the only role in the corpus that cannot be one ``run_waves`` pass, and
# the reason is a hard engine limit (design doc section 13): **run-as users come
# from the inventory, never from a request.** The system tasks run as root (the
# normal inventory view, ``sudo=True``), but the role's ``become_user: postgres``
# data plane -- and the peer-authenticated catalog read it needs -- run as the OS
# ``postgres`` user, which only an inventory ``privilege.sudo_user`` view can
# select. ``run_waves`` builds one host list and closes it in its own ``finally``,
# so nothing inside a role can switch the privilege view mid-run. The role is
# therefore **two factories**, driven as two passes by the program:
# 
# * :func:`postgresql` (normal view) -- wave 0 ``LinuxDistribution``, wave 1
#   ``FipsEnabled``, then :func:`setup` and :func:`initialize` in one wave closed
#   by ``checkpoint("initialize done")``, the ``stat`` gate and (on a fresh
#   cluster) ``initdb`` as ``act`` barriers, :func:`configure` closed by
#   ``checkpoint("configure done")``, :func:`service`, then :func:`handler`.
# * :func:`postgresql_data` (postgres view) -- the ``PostgresqlRoles`` catalog
#   fact as a reachability gate (peer auth needs the service listening, so it is
#   this pass's first barrier, not pass A's last), then :func:`users`,
#   :func:`databases` and :func:`users_props`.
# 
# The three reads the example took *between* its ``run()`` calls -- the
# distribution and FIPS gathers, the ``stat`` gate and the ``initdb`` command --
# are barriers now, so the templates are rendered inside the body from the values
# those barriers returned. Nothing is frozen in a module-level plan dict keyed by
# host name, and no name set is unioned across the inventory: the per-host body
# *is* the plan.
# 
# Deviations from the Ansible role, all deliberate:
# 
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: a host outside the ``"RedHat"`` family returns before it yields
#   anything, so it emits no requests and no log entries at all.
# * ``initdb`` stays in pass A as ``act(Shell, command="sudo -u <user> <initdb>",
#   sudo=True)``: the elevation to root comes from the inventory and the drop to
#   ``postgres`` is inside the command. It has to sit between :func:`initialize`
#   and :func:`configure`, so putting it in pass B would mean a third pass.
# * **the handler moves earlier.** Its notification union is ``initialize |
#   configure``, both in pass A, so it now runs *before* the users and databases
#   instead of after them. No data request notifies, so the reordering is safe.
# * the ``stat`` gate is an elevated ``Shell`` action, not a ``FileExists`` fact:
#   the data directory is ``0700 postgres``, so the unprivileged fact cannot
#   traverse it (Ansible's ``stat`` ran under play-level ``become``).
# * credentials are out of scope: ``password``/``encrypted``/``login_password``/
#   ``VALID UNTIL`` are dropped and every connection rides peer auth over the unix
#   socket as the OS ``postgres`` user. ``login_host`` is ignored (Ansible's
#   ``login_unix_socket`` wins in psycopg2, so the effective connection is always
#   the socket). ``PGOPTIONS`` and ``no_log`` are moot with no password.
# * a duplicate user name fails fast instead of Ansible's last-item-wins, and a
#   ``state: absent`` user is not created first. ``postgresql_db``'s ``UTF-8``
#   default is normalized to the catalog spelling ``UTF8``. ``fail_on_role`` is
#   absorbed (out of scope per closed issue #29); an exotic ``postgresql_privs``
#   type is rejected control-side citing #29. The deprecated ``priv`` setting's
#   debug warning is dropped -- a library role does not print progress.
# * the role's implicit variable gates become pydantic validation: the enums are
#   :data:`~typing.Literal` fields, an item's ``name`` is required, and a bad
#   value raises ``ValidationError`` (a ``ValueError``, so the runner maps it)
#   before any host is contacted.
# 
# The library gaps this migration originally found are all closed on main (#27
# ``dnf.packages(exclude=)``, #28 the elevated catalog re-read, #29
# ``postgresql.privilege``, #30 ``server.FipsEnabled``, #31/#45 the tri-state
# ``postgresql.role`` attribute flags, #32 ``dnf.packages(enable_repos=)``, #44
# ``systemd.service(sleep=)``), so the role drives the shipped module surface
# directly.
# """

import posixpath
import re
import shlex
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import dnf, files, systemd
from ..modules import postgresql as pg
from ..modules.postgresql import PostgresqlRoles
from ..modules.server import FipsEnabled, LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint, fact

# --- role constants (defaults/main.yml + vars/RedHat-<major>.yml) -------------

#: The frozen ``include_vars`` dispatch: ``vars/RedHat-9.yml`` and
#: ``vars/RedHat-10.yml`` (identical but for the informational version). A major
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

#: The requests that ``notify: restart postgresql`` (initialize.yml's profile
#: template, configure.yml's lineinfile loop and the pg_hba template). Names are
#: the notification channel: :func:`~reemote.handlers.changed_hosts` selects them
#: from the wave reports.
NOTIFY_RESTART = frozenset(
    {
        "Set PostgreSQL environment variables.",
        "Configure global settings.",
        "Configure host based authentication.",
    }
)

#: ``templates/postgres.sh.j2``, verbatim.
POSTGRES_SH_TEMPLATE = (
    "export PGDATA={{ postgresql_data_dir }}\n"
    "export PATH=$PATH:{{ postgresql_bin_path }}\n"
)

#: ``templates/pg_hba.conf.j2``, verbatim. The empty ``default('')`` fields leave
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
    '{{ client.auth_options|default("") }}\n'
    "{% endfor %}\n"
)

#: Ansible ``role_attr_flags`` tokens -> (option, value). ``BYPASSRLS`` is the
#: #31 parity flag; the six others are the #45 partial-reconcile flags. Only the
#: tokens an item lists reach :func:`~reemote.modules.postgresql.role` -- an
#: unlisted attribute is left alone on an existing role and falls back to its
#: ``CREATE ROLE`` default on an absent one, so no control-side fill is needed.
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

#: The ``postgresql_privs`` object types
#: :func:`~reemote.modules.postgresql.privilege` covers (#29). Anything else is
#: that closed issue's deliberate scope boundary.
PRIVS_TYPES = frozenset(
    {"table", "schema", "sequence", "function", "database", "tablespace", "default_privs"}
)

#: Ansible's ``postgresql_restarted_state`` -> ``service_handler`` action.
HANDLER_ACTIONS = {"restarted": "restarted", "reloaded": "reloaded", "started": "running"}


# --- variables ---------------------------------------------------------------


class PgConfigOption(BaseModel):
    """One ``postgresql_global_config_options`` item (a ``lineinfile`` edit).

    ``extra="allow"`` and no declared field beyond the model config: the
    ``option``/``value``/``state`` keys are validated by :func:`resolve_plan`
    (which needs the whole-inventory ``log_directory`` cross-check), so the model
    only has to carry the mapping through resolution frozen and hashable.
    """

    model_config = ConfigDict(frozen=True, extra="allow")


class PgHbaEntry(BaseModel):
    """One ``postgresql_hba_entries`` item, a ``pg_hba.conf`` row.

    The four keys the template reads unconditionally are required; the optional
    address/mask/options keys stay ``extra`` so that an absent one is *undefined*
    for the template's ``|default('')`` rather than rendering as ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str
    database: str
    user: str
    auth_method: str


class PgUser(BaseModel):
    """One ``postgresql_users`` item.

    Only ``name`` is required (the role's own gate); every other key
    (``state``, ``role_attr_flags``, ``login_db``/``db``, ``priv``,
    ``login_user``, ``login_unix_socket``, ``port``) stays ``extra`` so
    :func:`normalize_users` reads it with the Ansible ``.get(key, default)``
    semantics -- a declared default would suppress that.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        """An empty user name is a typo, not "no user"."""
        if not value:
            raise ValueError("a postgresql_users item needs a name")
        return value


class PgDatabase(BaseModel):
    """One ``postgresql_databases`` item; only ``name`` is required."""

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        """An empty database name is a typo, not "no database"."""
        if not value:
            raise ValueError("a postgresql_databases item needs a name")
        return value


class PgPriv(BaseModel):
    """One ``postgresql_privs`` item.

    No required field: the database is ``login_db`` *or* the role's ``db`` alias,
    and :func:`normalize_privs` reproduces ``community.postgresql.postgresql_privs``
    argument handling (including the #29 scope rejection) on the dumped mapping.
    """

    model_config = ConfigDict(frozen=True, extra="allow")


class PostgresqlVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names carry no ``postgresql_`` prefix -- the prefix passed to
    :func:`~reemote.rolevars.resolve_vars` binds ``postgresql_user`` in the
    inventory to :attr:`user`, so an inventory written for the role needs no
    change. The keys the RedHat vars file overrides (:attr:`version`,
    :attr:`packages`, :attr:`python_library`, :attr:`daemon`, :attr:`data_dir`,
    :attr:`bin_path`, :attr:`config_path`, :attr:`unix_socket_directories_mode`)
    are ``None`` by default and fall back to :data:`REDHAT_VARS` unless pinned.

    Sequences are ``tuple``, never ``list``: the model is frozen and a value that
    reaches :func:`~reemote.core.op` must hash (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enablerepo: str = ""
    restarted_state: Literal["restarted", "reloaded", "started"] = "restarted"
    user: str = "postgres"
    group: str = "postgres"
    #: ``None`` resolves to ``scram-sha-256`` under FIPS else ``md5`` (the role's
    #: ``ansible_facts.fips | ternary(...)`` default, #30).
    auth_method: Literal["md5", "scram-sha-256"] | None = None
    unix_socket_directories: tuple[str, ...] = ("/var/run/postgresql",)
    service_state: Literal["started", "stopped"] = "started"
    service_enabled: bool = True
    global_config_options: tuple[PgConfigOption, ...] | None = None
    hba_entries: tuple[PgHbaEntry, ...] | None = None
    databases: tuple[PgDatabase, ...] = ()
    users: tuple[PgUser, ...] = ()
    privs: tuple[PgPriv, ...] = ()
    #: The RedHat-overridable knobs; ``None`` falls back to :data:`REDHAT_VARS`.
    version: str | None = None
    packages: tuple[str, ...] | None = None
    python_library: str | None = None
    daemon: str | None = None
    data_dir: str | None = None
    bin_path: str | None = None
    config_path: str | None = None
    unix_socket_directories_mode: str | None = None

    @field_validator("unix_socket_directories", mode="before")
    @classmethod
    def _socket_dirs(cls, value: Any) -> Any:
        """Accept a bare string, and require absolute paths (the role's gate)."""
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("postgresql_unix_socket_directories must be a non-empty list")
        for socket_dir in value:
            if not isinstance(socket_dir, str) or not socket_dir.startswith("/"):
                raise ValueError(
                    f"unix socket directory must be an absolute path, got {socket_dir!r}"
                )
        return tuple(value)


@dataclass(frozen=True)
class PostgresqlData:
    """One host's resolved data plane, built by :func:`resolve_data`.

    Everything :func:`postgresql_data` (pass B) needs and nothing it does not:
    the normalized users/databases/privileges plus the peer-auth connection
    identity. It needs no fact, so pass B resolves it without gathering the
    distribution or FIPS state.
    """

    pg_user: str
    pg_group: str
    socket_dirs: tuple[str, ...]
    users: tuple[dict[str, Any], ...]
    databases: tuple[dict[str, Any], ...]
    privs: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PostgresqlPlan:
    """One host's resolved system plan, built by :func:`resolve_plan`.

    Every value the pass-A stages need is decided *before* the first request of
    their wave is built -- the RedHat vars table, the rendered profile and
    ``pg_hba.conf`` text, the ``postgresql.conf`` transforms, the ``initdb``
    command -- so each stage is a pure function of this one frozen object. It
    replaces the example's ``plans`` dict keyed by host name: under a per-host
    body, the plan is a local.
    """

    version: str
    major: int
    packages: tuple[str, ...]
    python_library: str
    daemon: str
    data_dir: str
    socket_mode: str
    socket_dirs: tuple[str, ...]
    pg_user: str
    pg_group: str
    enable_repos: tuple[str, ...] | None
    log_dir: str
    hba_content: str | None
    conf_path: str
    hba_path: str
    conf_transforms: tuple[Callable[[str | None], str], ...]
    pg_version_path: str
    initdb_command: str
    profile_content: str
    service_running: bool
    service_enabled: bool
    handler_action: str


# --- pure helpers ------------------------------------------------------------


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

    ``"Ansible managed"`` becomes ``"# Ansible managed"``; an empty line becomes a
    bare ``#``. Only the default style the role's template uses.
    """
    return "\n".join(("# " + line).rstrip() for line in str(text).splitlines())


def render_profile(data_dir: str, bin_path: str) -> str:
    """Pre-render ``templates/postgres.sh.j2`` control-side (recipe: Jinja2)."""
    from ._render import jinja2_environment

    return (
        jinja2_environment()
        .from_string(POSTGRES_SH_TEMPLATE)
        .render(postgresql_data_dir=data_dir, postgresql_bin_path=bin_path)
    )


def render_pg_hba(entries: list[dict[str, Any]]) -> str:
    """Pre-render ``templates/pg_hba.conf.j2`` control-side, byte-exactly.

    The ``comment`` filter is an Ansible extra, registered by hand on the shared
    environment (which otherwise carries Ansible's whitespace defaults and
    ``StrictUndefined``). The jinja2 import lives in :mod:`reemote.roles._render`
    so importing ``reemote.roles`` stays clean without ``reemote[templates]``.
    """
    from ._render import jinja2_environment

    env = jinja2_environment()
    env.filters["comment"] = _ansible_comment
    return env.from_string(PG_HBA_TEMPLATE).render(
        ansible_managed="Ansible managed", postgresql_hba_entries=entries
    )


def make_conf_transform(
    option: str, value: str, present: bool = True
) -> Callable[[str | None], str]:
    """One ``lineinfile`` task as a pure, idempotent transform.

    Ansible: ``regexp="^#?{{ option }}.+$"``, ``line="{{ option }} = '{{ value }}'"``.
    ``state: present`` replaces the LAST matching line (appending when none
    matches); ``state: absent`` removes ALL matching lines. The option is
    interpolated into the regex raw, exactly as the role does. Built once per
    host, in :func:`resolve_plan`, so the callable a request carries is stable for
    the whole run and the transform is a fixpoint.
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
    """Ansible ``role_attr_flags``: comma-separated ``[NO]FLAG`` tokens.

    Returns the listed ``(option, bool)`` pairs -- ONLY the listed ones, which is
    exactly what :func:`~reemote.modules.postgresql.role` reconciles now that its
    attribute options are tri-state (#45): an unlisted flag is left alone on an
    existing role. ``None`` (omitted) stays ``None``: presence-only, touch nothing.
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


def _psql_conn(item: dict[str, Any], v: PostgresqlVars) -> dict[str, Any]:
    """The psql connection kwargs every ``community.postgresql`` task shares.

    Deviation: ``login_host`` is ignored -- Ansible's ``login_unix_socket`` wins
    over ``login_host`` in psycopg2, so the effective connection is the unix
    socket (peer auth). ``login_password`` is dropped (credentials boundary); the
    run-as user comes from the inventory privilege view.
    """
    port = item.get("port")
    if port is not None:
        port = int(port)
    return {
        "psql_user": item.get("login_user") or v.user,
        "psql_host": item.get("login_unix_socket") or v.unix_socket_directories[0],
        "psql_port": port,
    }


def normalize_users(v: PostgresqlVars) -> tuple[dict[str, Any], ...]:
    """The role's ``postgresql_users`` items, validated and flattened.

    Deviation: a duplicate name fails fast (Ansible loops twice over the same
    list -- users.yml creates, users_props.yml reconciles -- so a duplicate
    silently applies last-item-wins; here it is a configuration error).
    """
    users: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in v.users:
        item = entry.model_dump()
        name = item["name"]
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
                **_psql_conn(item, v),
            }
        )
    return tuple(users)


def normalize_databases(v: PostgresqlVars) -> tuple[dict[str, Any], ...]:
    """The role's ``postgresql_databases`` items with the task defaults.

    Deviation: the ``UTF-8`` encoding default is normalized to the catalog
    spelling ``UTF8`` (PostgreSQL accepts both on ``CREATE DATABASE``; the
    ``PostgresqlDatabases`` fact reports ``UTF8``).
    """
    databases: list[dict[str, Any]] = []
    for entry in v.databases:
        item = entry.model_dump()
        name = item["name"]
        state = item.get("state", "present")
        if state not in ("present", "absent"):
            raise ValueError(
                f"database {name}: state must be present/absent, got {state!r}"
            )
        databases.append(
            {
                "name": name,
                "present": state == "present",
                "owner": item.get("owner") or v.user,
                "template": item.get("template") or "template0",
                "encoding": (item.get("encoding") or "UTF-8").replace("-", "").upper(),
                "lc_collate": item.get("lc_collate") or "en_US.UTF-8",
                "lc_ctype": item.get("lc_ctype") or "en_US.UTF-8",
                **_psql_conn(item, v),
            }
        )
    return tuple(databases)


def normalize_privs(v: PostgresqlVars) -> tuple[dict[str, Any], ...]:
    """The role's ``postgresql_privs`` items, mapped onto ``postgresql.privilege``.

    Reproduces the ``community.postgresql.postgresql_privs`` argument handling:
    ``login_db`` (or the role's ``db`` alias) is required, ``roles`` is
    comma-split, ``privs`` is comma-split into a tuple of names (a lone ``ALL``
    stays the bare string the operation expands), ``type`` defaults to ``table``,
    ``objs`` defaults to the login_db for databases, ``ALL_IN_SCHEMA`` becomes
    ``all_in_schema`` (the schema defaulting to public), ``ALL_DEFAULT`` becomes
    every default-privs class except schemas, function argument lists turn ``:``
    into ``,``, and table/sequence/function objects are schema-qualified.
    ``fail_on_role`` is absorbed (out of scope per #29); an exotic type is
    rejected citing #29.
    """
    privs: list[dict[str, Any]] = []
    for entry in v.privs:
        item = entry.model_dump()
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
        # reemote's postgresql.privilege takes a single privilege name, the string
        # "ALL" (expanded to the type's full set), or an iterable of names -- NOT
        # Ansible's comma-joined string. A lone ALL stays the bare string so the
        # operation expands it; anything else becomes a tuple of tokens.
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
                **_psql_conn(item, v),
            }
        )
    return tuple(privs)


def resolve_data(v: PostgresqlVars) -> PostgresqlData:
    """The data plane's static half: no fact, so pass B needs no gather.

    Normalizes the users/databases/privileges and freezes the peer-auth identity
    the data requests connect with.
    """
    return PostgresqlData(
        pg_user=v.user,
        pg_group=v.group,
        socket_dirs=v.unix_socket_directories,
        users=normalize_users(v),
        databases=normalize_databases(v),
        privs=normalize_privs(v),
    )


def resolve_plan(v: PostgresqlVars, major: int, fips: bool) -> PostgresqlPlan:
    """The system plan's static half: the frozen vars table plus resolved defaults.

    ``major`` is the gathered ``LinuxDistribution`` major version (the
    ``include_vars`` dispatch key); ``fips`` is the gathered ``FipsEnabled`` (the
    ``ansible_facts.fips | ternary(...)`` auth-method default, #30). Everything
    here is decided control-side before the deploys run.

    Raises:
        ValueError: No RedHat vars are pinned for ``major``, or a
            ``postgresql_global_config_options`` item is malformed or omits
            ``log_directory`` (the role derives the effective log directory from
            it). Raised before the first request of the wave is built.
    """
    if major not in REDHAT_VARS:
        raise ValueError(
            f"no RedHat vars pinned for major version {major}; "
            f"supported: {tuple(sorted(REDHAT_VARS))}"
        )
    rv = REDHAT_VARS[major]
    data_dir = v.data_dir or rv["data_dir"]
    bin_path = v.bin_path or rv["bin_path"]
    config_path = v.config_path or rv["config_path"]
    socket_dirs = v.unix_socket_directories
    auth_method = v.auth_method or ("scram-sha-256" if fips else "md5")

    raw_options: tuple[dict[str, Any], ...]
    if v.global_config_options is None:
        raw_options = (
            {"option": "unix_socket_directories", "value": ",".join(socket_dirs)},
            {"option": "log_directory", "value": "log"},
        )
    else:
        raw_options = tuple(option.model_dump() for option in v.global_config_options)
    options: list[tuple[str, str, bool]] = []
    for item in raw_options:
        if "option" not in item or "value" not in item:
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

    entries: list[dict[str, Any]]
    if v.hba_entries is None:
        entries = [
            {"type": "local", "database": "all", "user": "postgres", "auth_method": "peer"},
            {"type": "local", "database": "all", "user": "all", "auth_method": "peer"},
            {
                "type": "host",
                "database": "all",
                "user": "all",
                "address": "127.0.0.1/32",
                "auth_method": auth_method,
            },
            {
                "type": "host",
                "database": "all",
                "user": "all",
                "address": "::1/128",
                "auth_method": auth_method,
            },
        ]
    else:
        entries = [entry.model_dump(exclude_none=True) for entry in v.hba_entries]

    enablerepo = v.enablerepo
    enable_repos = tuple(r.strip() for r in enablerepo.split(",") if r.strip()) or None
    return PostgresqlPlan(
        version=v.version or rv["version"],
        major=major,
        packages=tuple(v.packages or rv["packages"]),
        python_library=v.python_library or rv["python_library"],
        daemon=v.daemon or rv["daemon"],
        data_dir=data_dir,
        socket_mode=v.unix_socket_directories_mode or rv["socket_mode"],
        socket_dirs=socket_dirs,
        pg_user=v.user,
        pg_group=v.group,
        enable_repos=enable_repos,
        log_dir=effective_log_dir,
        hba_content=render_pg_hba(entries) if entries else None,
        conf_path=posixpath.join(config_path, "postgresql.conf"),
        hba_path=posixpath.join(config_path, "pg_hba.conf"),
        conf_transforms=tuple(
            make_conf_transform(option, value, present)
            for option, value, present in options
        ),
        pg_version_path=posixpath.join(data_dir, "PG_VERSION"),
        initdb_command=f"{bin_path}/initdb -D {data_dir}",
        profile_content=render_profile(data_dir, bin_path),
        service_running=v.service_state == "started",
        service_enabled=v.service_enabled,
        handler_action=HANDLER_ACTIONS[v.restarted_state],
    )


# --- helper deploys ----------------------------------------------------------


def setup(plan: PostgresqlPlan) -> Deploy:
    """``tasks/setup-RedHat.yml``: the two ``yum`` tasks.

    ``exclude`` (#27) rides the package install only -- the Python library task
    has no exclude, exactly like the role. ``enable_repos`` (#32) is the additive
    ``enablerepo`` parity kwarg; the role's ``default(omit, true)`` on the empty
    string becomes ``None``.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            dnf.packages,
            name="Ensure PostgreSQL packages are installed.",
            packages=plan.packages,
            exclude=("python-unversioned-command",),
            enable_repos=plan.enable_repos,
            sudo=True,
        )
        yield op(
            dnf.packages,
            name="Ensure PostgreSQL Python libraries are installed.",
            packages=(plan.python_library,),
            enable_repos=plan.enable_repos,
            sudo=True,
        )

    return deploy


def initialize(plan: PostgresqlPlan) -> Deploy:
    """``tasks/initialize.yml``: the profile template and the data directory.

    The profile write notifies the restart handler (its name is in
    :data:`NOTIFY_RESTART`). The ``stat``/``initdb``/log-dir tasks are NOT here:
    the stat gate and initdb are ``act`` barriers between stages (mutable state /
    run-as postgres), and the log directory leads :func:`configure` so it is
    created after initdb, exactly where the role creates it.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.install,
            name="Set PostgreSQL environment variables.",
            content=plan.profile_content,
            dest="/etc/profile.d/postgres.sh",
            mode="0644",
            sudo=True,
        )
        yield op(
            files.directory,
            name="Ensure PostgreSQL data directory exists.",
            path=plan.data_dir,
            user=plan.pg_user,
            group=plan.pg_group,
            mode="0700",
            sudo=True,
        )

    return deploy


def configure(plan: PostgresqlPlan) -> Deploy:
    """``tasks/configure.yml``: the log directory, the lineinfile loop, the
    ``pg_hba.conf`` template and the unix socket directories.

    Each :func:`~reemote.modules.files.transform` is one lineinfile item; the
    transform objects were built once in :func:`resolve_plan` (stable across the
    run). The conf file exists by now -- initdb created it (or the cluster was
    already initialized). ``files.install``'s ``cp`` preserves the existing owner,
    so the postgres-owned ``postgresql.conf`` stays postgres-owned like lineinfile
    leaves it; ``pg_hba.conf`` is explicitly ``postgres:postgres 0600``.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.directory,
            name="Ensure PostgreSQL log directory exists.",
            path=plan.log_dir,
            user=plan.pg_user,
            group=plan.pg_group,
            mode="0700",
            sudo=True,
        )
        for transform in plan.conf_transforms:
            yield op(
                files.transform,
                name="Configure global settings.",
                path=plan.conf_path,
                transform=transform,
                mode="0644",
                sudo=True,
            )
        if plan.hba_content is not None:
            yield op(
                files.install,
                name="Configure host based authentication.",
                content=plan.hba_content,
                dest=plan.hba_path,
                mode="0600",
                user=plan.pg_user,
                group=plan.pg_group,
                sudo=True,
            )
        for socket_dir in plan.socket_dirs:
            yield op(
                files.directory,
                name="Ensure PostgreSQL unix socket dirs exist.",
                path=socket_dir,
                user=plan.pg_user,
                group=plan.pg_group,
                mode=plan.socket_mode,
                sudo=True,
            )

    return deploy


def service(plan: PostgresqlPlan) -> Deploy:
    """``tasks/main.yml``: the started-and-enabled service task."""

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            systemd.service,
            name="Ensure PostgreSQL is started and enabled on boot.",
            service=plan.daemon,
            running=plan.service_running,
            enabled=plan.service_enabled,
            sudo=True,
        )

    return deploy


def users(data: PostgresqlData) -> Deploy:
    """``tasks/users.yml``: ensure the users exist, touch nothing else.

    Ansible's ``postgresql_user`` WITHOUT ``role_attr_flags``/``password`` is a
    presence-only no-op on an existing role and ``CREATE USER`` (server defaults)
    on an absent one. :func:`~reemote.modules.postgresql.role` matches that
    natively now that its attribute options are tri-state (#45): a request listing
    no attribute is a no-op on an existing role and creates an absent one with the
    ``CREATE ROLE`` defaults. So this stage yields one presence-only request per
    present user and lets the operation's own ``PostgresqlRoles`` read decide
    CREATE vs no-op. The reconcile stage (:func:`users_props`) applies the listed
    flags.

    Deviation: a ``state: absent`` item is not created first (the role's users.yml
    creates every user before users_props.yml applies state).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for user in data.users:
            if not user["present"]:
                continue
            yield op(
                pg.role,
                name="Ensure PostgreSQL users are present.",
                role=user["name"],
                psql_user=user["psql_user"],
                psql_host=user["psql_host"],
                psql_port=user["psql_port"],
                sudo=True,
            )

    return deploy


def databases(data: PostgresqlData) -> Deploy:
    """``tasks/databases.yml``: the ``postgresql_db`` loop.

    The creation-only parameters (template/encoding/locales) are reported skipped
    on an existing database, mirroring ``postgresql_db``'s immutability; the owner
    is reconciled. Runs after :func:`users` so an owning role exists.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for database in data.databases:
            yield op(
                pg.database,
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
            )

    return deploy


def users_props(data: PostgresqlData) -> Deploy:
    """``tasks/users_props.yml``: attribute reconciliation and privileges.

    Ansible's ``role_attr_flags`` adjusts ONLY the listed flags, and
    :func:`~reemote.modules.postgresql.role`'s tri-state options now do exactly
    that (#45): this stage forwards the item's listed flags verbatim, so an
    unlisted attribute is left alone on an existing role, and an absent role
    (:func:`users` creates it with the ``CREATE ROLE`` defaults) reconciles only
    the listed flags. ``bypassrls`` rides the same listed-flag path (#31);
    ``connection_limit`` is not a ``role_attr_flags`` token. An item without flags
    is presence-only: :func:`users` already ensured it.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for user in data.users:
            conn = {
                "psql_user": user["psql_user"],
                "psql_host": user["psql_host"],
                "psql_port": user["psql_port"],
                "psql_database": user["psql_database"],
            }
            if not user["present"]:
                yield op(
                    pg.role,
                    name="Ensure PostgreSQL users are configured correctly.",
                    role=user["name"],
                    present=False,
                    **conn,
                    sudo=True,
                )
                continue
            if not user["flags"]:
                continue
            yield op(
                pg.role,
                name="Ensure PostgreSQL users are configured correctly.",
                role=user["name"],
                **dict(user["flags"]),
                **conn,
                sudo=True,
            )
        for priv in data.privs:
            yield op(
                pg.privilege,
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
            )

    return deploy


def handler(plan: PostgresqlPlan, notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart postgresql``.

    Issue #44 closed the sleep gap: ``sleep=5`` is the role's own value, so
    :func:`~reemote.modules.systemd.service` renders stop / ``sleep 5`` / start
    instead of one ``systemctl restart``. The pause is inert when the state is
    overridden to something other than ``restarted`` -- exactly as in Ansible,
    where ``sleep:`` only applies to a restart. A per-host body needs none of the
    example's ``by_action`` partition: this host has exactly one
    ``restarted_state``. An empty ``notified`` set yields no request, the
    converged no-op Ansible's handler gives for free.
    """
    return service_handler(
        notified, (plan.daemon,), action=plan.handler_action, sleep=5
    )


# --- the two roles -----------------------------------------------------------


def postgresql(v: VarsSet[PostgresqlVars]) -> Deploy:
    """Install, initialize, configure and start PostgreSQL (pass A, normal view).

    Args:
        v: The role's resolved variables, one :class:`PostgresqlVars` per host
            (``resolve_vars(PostgresqlVars, "postgresql_", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that publishes no outputs -- a
        composing role wants its *effect*, not a value (design doc section 10.5).

    Raises:
        ValueError: The declared RedHat family does not match the machine (or is
            Fedora/Amazon, which the role's vars dispatch excludes), no RedHat
            vars are pinned for the major version, a config option is malformed,
            or ``initdb`` exited non-zero. Each is raised inside the body before
            the affected wave is built, so nothing is half-applied.
        TypeError: The distribution major version cannot be read from the
            ``LinuxDistribution`` fact.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "RedHat":
            return

        # Waves 0-1: the include_vars dispatch key and the FIPS auth ternary.
        distro = yield fact(LinuxDistribution)
        name = str(distro.get("name") or "")
        if distro.get("family") != "RedHat" or name.startswith(("Fedora", "Amazon")):
            raise ValueError(
                f"{host.name}: declared RedHat family but the distribution is "
                f"{name!r} (family {distro.get('family')!r}); the role's RedHat "
                "vars dispatch excludes Fedora and Amazon"
            )
        major = distro.get("major")
        if not isinstance(major, int):
            raise TypeError(
                f"{host.name}: cannot read the distribution major version "
                f"from {distro!r}"
            )
        fips = yield fact(FipsEnabled)
        plan = resolve_plan(hv, major, bool(fips))

        # One wave: the packages, the profile and the data directory, closed by a
        # checkpoint. The report is the setup+initialize wave; only the profile
        # write's name is in NOTIFY_RESTART, so the union below is honest.
        yield from setup(plan)(host)
        yield from initialize(plan)(host)
        init_report = yield checkpoint("initialize done")

        # The stat gate: an elevated Shell action, because the 0700 postgres data
        # directory cannot be traversed by the unprivileged FileExists fact. A
        # barrier never raises on a non-zero exit -- the Result is data -- so the
        # exit status is the "already initialized" answer.
        stat = yield act(
            Shell,
            command=f"test -e {shlex.quote(plan.pg_version_path)}",
            name="stat PG_VERSION",
            sudo=True,
        )
        if stat["exit_status"] != 0:
            # initdb stays in pass A: the elevation to root comes from the
            # inventory, the drop to postgres is inside the command. It has to sit
            # between initialize and configure, so pass B would mean a third pass.
            result = yield act(
                Shell,
                command=f"sudo -u {plan.pg_user} {plan.initdb_command}",
                name="initdb",
                sudo=True,
            )
            if result["exit_status"] != 0:
                raise ValueError(
                    f"{host.name}: initdb failed (exit {result['exit_status']}): "
                    f"{result['stderr'].strip()}"
                )

        # One wave: postgresql.conf, pg_hba.conf and the socket directories,
        # closed by a checkpoint that reads exactly this wave.
        yield from configure(plan)(host)
        conf_report = yield checkpoint("configure done")

        # The service, then the handler. The notification union is initialize |
        # configure, both already applied, so the handler runs here -- before the
        # data plane -- instead of after it as the two-pass example did.
        yield from service(plan)(host)
        notified = changed_hosts(init_report, NOTIFY_RESTART) | changed_hosts(
            conf_report, NOTIFY_RESTART
        )
        yield from handler(plan, notified)(host)

    return deploy


def postgresql_data(v: VarsSet[PostgresqlVars]) -> Deploy:
    """Manage users, databases and privileges (pass B, ``postgres`` view).

    Args:
        v: The role's resolved variables, one :class:`PostgresqlVars` per host
            (``resolve_vars(PostgresqlVars, "postgresql_", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that publishes no outputs.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "RedHat":
            return

        data = resolve_data(hv)

        # Wave 0: the catalog read is a reachability gate, not a value the body
        # consumes -- peer auth needs a listening socket, so this fact fails fast
        # (HostConnectionError / CommandNotFoundError) if pass A left the service
        # down. It is pass B's first barrier, never pass A's last, because pass A
        # runs on the root view.
        yield fact(
            PostgresqlRoles,
            psql_user=data.pg_user,
            psql_host=data.socket_dirs[0],
            name="read pg_roles catalog",
        )

        # One wave: users, then databases (so an owning role exists), then the
        # attribute/privilege reconciliation. No checkpoint -- nothing reads an
        # outcome, and the data plane never notifies the handler.
        yield from users(data)(host)
        yield from databases(data)(host)
        yield from users_props(data)(host)

    return deploy


__all__ = [
    "PostgresqlVars",
    "postgresql",
    "postgresql_data",
]
