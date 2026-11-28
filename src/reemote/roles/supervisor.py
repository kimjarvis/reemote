# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Supervisor role (migrated from ``geerlingguy.supervisor``).
#
# Installs Supervisor on Ubuntu hosts: the pip-installed ``supervisor`` package
# (optionally version-pinned), the ``0755`` log directory, the config paths and
# ``conf.d``, the rendered ``{{ config_path }}/supervisord.conf``, the per-program
# ``conf.d/<name>.conf`` files (present and absent directions), the rendered
# ``/etc/systemd/system/supervisord.service`` unit, and the started/enabled
# service state; any config or unit change queues the role's one handler,
# ``restart supervisor``. :func:`supervisor` is the whole task list and the
# handler, :class:`SupervisorVars` is ``defaults/main.yml``, bound from the
# inventory entry's ``supervisor`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family, and the systemd service manager.
# The role's RedHat branches (``vars/RedHat.yml``, ``vars/RedHat-7.yml``,
# ``supervisord.init-RedHat.j2``) and its non-systemd init-script branch
# (``supervisord.init-Debian.j2``) are out of scope, so its ``include_vars`` OS
# dispatch collapses to the frozen Debian constants below (the Debian vars table
# only sets the two ``/usr/local/bin`` paths) and the ``service_mgr`` condition
# in ``init-setup.yml`` is always true.
#
# The stream is two waves and one checkpoint:
#
# 0. :func:`configure` -- the package, the log directory, the two config
#    directories, the rendered main config, the program configs, the rendered
#    unit, and the started/enabled service -- closed by
#    ``checkpoint("configure done")``.
# 1. :func:`handler` -- ``Restart supervisord (handler)`` with a state-gated
#    daemon-reload, only on the hosts that checkpoint selected *and* only while
#    ``started`` is true (the handler task's ``when:``). An empty notification
#    set yields no request, the converged no-op Ansible's handler gives for free.
#
# **No facts at all**, and no ``act``/``control`` either: the role reads nothing
# mid-play -- every template input is a resolved variable or a frozen constant,
# so all three renders are pure control-side Python inside the body and the
# checkpoint is the entire barrier surface (the redis/memcached precedent). The
# request order reproduces ``tasks/main.yml`` exactly: package, log dir, config
# paths, main config, program configs, unit file, service state.
#
# Prerequisite (the role's own README requirement): ``pip`` must exist on the
# host -- Ansible says "use the ``geerlingguy.pip`` role first", which in reemote
# is :func:`reemote.roles.pip.pip`, run before this one.
#
# The config renders use Jinja2 (the ``{% if %}`` guards of
# ``templates/supervisord.conf.j2`` and the ``is defined`` test of
# ``program.conf.j2``), imported only through :mod:`reemote.roles._render`
# inside the render functions, so importing this module never requires it --
# rendering needs the ``reemote[templates]`` extra.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: an off-target host emits no requests and no log lines at all.
# * D2: the ``include_vars`` OS dispatch collapses to frozen constants -- the
#   Debian ``supervisor_bin_path``/``supervisorctl_bin_path`` become
#   :data:`BIN_PATH`/:data:`CTL_BIN_PATH` -- and the RedHat branches are scoped
#   out (the Ubuntu-target decision of the Phase 0 scope).
# * D3: the non-systemd init-script branch is scoped out (the same Phase 0
#   decision): the unit file is always the systemd one. Modern Ubuntu is
#   always systemd; the 173-line SysV template is not ported.
# * D4: Ansible's ``hash('sha1')`` filter is reproduced as a registered Jinja2
#   filter (``hashlib``), so ``supervisord.conf.j2`` stays verbatim and the
#   ``{SHA}<hexdigest>`` password line is byte-identical.
# * D5: the two final ``service:`` tasks (``state: started`` gated on
#   ``supervisor_started``, ``enabled: true`` gated on ``supervisor_enabled``)
#   collapse to one ``systemd.service`` request whose ``running``/``enabled``
#   flags are the ``None`` sentinels when their gate is false -- the exact
#   mirror of a skipped Ansible task (issue #63): the role never stops or
#   disables the unit.
# * D6: ``daemon_reload=True`` on the service request and the handler. Ansible's
#   systemd modules daemon-reload implicitly after a unit-file change; reemote
#   spells it out, and the reload is state-gated on ``SystemdNeedDaemonReload``
#   (issue #50), so a converged run yields no reload command.
# * D7: the pip executable is an explicit variable (:attr:`SupervisorVars.pip`,
#   default ``"pip3"``) instead of the Ansible pip module's interpreter
#   auto-detection -- the same choice the shipped ``pip`` role makes, and it
#   lets a virtualenv's pip be targeted by path.
# * D8: the handler's ``when: supervisor_started`` gate is a body-level check of
#   the resolved variable (static, deterministic), not a request ``when=``.
# * D9: ``files.install`` idempotence is content-only, so an owner/mode drift on
#   unchanged content neither re-installs nor queues the restart; Ansible's
#   template module reports a change there too. Accepted for the root-owned
#   ``0644`` files (the apache precedent, not redis's companion: nothing here is
#   service-owned, so no daemon can be locked out of its own config).
# """

