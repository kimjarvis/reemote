# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The systemd role (migrated from ``0x0I/systemd``).
#
# Manages systemd units on an Ubuntu host as the role's task list does: the
# preflight passwd and path checks, the unit load-path directory, the rendered
# ``.service``/``.socket``/``.mount``/``.target`` unit file, the rendered drop-in
# ``.conf`` override, the two reload handlers, each unit's running/enabled state,
# and the opt-in uninstall. :func:`systemd` is the whole task list and
# :class:`SystemdVars` is ``defaults/main.yml`` together with ``vars/main.yml``,
# bound from the inventory entry's ``systemd`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role carries no per-OS
# dispatch -- systemd is systemd everywhere -- so its ``vars/main.yml`` freezes
# to the constants below and an off-target host returns before its first
# ``yield``, emitting no requests, no barriers and no log lines at all.
#
# The stream is **two waves**, one more when a unit is per-user:
#
# 0. ``fact(Users)`` -- only when some unit names a non-root ``user`` or a
#    ``scope: user`` manager. That one read is what the role's ``getent passwd``
#    provided: the owner-existence assert, the owner's home directory behind
#    ``_own_as_user``, and the uid behind ``XDG_RUNTIME_DIR``. A host whose units
#    are all root-owned system units parks on nothing here, so the wave count is
#    host-dependent (see D2).
# 1 :func:`configure` -- the load-path directory of every unit that names a
#    ``path``, then the rendered unit or drop-in file. Closed by
#    ``checkpoint("configure done")``, which is where the role's two ``notify:``
#    lines and the ``meta: flush_handlers`` of its launch stage land.
# 2 the handlers, then the activations, in the play's own order: one
#    ``Reload systemd unit`` per unit whose *own* file changed (``Reload systemd
#    units``), one ``Reload systemd daemon`` per changed drop-in (``Reload
#    systemd daemon``), then one ``Activate systemd unit`` per non-drop-in,
#    non-template-instance unit. The driver applies a wave in order, so every
#    reload lands before the activation that needs the reloaded manager -- the
#    guarantee ``flush_handlers`` bought, without leaving the stream.
#
# ``perform_uninstall`` replaces waves 1 and 2 with the uninstall handler -- stop
# and disable every managed unit, then remove every managed file -- because the
# role gates its whole config/launch block on ``when: not perform_uninstall``.
# Its ``command: /bin/true`` + ``changed_when: true`` signal task needs no
# counterpart: nothing here has to be *told* to uninstall, the flag is a
# variable the body reads.
#
# :func:`systemd` returns ``None`` on every path, including the family guard's:
# no composing role consumes outputs from this one.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: there is no ``become_user`` in reemote -- privilege comes from the
#   inventory -- so the two places the role became the unit's user are mapped
#   differently. A per-user *file* is written by the root-owned spool-and-install
#   and then ``chown``ed to that user by the same request
#   (:func:`~reemote.modules.files.install` takes ``user``/``group``), which is
#   what the ``become_user`` + ``owner`` pair achieved. A per-user *manager* is
#   addressed by name -- :func:`~reemote.modules.systemd.service` with
#   ``user_name=<user>`` renders ``systemctl --machine=<user>@.host`` -- run
#   while escalated to root, and the role's ``environment: XDG_RUNTIME_DIR``
#   rides :func:`~reemote.core.op`'s own ``env`` meta-parameter. The command
#   differs from Ansible's ``sudo -u <user> systemctl --user``; the manager it
#   talks to does not.
# * D2: the role's ``getent passwd`` runs whenever any unit is configured; here
#   the ``fact(Users)`` barrier is conditional (wave 0 above). Every unit
#   root-owned and system-scoped -- the common case -- costs no wave, and the
#   passwd-existence assert the read feeds is one root always passes.
# * D3: both handlers loop over a *registered* result list and gate on each item's
#   own ``changed`` flag, so the notification here is read per host and per
#   request name out of the checkpoint's log (:func:`changed_names`) rather than
#   by the host-level :func:`~reemote.handlers.changed_hosts`. A host with three
#   units and one changed file gets one reload, not three-and-all.
# * D4: reemote's install-family idempotence is *content*-only, so a unit file
#   whose content matches but whose owner or mode drifted reports no change --
#   and therefore queues no reload, where Ansible's ``template`` would report
#   both. No paired ``files.file`` metadata request is issued either, matching
#   the shipped ``nginx`` role's handling of the same trade-off.
# * D5: ``backup: {{ systemd_backup_files }}`` has no analogue: the install
#   family keeps no timestamped copy of the file it replaces.
#   :attr:`SystemdVars.backup_files` is carried so an existing inventory still
#   binds, and does nothing.
# * D6: ``follow: false`` on the directory task has no analogue either:
#   :func:`~reemote.modules.files.directory` manages the directory the path
#   resolves to, so a symlink at a load path is followed rather than managed as
#   a link.
# * D7: ``scope: global`` is not supported. reemote's systemd descriptors reach
#   the system manager, a named machine, or one named user's manager -- never
#   ``systemctl --global`` -- so :attr:`UnitSpec.scope` is a two-value
#   :data:`~typing.Literal` and such an entry is refused by ``ValidationError``
#   (a ``ValueError``) while the inventory is resolved, before any host is
#   contacted.
# * D8: the role's two ``ansible.builtin.assert`` tasks become :class:`ValueError`
#   raised in the body, the corpus idiom for a config the deploy refuses to
#   apply. The load-path assert is pure and so runs *before* the passwd read,
#   which means a configuration violating both reports the path problem first.
# * the family guard is the body's opening ``if``, and an empty ``unit_config``
#   is an immediate return: every preflight task is gated ``when: unit_config |
#   length > 0``, and the handlers would loop over nothing, so the role is a
#   no-op for such a host -- as it is here, with no log lines to prove it.
# """

import re
from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import files
from ..modules import systemd as sd
from ..modules.server import Users
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from ..drivers import ApplyReport

# --- role constants (vars/main.yml + defaults/main.yml, Ubuntu path only) -----

#: ``_default_unit_path``: where a unit that names no ``path`` is written.
DEFAULT_UNIT_PATH = "/etc/systemd/system"

#: ``_default_unit_type``: the unit type of an entry that names none, and the
#: section the unit template picks when it derives ``config.type | title``.
DEFAULT_UNIT_TYPE = "service"

#: ``_default_unit_state``: what the launch task asks for when an entry names no
#: ``state`` (the *handler* omits the state instead -- see :func:`reload_units`).
DEFAULT_UNIT_STATE = "stopped"

#: ``_default_unit_user``, and therefore ``_default_unit_group``, which the role
#: derives from the user when an entry names no group.
DEFAULT_UNIT_USER = "root"

#: ``_default_unit_scope`` as the role spells it: ``user`` when the unit's user
#: is not root, ``system`` otherwise.
DEFAULT_UNIT_SCOPE = "system"

#: ``_pid1_unit_path_prefixes``: the system load paths a ``scope: user`` unit
#: must not be written to, because PID 1 -- not the user's manager -- reads them.
PID1_UNIT_PATH_PREFIXES = (
    "/etc/systemd/system",
    "/run/systemd/system",
    "/usr/lib/systemd/system",
    "/usr/local/lib/systemd/system",
    "/lib/systemd/system",
)

#: ``mode: "0755"`` on the unit configuration path, ``mode: "0644"`` on every
#: rendered unit and drop-in file.
DIR_MODE = "0755"
UNIT_MODE = "0644"

#: ``{{ ansible_managed }}``, which Ansible fills from its global
#: ``ansible_managed`` template. reemote has no such variable, so the default
#: string is frozen here and rendered through the ``comment`` filter below.
ANSIBLE_MANAGED = "Ansible managed"