import hashlib
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import Host
from ..modules import files, pip, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: ``vars/Debian.yml``, frozen to the single Ubuntu target (D2): the pip-installed
#: console scripts the unit file's ``ExecStart``/``ExecStop``/``ExecReload`` run.
BIN_PATH = "/usr/local/bin/supervisord"
CTL_BIN_PATH = "/usr/local/bin/supervisorctl"

#: The unit name everywhere: the ``service:`` tasks, the handler, and the
#: ``init-setup.yml`` destination ``/etc/systemd/system/supervisord.service``.
SERVICE = "supervisord"
UNIT_PATH = "/etc/systemd/system/supervisord.service"

#: ``defaults/main.yml``'s ``supervisor_config_path`` default: a variable
#: (:attr:`SupervisorVars.config_path`), but its ``/conf.d`` child and the
#: ``supervisord.conf``/``*.conf`` file names inside it are the templates' and
#: the tasks' own literals.
CONF_DIR_NAME = "conf.d"
CONF_FILE_NAME = "supervisord.conf"

#: The modes the tasks set: ``0755`` on all three directories, ``0644`` on the
#: main config, program configs and the unit file.
DIR_MODE = "0755"
CONF_MODE = "0644"


#: ``templates/supervisord.conf.j2``, verbatim. The ``{SHA}``-prefixed password
#: lines ride the ``hash`` filter :func:`render_supervisord_conf` registers
#: (D4); everything else is plain substitution and ``{% if %}`` guards. The
#: both-http-servers case keeps the upstream quirk of two ``[supervisorctl]``
#: sections exactly as the bundled template has it.
SUPERVISORD_CONF_TEMPLATE = """[supervisord]
nodaemon = {{ supervisor_nodaemon }}
childlogdir = {{ supervisor_log_dir }}
user = {{ supervisor_user }}
pidfile = /var/run/supervisord.pid

{% if supervisor_unix_http_server_enable %}
[unix_http_server]
file = {{ supervisor_unix_http_server_socket_path }}
chown = {{ supervisor_user }}
{% if supervisor_unix_http_server_password_protect %}
username = {{ supervisor_user }}
password = {SHA}{{ supervisor_password|hash('sha1') }}
{% endif %}

[supervisorctl]
serverurl = unix://{{ supervisor_unix_http_server_socket_path }}
{% if supervisor_unix_http_server_password_protect %}
username = {{ supervisor_user }}
password = {{ supervisor_password }}
{% endif %}
{% endif -%}

{%- if supervisor_inet_http_server_enable %}
[inet_http_server]
port = {{ supervisor_inet_http_server_port }}
{% if supervisor_inet_http_server_password_protect %}
username = {{ supervisor_user }}
password = {SHA}{{ supervisor_password|hash('sha1') }}
{% endif %}

[supervisorctl]
serverurl = http://{{ 'localhost' if supervisor_inet_http_server_port.split(':')[0] == '*' or supervisor_inet_http_server_port.split(':')[0] == '' else supervisor_inet_http_server_port.split(':')[0] }}:{{ supervisor_inet_http_server_port.split(':')[1] }}
{% if supervisor_inet_http_server_password_protect %}
username = {{ supervisor_user }}
password = {{ supervisor_password }}
{% endif %}
{% endif %}

[include]
files = {{ supervisor_include_files }}

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface
"""

#: ``templates/program.conf.j2``, verbatim: one ``[program:<name>]`` section,
#: its command, and the optional raw ``configuration`` block (the ``is defined``
#: test reads a program dumped with ``exclude_none=True``).
PROGRAM_CONF_TEMPLATE = """[program:{{ item.name }}]
command={{ item.command }}
{% if item.configuration is defined %}
{{ item.configuration }}
{% endif %}
"""

#: ``templates/supervisord.unit.j2``, verbatim (the systemd branch of
#: ``init-setup.yml``; D3 scopes the init-script alternative out).
UNIT_TEMPLATE = """# supervisord service for systemd
# Originally by ET-CS (https://github.com/ET-CS)
[Unit]
Description=Supervisor daemon

[Service]
Type=forking
ExecStart={{ supervisor_bin_path }} -c {{ supervisor_config_path }}/supervisord.conf
ExecStop={{ supervisorctl_bin_path }} $OPTIONS shutdown
ExecReload={{ supervisorctl_bin_path }} $OPTIONS reload
KillMode=process
Restart=on-failure
RestartSec=42s

[Install]
WantedBy=multi-user.target
"""


# --- variables ---------------------------------------------------------------


class Program(BaseModel):
    """One managed program: an item of ``supervisor_programs``.

    Only :attr:`name` is always required; :attr:`command` is required when the
    program is present (the template renders it unconditionally), while an
    ``absent`` item needs nothing but its name -- that is the removal task's own
    gate. :attr:`state` is the role's ``item.state|default('present')`` spelled
    as a :data:`~typing.Literal`, and :attr:`configuration` stays ``None`` when
    unset so ``model_dump(exclude_none=True)`` omits it and the template's
    ``{% if item.configuration is defined %}`` behaves exactly like Ansible's
    undefined check. ``extra="allow"`` keeps any further key the upstream
    template might learn to read; the model is frozen so the containing
    :class:`SupervisorVars` stays hashable.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    command: str = ""
    state: Literal["present", "absent"] = "present"
    configuration: str | None = None

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        """An empty program name would build ``conf.d/.conf``."""
        if not value:
            raise ValueError("a program needs a name")
        return value

    @model_validator(mode="after")
    def _command_required_for_present(self) -> "Program":
        """The present template renders ``command=`` unconditionally.

        A model-level (not field) check: ``state`` is declared after ``command``,
        so a ``field_validator`` on ``command`` cannot yet read ``state`` from
        ``info.data``. An ``absent`` item is the removal task's own gate and needs
        nothing but its name.
        """
        if self.state == "present" and not self.command:
            raise ValueError("a present program needs a command")
        return self


class SupervisorVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``supervisor`` role mapping, so ``config_path`` there sets
    :attr:`config_path`. Every Ansible default is carried over. Sequences are
    ``tuple``, never ``list``: the model is frozen and hashable, and a value
    that reaches :func:`~reemote.core.op` must hash (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``supervisor_version``: an empty string installs unpinned (the role's
    #: ``version: {{ supervisor_version | default(omit) }}``), a value pins
    #: ``supervisor==<version>`` through the pip operation's version match.
    version: str = ""
    #: ``supervisor_started``: whether the unit runs, and whether the restart
    #: handler is eligible at all (the handler task's ``when:``, D8).
    started: bool = True
    #: ``supervisor_enabled``: whether the unit starts on boot.
    enabled: bool = True
    #: ``supervisor_config_path``: where ``supervisord.conf`` and ``conf.d`` live.
    config_path: str = "/etc/supervisor"
    #: ``supervisor_include_files``: an empty string derives the role's default
    #: ``{{ supervisor_config_path }}/conf.d/*.conf`` at render time, so changing
    #: only :attr:`config_path` follows, exactly as the Jinja default does.
    include_files: str = ""
    #: ``supervisor_programs``: the managed program list.
    programs: tuple[Program, ...] = ()
    #: ``supervisor_nodaemon``: the ``[supervisord]`` foreground flag.
    nodaemon: bool = False
    #: ``supervisor_log_dir``: the ``childlogdir`` the role creates first.
    log_dir: str = "/var/log/supervisor"
    #: ``supervisor_user``: the ``[supervisord] user=`` and http-server username.
    user: str = "root"
    #: ``supervisor_password``: the http-server password, ``{SHA}``-hashed into
    #: the server sections and written plainly for ``[supervisorctl]``.
    password: str = "my_secret_password"
    #: ``supervisor_unix_http_server_enable`` and its socket path / password gate.
    unix_http_server_enable: bool = True
    unix_http_server_socket_path: str = "/var/run/supervisor.sock"
    unix_http_server_password_protect: bool = True
    #: ``supervisor_inet_http_server_enable`` and its port / password gate. The
    #: opt-in branch (default false) is carried through the verbatim template --
    #: it costs no extra wave.
    inet_http_server_enable: bool = False
    inet_http_server_port: str = "*:9001"
    inet_http_server_password_protect: bool = True
    #: The pip executable the install runs (D7): ``pip3`` on an Ubuntu host that
    #: took the ``pip`` role's default; a virtualenv's ``bin/pip`` path also fits.
    pip: str = "pip3"

    @field_validator("config_path", "log_dir", "user", "pip")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty path/user/executable is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("unix_http_server_socket_path")
    @classmethod
    def _socket_path_required(cls, value: str) -> str:
        """The default config enables the unix server, so its path is used."""
        if not value:
            raise ValueError("the unix http server needs a socket path")
        return value


@dataclass(frozen=True)
class SupervisorPlan:
    """One host's resolved targets, built by :func:`supervisor_plan`.

    Every value the configure and handler stages need is decided *before* the
    first request of the wave is built -- the three rendered files, the pinned
    or bare package spec, the two gate flags -- so both stages are a pure
    function of this one frozen object. Under a per-host body the plan is a
    local, not a registry.
    """

    package_specs: tuple[str, ...]
    pip: str
    log_dir: str
    config_path: str
    config_content: str
    programs_present: tuple[tuple[str, str], ...]
    programs_absent: tuple[str, ...]
    unit_content: str
    write_unit: bool
    started: bool
    enabled: bool


# --- pure helpers ------------------------------------------------------------


def ansible_hash(value: str, algorithm: str) -> str:
    """Ansible's ``hash`` filter, as a Jinja2 filter (D4).

    ``{{ password | hash('sha1') }}`` is ``hashlib.new(algorithm,
    value.encode()).hexdigest()`` -- the exact digest supervisor stores with its
    ``{SHA}`` prefix. Any unknown algorithm raises ``ValueError`` through
    ``hashlib`` itself.
    """
    return hashlib.new(algorithm, value.encode("utf-8")).hexdigest()


def derive_include_files(hv: SupervisorVars) -> str:
    """``supervisor_include_files``' Jinja default, evaluated in Python.

    The role declares ``'{{ supervisor_config_path }}/conf.d/*.conf'``, so an
    override of the config path moves the include glob unless the glob itself
    was overridden; an empty :attr:`SupervisorVars.include_files` reproduces
    exactly that derivation.
    """
    return hv.include_files or f"{hv.config_path}/{CONF_DIR_NAME}/*.conf"


def render_supervisord_conf(hv: SupervisorVars) -> str:
    """``templates/supervisord.conf.j2``, pre-rendered control-side (recipe 4).

    The environment is :func:`~reemote.roles._render.jinja2_environment`
    (Ansible's whitespace defaults, ``StrictUndefined``) with the ``hash``
    filter registered (D4). The context keeps the role's original
    ``supervisor_*`` names because the bundled template does;
    ``supervisor_nodaemon`` stays a Python ``bool`` so Jinja prints ``True`` /
    ``False`` exactly as Ansible does (supervisor's ini parser accepts both).
    The jinja2 import lives inside this function so importing
    ``reemote.roles`` stays clean without the ``reemote[templates]`` extra.
    """
    from ._render import jinja2_environment

    environment = jinja2_environment()
    environment.filters["hash"] = ansible_hash
    template = environment.from_string(SUPERVISORD_CONF_TEMPLATE)
    return template.render(
        supervisor_nodaemon=hv.nodaemon,
        supervisor_log_dir=hv.log_dir,
        supervisor_user=hv.user,
        supervisor_unix_http_server_enable=hv.unix_http_server_enable,
        supervisor_unix_http_server_socket_path=hv.unix_http_server_socket_path,
        supervisor_unix_http_server_password_protect=(
            hv.unix_http_server_password_protect
        ),
        supervisor_inet_http_server_enable=hv.inet_http_server_enable,
        supervisor_inet_http_server_port=hv.inet_http_server_port,
        supervisor_inet_http_server_password_protect=(
            hv.inet_http_server_password_protect
        ),
        supervisor_password=hv.password,
        supervisor_include_files=derive_include_files(hv),
    )


def render_program_conf(program: Program) -> str:
    """``templates/program.conf.j2``, pre-rendered control-side, for one item.

    The item is dumped with ``exclude_none=True`` so an unset
    :attr:`Program.configuration` stays *undefined* for the template's
    ``{% if %}`` guard, exactly as on a host that never set the key.
    """
    from ._render import jinja2_environment

    environment = jinja2_environment()
    template = environment.from_string(PROGRAM_CONF_TEMPLATE)
    return template.render(item=program.model_dump(exclude_none=True))


def render_unit_conf(hv: SupervisorVars) -> str:
    """``templates/supervisord.unit.j2``, pre-rendered control-side.

    ``supervisor_bin_path``/``supervisorctl_bin_path`` are the frozen Debian
    constants (D2); only the config path is a variable.
    """
    from ._render import jinja2_environment

    environment = jinja2_environment()
    template = environment.from_string(UNIT_TEMPLATE)
    return template.render(
        supervisor_bin_path=BIN_PATH,
        supervisor_config_path=hv.config_path,
        supervisorctl_bin_path=CTL_BIN_PATH,
    )


def supervisor_plan(hv: SupervisorVars) -> SupervisorPlan:
    """Freeze everything the waves read: three renders, the spec, the gates.

    Control-side work inside the body, before the first request is built. The
    package spec is ``(supervisor,)`` or the ``(supervisor==<version>)`` pin
    (an empty version omits the pin, the role's ``default(omit)`` reading).
    ``write_unit`` is ``init-setup.yml``'s inclusion gate: the unit file is
    managed only when the service is started *or* enabled.
    """
    package = f"supervisor=={hv.version}" if hv.version else "supervisor"
    return SupervisorPlan(
        package_specs=(package,),
        pip=hv.pip,
        log_dir=hv.log_dir,
        config_path=hv.config_path,
        config_content=render_supervisord_conf(hv),
        programs_present=tuple(
            (program.name, render_program_conf(program))
            for program in hv.programs
            if program.state == "present"
        ),
        programs_absent=tuple(
            program.name for program in hv.programs if program.state == "absent"
        ),
        unit_content=render_unit_conf(hv),
        write_unit=hv.started or hv.enabled,
        started=hv.started,
        enabled=hv.enabled,
    )


def conf_d_path(plan: SupervisorPlan) -> str:
    """``{{ supervisor_config_path }}/conf.d`` -- the program configs' directory."""
    return f"{plan.config_path}/{CONF_DIR_NAME}"


def notifying_names(plan: SupervisorPlan) -> tuple[str, ...]:
    """Every request name that queues ``restart supervisor``.

    The role's four notifying tasks: the main config template, the program
    configs in both directions, and the unit-file template (only when
    :func:`supervisor_plan` decided to write it). The pip, directory and final
    service tasks carry no ``notify`` in the role, so their names are
    deliberately absent.
    """
    names = ["Ensure Supervisor configuration is present"]
    names.extend(
        f"Ensure Supervisor program config is present ({name})"
        for name, _content in plan.programs_present
    )
    names.extend(
        f"Ensure Supervisor program config is absent ({name})"
        for name in plan.programs_absent
    )
    if plan.write_unit:
        names.append("Ensure Supervisor systemd unit is present")
    return tuple(names)


# --- helper deploys ----------------------------------------------------------


def configure(plan: SupervisorPlan) -> Deploy:
    """``tasks/main.yml`` (install + log dir), ``config.yml`` and ``init-setup.yml``.

    Branch-free with respect to facts: the plan is entirely static, so this is
    one pure request wave in the role's own order. Each notifying request
    carries a stable ``name=`` enumerated by :func:`notifying_names`; the
    transfers never elevate (the spool-and-install family, ``sudo=True`` on the
    request) while the directory/file edits and systemctl do need root.
    """
    conf_d = conf_d_path(plan)

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # tasks/main.yml: pip name=supervisor (version-pinned when set). The
        # executable is the explicit variable (D7); the system site-packages is
        # root-owned, so the request elevates.
        yield op(
            pip.packages,
            name="Ensure Supervisor is installed",
            packages=plan.package_specs,
            pip=plan.pip,
            sudo=True,
        )
        # tasks/main.yml: file state=directory mode=0755 (no owner in the role).
        yield op(
            files.directory,
            name="Ensure Supervisor log dir exists",
            path=plan.log_dir,
            mode=DIR_MODE,
            sudo=True,
        )
        # tasks/config.yml: the config path and its conf.d child, root:root 0755.
        yield op(
            files.directory,
            name=f"Ensure Supervisor config paths exist ({plan.config_path})",
            path=plan.config_path,
            user="root",
            group="root",
            mode=DIR_MODE,
            sudo=True,
        )
        yield op(
            files.directory,
            name=f"Ensure Supervisor config paths exist ({conf_d})",
            path=conf_d,
            user="root",
            group="root",
            mode=DIR_MODE,
            sudo=True,
        )
        # The main template task (mode 0644, notify): rendered control-side.
        yield op(
            files.install,
            name="Ensure Supervisor configuration is present",
            content=plan.config_content,
            dest=f"{plan.config_path}/{CONF_FILE_NAME}",
            user="root",
            group="root",
            mode=CONF_MODE,
            sudo=True,
        )
        # The program loop's present direction: one rendered install per item.
        for name, content in plan.programs_present:
            yield op(
                files.install,
                name=f"Ensure Supervisor program config is present ({name})",
                content=content,
                dest=f"{conf_d}/{name}.conf",
                user="root",
                group="root",
                mode=CONF_MODE,
                sudo=True,
            )
        # The program loop's absent direction: rm -f through files.file.
        for name in plan.programs_absent:
            yield op(
                files.file,
                name=f"Ensure Supervisor program config is absent ({name})",
                path=f"{conf_d}/{name}.conf",
                present=False,
                sudo=True,
            )
        # tasks/init-setup.yml, systemd branch (D3): the rendered unit file.
        if plan.write_unit:
            yield op(
                files.install,
                name="Ensure Supervisor systemd unit is present",
                content=plan.unit_content,
                dest=UNIT_PATH,
                user="root",
                group="root",
                mode=CONF_MODE,
                sudo=True,
            )
        # The two gated service tasks as one request (D5): the None sentinel
        # mirrors a skipped task, so neither flag false ever stops/disables.
        # daemon_reload is the implicit Ansible reload, state-gated (D6, #50) --
        # this is what starts a freshly written unit file on the first pass.
        yield op(
            systemd.service,
            name="Ensure Supervisor is started and enabled",
            service=SERVICE,
            running=True if plan.started else None,
            enabled=True if plan.enabled else None,
            daemon_reload=True,
            sudo=True,
        )

    return deploy


def handler(notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart supervisor`` (the role's one handler).

    ``restart_handler`` closes over the notification set the checkpoint read
    produced and branches on ``host.name`` itself, so an unnotified host yields
    nothing. ``restarted=True`` restarts a running unit and starts a stopped one
    -- Ansible's ``state: restarted`` -- and ``daemon_reload=True`` reloads the
    daemon first, state-gated (D6), which is the point after a unit-file
    rewrite. The body only reaches this handler while ``started`` is true, the
    handler task's own ``when:`` (D8).
    """
    return restart_handler(notified, SERVICE, daemon_reload=True)


# --- the role ----------------------------------------------------------------


def supervisor(v: VarsSet[SupervisorVars]) -> Deploy:
    """Install and configure Supervisor on Ubuntu hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`SupervisorVars` per host
            (``resolve_vars(SupervisorVars, "supervisor", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ImportError: The ``reemote[templates]`` extra is missing. The renders
            happen inside the body, while the first request wave is built, so
            the failure arrives at the moment the consumer needs the extra.
            Otherwise the role has no in-body error path -- no barrier to
            misread, no version to gate on -- and bad *variables* are rejected
            earlier, by :func:`~reemote.rolevars.resolve_vars`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the whole task list -- static plan, no fact barriers -- closed
        # by the role's only barrier. The checkpoint must immediately follow the
        # stage it reports on; the handler wave's entries would be invisible to
        # a second checkpoint, and nothing here needs one.
        plan = supervisor_plan(hv)
        yield from configure(plan)(host)
        wave_report = yield checkpoint("configure done")

        # Wave 1: the handler, only on the hosts this checkpoint selected, and
        # only while the handler task's ``when: supervisor_started`` holds (D8).
        notified = changed_hosts(wave_report, notifying_names(plan))
        if hv.started:
            yield from handler(notified)(host)

    return deploy


__all__ = [
    "Program",
    "SupervisorVars",
    "supervisor",
]