#: ``templates/systemd.unit.j2``, verbatim. ``config`` is the whole
#: ``unit_config`` entry (its INI sections included), so the role's
#: ``{% if "Unit" in config %}``, ``config.type|default(...)|title`` and
#: repeated-key list handling all read exactly what Ansible handed the template.
#: The ``comment`` filter is registered by :func:`_environment`; ``title``,
#: ``default`` and the ``string``/``iterable`` tests are Jinja2's own.
UNIT_TEMPLATE = """{{ ansible_managed | comment }}
# see: Systemd Unit configuration logic -- http://man7.org/linux/man-pages/man5/systemd.unit.5.html for more details

{% if "Unit" in config %}
[Unit]
{% for key, value in config["Unit"].items() %}
{{ key }}={{ value }}
{% endfor %}
{% endif %}

{% if config.type|default(_default_unit_type)|title in config %}
[{{ config.type|default(_default_unit_type)|title }}]
{% for key, value in config[config.type|default(_default_unit_type)|title].items() %}
{% if value is not string and value is iterable %}
{% for value_item in value %}
{{ key }}={{ value_item }}
{% endfor %}
{% else %}
{{ key }}={{ value }}
{% endif %}
{% endfor %}
{% endif %}

{% if "Install" in config %}
[Install]
{% for key, value in config["Install"].items() %}
{{ key }}={{ value }}
{% endfor %}
{% endif %}
"""

#: ``templates/override.conf.j2``, verbatim: every title-cased key of the entry
#: becomes a section of the drop-in, which is how a ``.d`` override inherits the
#: section layout of the unit it extends.
DROPIN_TEMPLATE = """{{ ansible_managed | comment }}
# see: Systemd Unit configuration logic -- http://man7.org/linux/man-pages/man5/systemd.unit.5.html for more details

{% for section in config.keys() if section == section|title  %}
[{{ section }}]
{% for key, value in config[section].items() %}
{% if (value is not mapping) and (value is iterable) and (value is not string) %}
{% for item in value %}
{{ key }}={{ item }}
{% endfor %}
{% else %}
{{ key }}={{ value }}
{% endif %}
{% endfor %}
{% endfor %}
"""

#: The role's ``unit_item.name is not search("^[^@]+@$")`` gate: the name of a
#: template *unit* (``getty@``) rather than of an instance. Such a unit is
#: rendered but never reloaded or activated on its own.
INSTANCE_PATTERN = re.compile(r"^[^@]+@$")


# --- variables ---------------------------------------------------------------


class UnitSpec(BaseModel):
    """One ``unit_config`` entry: a unit (or drop-in) plus its INI sections.

    ``extra="allow"`` is the point of the model. The role reads the entry's
    sections -- ``Unit``, ``Service``, ``Socket``, ``Mount``, ``Install``, and any
    other unit-type section -- straight off the dict, and every key and value
    Systemd itself supports has to survive to the template. The declared fields
    are only the keys the *tasks* read, and they are what the template's
    ``config`` still carries alongside the sections.

    The optional keys keep the Jinja ``| default(...)`` distinction the role was
    written with: ``None`` means "the entry omitted it", and :func:`resolve_units`
    applies the fallback. Frozen, so the containing :class:`SystemdVars` stays
    hashable.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    #: ``unit_item.name``: the unit's name without its type suffix (the role's
    #: required key; a template instance ends in ``@``).
    name: str
    #: ``unit_item.type``: the unit type -- systemd's own set of eleven, plus the
    #: role's special ``conf``, which renders a drop-in override instead of a unit
    #: file. Anything else is refused before a host is contacted.
    type: Literal[
        "service", "socket", "target", "device", "mount", "automount",
        "timer", "swap", "path", "slice", "scope", "conf",
    ] | None = None
    #: ``unit_item.path``: the load directory to write into. Its absence is what
    #: skips the role's "Create unit configuration path" task (D6).
    path: str | None = None
    #: ``unit_item.user``: the file's owner, and the user whose manager a
    #: per-user unit is reloaded and activated through (D1). Not
    #: ``Service.User``, which is an ordinary section key.
    user: str | None = None
    #: ``unit_item.group``: the file's group; ``None`` takes :attr:`user`, as
    #: ``group|default(_unit_user)`` did.
    group: str | None = None
    #: ``unit_item.scope``: which manager to talk to. ``global`` is refused (D7);
    #: ``None`` takes ``user`` for a non-root :attr:`user` and ``system``
    #: otherwise.
    scope: Literal["system", "user"] | None = None
    #: ``unit_item.state``: the launch task's state. ``None`` still means
    #: ``stopped`` at launch time, but *omitted* in the reload handler -- the
    #: handler must not start or stop a unit the entry never named a state for.
    state: Literal["started", "stopped"] | None = None
    #: ``unit_item.enabled``: the boot setting the launch task asserts.
    enabled: bool = False
    #: ``unit_item.manage_state``: ``false`` leaves the running state alone
    #: entirely (Ansible's ``state: omit``) while still setting :attr:`enabled`.
    manage_state: bool = True
    #: ``unit_item.reload_on_change``: ``false`` keeps a changed unit file from
    #: queueing the reload handler.
    reload_on_change: bool = True

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty unit name is a typo, not an anonymous unit."""
        if not value:
            raise ValueError("a unit_config entry needs a name")
        return value


class SystemdVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``systemd`` role mapping, so ``unit_config`` there sets
    :attr:`unit_config`. Sequences are ``tuple``, never ``list``: the model is
    frozen and hashable, and a value that reaches :func:`~reemote.core.op` must
    hash (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``unit_config``: the units and drop-ins this host manages.
    unit_config: tuple[UnitSpec, ...] = ()
    #: ``perform_uninstall``: stop, disable and remove :attr:`unit_config`
    #: instead of configuring it.
    perform_uninstall: bool = False
    #: ``systemd_backup_files``: reemote's install family keeps no timestamped
    #: copy of the file it replaces, so the flag is carried and inert (D5).
    backup_files: bool = True


# --- the per-host plan -------------------------------------------------------


@dataclass(frozen=True)
class UnitPlan:
    """One ``unit_config`` entry, resolved the way the role's ``vars:`` blocks do.

    Everything the tasks derive from the entry alone -- the type and filename,
    the destination, the manager scope, the rendered content -- is decided once,
    purely, in :func:`resolve_units`, before a host is contacted. The two values
    that need the passwd read are derived afterwards, from the barrier's result:
    the file's owner pair by :func:`unit_owner`, the manager arguments by
    :func:`manager_args`. It replaces nothing in the example (the role had no
    example to replace): under a per-host body, the plan is a local.
    """

    #: ``unit_item.name``.
    name: str
    #: ``type | default(_default_unit_type)``.
    unit_type: str
    #: ``{{ name }}.{{ type }}`` -- the unit the handler and launch tasks name.
    filename: str
    #: ``path | default(_default_unit_path)``: the load directory.
    path: str
    #: Whether the entry named a ``path`` at all: the role's directory task is
    #: gated ``when: unit_item.path is defined``, not on the resolved path.
    path_defined: bool
    #: ``{{ path }}/{{ filename }}`` -- where the rendered file lands.
    dest: str
    #: The stable ``name=`` of the request that writes :attr:`dest`, and so the
    #: channel :func:`changed_names` reads the handler notification from.
    render_name: str
    #: ``type == 'conf'``: a drop-in override rather than a unit file.
    is_conf: bool
    #: ``name is search('^[^@]+@$')``: a template unit, never activated alone.
    is_instance: bool
    #: ``scope | default('user' if user != 'root' else 'system')``.
    scope: str
    #: ``user | default('root')``.
    user: str
    #: ``group | default(user)``.
    group: str
    #: ``unit_item.state``, with the omit-vs-default distinction intact.
    state: str | None
    #: ``manage_state | default(true) | bool``.
    manage_state: bool
    #: ``enabled | default('no')``.
    enabled: bool
    #: ``reload_on_change is not defined or reload_on_change``.
    reload_on_change: bool
    #: The rendered INI text, produced control-side by one of the two templates.
    content: str


# --- pure helpers ------------------------------------------------------------


def _under(path: str, prefix: str) -> bool:
    """``path is match('^' ~ prefix ~ '(/|$)')`` -- *path* is at or under *prefix*.

    The exact-match arm matters: ``/etc/systemd/systemd`` is not under
    ``/etc/systemd/system``, and a prefix-only ``startswith`` would say it was.
    """
    return path == prefix or path.startswith(f"{prefix}/")


def _environment() -> Any:
    """Ansible's whitespace settings, plus the ``comment`` filter.

    :func:`~reemote.roles._render.jinja2_environment` returns a plain
    environment (``trim_blocks=True``, ``lstrip_blocks=False``,
    ``keep_trailing_newline=True``, ``StrictUndefined``) rather than a
    configured one, so a role can register the filters its templates ask for.
    ``comment`` is Ansible's own, not Jinja2's, and the role's headers use it with
    no arguments -- the ``'# '`` prefix and one line at a time.
    """
    from ._render import jinja2_environment

    env = jinja2_environment()
    env.filters["comment"] = comment_filter
    return env


def comment_filter(text: Any, prefix: str = "# ") -> str:
    """Ansible's ``comment`` filter: every line of *text*, prefixed.

    ``ansible_managed`` reaches it as a string, which is all the role's two
    templates ask for. Empty input renders the empty string, as the ``splitlines``
    loop naturally does.
    """
    return "\n".join(f"{prefix}{line}" for line in str(text).splitlines())


def render_unit_file(config: dict[str, Any]) -> str:
    """``templates/systemd.unit.j2``, pre-rendered control-side.

    ``config`` is the entry dumped with ``exclude_none=True``, which is what keeps
    the template's ``config.type|default(_default_unit_type)`` working: a key the
    entry left out is *undefined* for the template rather than ``None``, exactly
    as it was in Ansible. The jinja2 import lives inside :func:`_environment`, so
    importing ``reemote.roles`` stays clean without the ``reemote[templates]``
    extra (§17.4).
    """
    return _environment().from_string(UNIT_TEMPLATE).render(
        config=config,
        ansible_managed=ANSIBLE_MANAGED,
        _default_unit_type=DEFAULT_UNIT_TYPE,
    )


def render_dropin_file(config: dict[str, Any]) -> str:
    """``templates/override.conf.j2``, pre-rendered control-side.

    Same context and same ``exclude_none=True`` reasoning; the drop-in template
    reads no defaults, only the title-cased sections of the entry.
    """
    return _environment().from_string(DROPIN_TEMPLATE).render(
        config=config,
        ansible_managed=ANSIBLE_MANAGED,
    )


def resolve_units(v: SystemdVars) -> tuple[UnitPlan, ...]:
    """``tasks/common/config.yml``'s ``vars:`` blocks, applied to every entry.

    Pure, and it runs before the first barrier: the derived user, group, scope,
    filename and destination come from the entry alone, and so does the rendered
    content. Two asserts guard the configuration --
    :func:`assert_not_pid1_user_scope` here (pure), the passwd-existence one in
    the body once the read exists (D8).

    Raises:
        ValueError: A section of an entry cannot be rendered (a template error on
            malformed input), raised while the plan is built rather than halfway
            through a wave.
    """
    plans: list[UnitPlan] = []
    for spec in v.unit_config:
        unit_type = spec.type or DEFAULT_UNIT_TYPE
        is_conf = unit_type == "conf"
        path = spec.path or DEFAULT_UNIT_PATH
        filename = f"{spec.name}.{unit_type}"
        dest = f"{path}/{filename}"
        user = spec.user or DEFAULT_UNIT_USER
        scope = spec.scope or ("user" if user != DEFAULT_UNIT_USER else DEFAULT_UNIT_SCOPE)
        config = spec.model_dump(exclude_none=True)
        plans.append(
            UnitPlan(
                name=spec.name,
                unit_type=unit_type,
                filename=filename,
                path=path,
                path_defined=spec.path is not None,
                dest=dest,
                render_name=(
                    f"Render systemd drop-in file {dest}"
                    if is_conf
                    else f"Render systemd unit file {dest}"
                ),
                is_conf=is_conf,
                is_instance=bool(INSTANCE_PATTERN.search(spec.name)),
                scope=scope,
                user=user,
                group=spec.group or user,
                state=spec.state,
                manage_state=spec.manage_state,
                enabled=spec.enabled,
                reload_on_change=spec.reload_on_change,
                content=(
                    render_dropin_file(config) if is_conf else render_unit_file(config)
                ),
            )
        )
    return tuple(plans)


def needs_passwd_read(plans: tuple[UnitPlan, ...]) -> bool:
    """Whether any unit needs the ``fact(Users)`` barrier (D2).

    A unit needs the read when it names a non-root user -- the passwd-existence
    assert covers every entry, and a non-root owner needs a home to be recognised
    -- or asks for a ``user`` scope manager, whose home, uid and address all come
    from the same read. A configuration of root-owned system units needs none.
    """
    return any(plan.user != DEFAULT_UNIT_USER or plan.scope == "user" for plan in plans)


def assert_not_pid1_user_scope(plans: tuple[UnitPlan, ...]) -> None:
    """``Reject scope=user on PID 1 unit load paths``: the role's third preflight.

    A user manager never reads the system load paths, so a per-user unit written
    there would be invisible to the manager the role then tries to reload. The
    message is the role's own ``fail_msg``.

    Raises:
        ValueError: A ``scope: user`` unit names a PID 1 load path.
    """
    for plan in plans:
        if plan.scope == "user" and any(
            _under(plan.path, prefix) for prefix in PID1_UNIT_PATH_PREFIXES
        ):
            raise ValueError(
                f"scope=user for '{plan.filename}' cannot use system unit path "
                f"'{plan.path}'. Set path to a per-user location such as "
                "/home/<user>/.config/systemd/user."
            )


def assert_users_exist(plans: tuple[UnitPlan, ...], users: dict[str, Any]) -> None:
    """``Ensure unit file owners exist in passwd``, the role's second preflight.

    The role asserted it against ``getent_passwd``; the same question is asked of
    :func:`~reemote.modules.server.Users`, which reads the same passwd database.
    Only reached when :func:`needs_passwd_read` took the barrier (D2).

    Raises:
        ValueError: An entry names a user the host has no account for -- the
            role's ``fail_msg``, verbatim.
    """
    for plan in plans:
        if plan.user not in users:
            raise ValueError(
                f"unit_config user '{plan.user}' for '{plan.filename}' was not "
                "found in passwd. Create the account before managing its units."
            )


def unit_owner(plan: UnitPlan, users: dict[str, Any] | None) -> tuple[str, str]:
    """The ``owner``/``group`` pair of :attr:`UnitPlan.dest` -- ``_own_as_user``.

    Non-root ownership applies only when the unit is user-scoped *and* its path
    sits under that user's home, which is the role's defence against a home
    symlink tricking a root converge into chowning a system path. Everything else
    -- every system load path, whatever the entry's user -- stays ``root:root``.
    A user with no home in the passwd read never owns the file, as the empty
    ``getent`` field made the role's own match fail.
    """
    if plan.scope == "user" and users is not None:
        home = (users.get(plan.user) or {}).get("home")
        if home and _under(plan.path, str(home)):
            return plan.user, plan.group
    return DEFAULT_UNIT_USER, DEFAULT_UNIT_USER


def manager_args(plan: UnitPlan, users: dict[str, Any] | None) -> dict[str, Any]:
    """The scope arguments of a request that talks to this unit's manager.

    Empty for a system unit -- the plain ``systemctl`` the role ran as root. For a
    user-scoped unit it is ``user_name=<user>`` (``systemctl
    --machine=<user>@.host``, D1) plus the ``XDG_RUNTIME_DIR=/run/user/<uid>`` the
    role set through ``environment:``, as :func:`~reemote.core.op`'s own ``env``
    meta-parameter. The value is a *tuple of pairs*, not a dict, so the request
    stays hashable; ``_coerce_env`` sorts it into the same normalised form.
    """
    if plan.scope != "user":
        return {}
    args: dict[str, Any] = {"user_name": plan.user}
    uid = (users or {}).get(plan.user, {}).get("uid")
    if uid is not None:
        args["env"] = (("XDG_RUNTIME_DIR", f"/run/user/{uid}"),)
    return args


def changed_names(report: "ApplyReport", host_name: str) -> frozenset[str]:
    """The request names that reported a change for one host in one wave.

    :func:`~reemote.handlers.changed_hosts`'s per-request analogue. These handlers
    loop over a registered result list and gate on each item's own ``changed``,
    so the name -- not just the host -- has to survive the checkpoint (D3).
    Skipped and unchanged entries never qualify, and a converged pass returns the
    empty set, which is what makes the handler deploys yield nothing.
    """
    return frozenset(
        entry.name for entry in report.log if entry.host == host_name and entry.changed
    )


# --- helper deploys ----------------------------------------------------------


def configure(
    plans: tuple[UnitPlan, ...], users: dict[str, Any] | None
) -> Deploy:
    """``tasks/common/config.yml``: the load path, then the rendered file.

    Both tasks of the role's loop, in its order: the directory only when the entry
    named a path (and with ``_own_as_user``'s owner pair, D6), then the unit or the
    drop-in file, written by the spool-and-install family because a root-owned
    ``/etc`` -- or another user's home -- is not writable by the login user. The
    render request names are the handler channels; the directory task notified
    nothing in the role, so it notifies nothing here.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for plan in plans:
            owner, group = unit_owner(plan, users)
            if plan.path_defined:
                yield op(
                    files.directory,
                    name=f"Create unit configuration path ({plan.path})",
                    path=plan.path,
                    user=owner,
                    group=group,
                    mode=DIR_MODE,
                    sudo=True,
                )
            yield op(
                files.install,
                name=plan.render_name,
                content=plan.content,
                dest=plan.dest,
                mode=UNIT_MODE,
                user=owner,
                group=group,
                sudo=True,
            )

    return deploy


def reload_units(
    plans: tuple[UnitPlan, ...], changed: frozenset[str], users: dict[str, Any] | None
) -> Deploy:
    """``Reload systemd units``: one reload per unit whose own file changed.

    The handler's three gates, verbatim: the entry's render task reported
    changed, ``reload_on_change`` was not switched off, and the name is not a
    template unit. Drop-ins are excluded -- only ``render_units`` notified this
    handler; the ``.conf`` entries notified the daemon reload instead.

    ``state: {{ item.unit_item.state | default(omit) }}`` is the reason
    :attr:`UnitPlan.state` keeps its ``None``: an entry that named no state gets
    ``running=None``, so the reload re-reads the unit file without starting or
    stopping anything, while ``state: started`` restarts into the running state
    and ``stopped`` stops it. ``daemon_reload=True`` is the handler's own
    ``daemon_reload: true``, and it is state-gated (issue #50): the command is
    issued only while the unit reports a stale cached fragment, which is what
    keeps a replayed handler convergent.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for plan in plans:
            if plan.is_conf or plan.render_name not in changed:
                continue
            if not plan.reload_on_change or plan.is_instance:
                continue
            yield op(
                sd.service,
                name=f"Reload systemd unit {plan.filename}",
                service=plan.filename,
                running=None if plan.state is None else plan.state == "started",
                daemon_reload=True,
                sudo=True,
                **manager_args(plan, users),
            )

    return deploy


def reload_daemon(
    plans: tuple[UnitPlan, ...], changed: frozenset[str], users: dict[str, Any] | None
) -> Deploy:
    """``Reload systemd daemon``: one reload per drop-in that changed.

    The role's name-less ``systemd: daemon_reload=true`` -- a whole-manager
    reload, which no unit-scoped flag can express -- so the request carries
    :func:`~reemote.modules.systemd.daemon_reload` with no ``unit``, the stateless
    form. It converges because it is only ever reached from a *changed* drop-in:
    a second pass reports nothing and yields nothing. One request per changed
    entry, as the role's loop issued one per loop item.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for plan in plans:
            if not plan.is_conf or plan.render_name not in changed:
                continue
            yield op(
                sd.daemon_reload,
                name=f"Reload systemd daemon ({plan.dest})",
                sudo=True,
                **manager_args(plan, users),
            )

    return deploy


def launch(plans: tuple[UnitPlan, ...], users: dict[str, Any] | None) -> Deploy:
    """``Activate configured Systemd units``: the state and boot setting.

    Same two gates as the role's task -- no drop-ins, no template units -- and the
    same three arguments. ``manage_state: false`` maps ``state`` to
    :data:`None`, leaving the active state alone while still asserting
    :attr:`UnitPlan.enabled`; an entry naming no state takes the role's
    ``_default_unit_state``, which is ``stopped``: the role only *activated* what
    you asked it to. ``enabled`` is always passed, so an entry that names nothing
    is taken off boot.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for plan in plans:
            if plan.is_conf or plan.is_instance:
                continue
            running = (
                None
                if not plan.manage_state
                else (plan.state or DEFAULT_UNIT_STATE) == "started"
            )
            yield op(
                sd.service,
                name=f"Activate systemd unit {plan.filename}",
                service=plan.filename,
                running=running,
                enabled=plan.enabled,
                sudo=True,
                **manager_args(plan, users),
            )

    return deploy


def uninstall(plans: tuple[UnitPlan, ...], users: dict[str, Any] | None) -> Deploy:
    """``Uninstall units``: stop and disable, then remove the managed files.

    Both of the handler's tasks, in order: the ``systemd`` one over the units
    (dropping drop-ins and template units, exactly as its ``when:`` did) asking
    for ``state: stopped`` and ``enabled: false``, the ``file`` one over *every*
    entry, drop-ins included. The removal is ``force=True`` because a path that is a
    directory here (a hand-made ``.d``) has to go, and ``create_remote_dir`` is
    irrelevant to an absent one.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for plan in plans:
            if plan.is_conf or plan.is_instance:
                continue
            yield op(
                sd.service,
                name=f"Stop and disable systemd unit {plan.filename}",
                service=plan.filename,
                running=False,
                enabled=False,
                sudo=True,
                **manager_args(plan, users),
            )
        for plan in plans:
            yield op(
                files.file,
                name=f"Remove systemd unit file {plan.dest}",
                path=plan.dest,
                present=False,
                force=True,
                sudo=True,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def systemd(v: VarsSet[SystemdVars]) -> Deploy:
    """Configure, activate or uninstall systemd units on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`SystemdVars` per host
            (``resolve_vars(SystemdVars, "systemd", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: A ``scope: user`` unit names a PID 1 load path, or a unit's
            user does not exist in the host's passwd. Both are the role's
            preflight assertions (D8); the first is raised before any host is
            contacted beyond the passwd read, the second as soon as that read
            lands.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu" or not hv.unit_config:
            return

        plans = resolve_units(hv)
        assert_not_pid1_user_scope(plans)

        users: dict[str, Any] | None = None
        if needs_passwd_read(plans):
            # Wave 0 (conditional): the one read the rest of the role's derived
            # variables depend on -- unit owners, their homes, their uids.
            users = yield fact(Users, name="Resolve passwd entries for unit owners")
            assert_users_exist(plans, users)

        if hv.perform_uninstall:
            # The role skips config and launch entirely in this mode; the signal
            # task that told its handlers to run has nothing to signal here.
            yield from uninstall(plans, users)(host)
            return

        # Wave 1: every managed file, then read this host's own outcomes. The
        # checkpoint must immediately follow the stage it reports on -- one placed
        # after the handler wave would read the handlers' entries instead.
        yield from configure(plans, users)(host)
        wave_report = yield checkpoint("configure done")
        changed = changed_names(wave_report, host.name)

        # Wave 2: the two handlers and the activations, in the play's order and
        # in one wave -- the driver applies each request before pulling the next,
        # so ``daemon-reload`` still precedes the ``systemctl start`` that needs
        # the reloaded unit file.
        yield from reload_units(plans, changed, users)(host)
        yield from reload_daemon(plans, changed, users)(host)
        yield from launch(plans, users)(host)

    return deploy


__all__ = [
    "SystemdVars",
    "UnitSpec",
    "systemd",
]
