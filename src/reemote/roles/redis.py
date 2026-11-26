# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Redis role (migrated from ``geerlingguy.redis``).
#
# Installs Redis on Ubuntu hosts: the ``/etc/redis`` configuration directory
# (redis:redis, mode ``02770``), the rendered ``/etc/redis/redis.conf``
# (redis:redis, mode ``0640``) and its metadata companion, the ``redis-server``
# package, the started-and-enabled service; a config change queues the role's
# one handler, ``restart redis``. :func:`redis` is the whole task list and the
# handler, :class:`RedisVars` is ``defaults/main.yml`` (plus the Debian
# ``__redis_package`` the ``set_fact`` task copies into ``redis_package``), bound
# from the inventory entry's ``redis`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family returns
# before its first ``yield`` and emits no requests, no barriers and no log lines
# at all. The role's RedHat and Archlinux branches (``vars/RedHat.yml``,
# ``vars/Archlinux.yml``, ``setup-RedHat.yml``, ``setup-Archlinux.yml``) are out
# of scope, and because the vars split only by OS family -- not by release -- the
# single-OS scope collapses the ``include_vars`` dispatch to frozen module
# constants: no ``LinuxDistribution`` barrier exists (the memcached precedent).
#
# The stream is two waves and one checkpoint:
#
# 0. :func:`configure` -- the configuration directory, the rendered config
#    install and its metadata companion (both notify), the package, and the
#    started/armed service -- closed by ``checkpoint("configure done")``.
# 1. :func:`handler` -- ``restart redis``, only on the hosts that checkpoint
#    selected. An empty notification set yields no request, the converged no-op
#    Ansible's handler gives for free.
#
# **No facts at all**, and no cache refresh either: the role's Debian path
# carries no ``update_cache``, so the checkpoint is the only barrier in the whole
# role. What the role *does* carry, and this migration keeps, is an ordering
# quirk: on Debian the package install starts Redis immediately, so
# ``tasks/main.yml`` runs :func:`configure`'s file tasks **before** the apt task
# -- e.g. so Redis can start on hosts with IPv6 disabled. The request order
# inside the single wave reproduces that sequence exactly: directory, config,
# companion, package, service.
#
# Prerequisites: the config render uses Jinja2 (the ``{% if %}`` guards and the
# three ``{% for %}`` loops of ``templates/redis.conf.j2``), imported only
# through :mod:`reemote.roles._render` inside :func:`render_redis_conf`, so
# importing this module never requires it -- rendering the config needs the
# ``reemote[templates]`` extra.
#
# Deviations from the Ansible role, all deliberate:
#
# * the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: an off-target host emits no requests and no ``[skipped]``
#   log lines.
# * ``ansible_managed`` is frozen to the literal ``Ansible managed`` (D1):
#   Ansible's ansible.cfg default is a *timestamped* string, which would
#   rewrite the config on every run and fire the restart handler forever.
# * the ``include_vars``/``set_fact`` pair collapses to constants and fields
#   (D2): ``__redis_package`` becomes :attr:`RedisVars.package`'s Debian
#   default (the README documents ``redis_package`` as user-overridable), while
#   ``redis_daemon``, the paths and the two modes are frozen -- they are vars
#   table entries, not documented overrides.
# * a ``files.file`` metadata companion is added, and its name notifies (D3):
#   ``files.install``'s idempotence is content-only, so an owner/mode ``0640``
#   drift on unchanged content would never converge, while Ansible's template
#   module reports a change on owner/group/mode drift too (the java/memcached
#   companion precedent).
# * the RedHat and Archlinux paths are scoped out (D4): a Phase 0 single-OS
#   decision. ``redis_enablerepo`` is carried as a variable and does nothing
#   here -- the repo knob belongs to the RHEL branch.
# * the config file and directory are owned ``redis:redis``, not the template
#   task's implicit ``root:root`` (D6): the packaged unit runs
#   ``User=redis``, so a root-owned ``0640`` config is unreadable to the daemon
#   and the first handler restart after such a write fails with
#   ``Permission denied``. ``redis:redis`` is also the end state the Ubuntu
#   packaging itself converges to (dpkg re-owns the directory on install).
# * type gates move to :class:`RedisVars` (D5): a wrongly typed variable is a
#   pydantic rejection at ``resolve_vars`` time, before any host is contacted,
#   not a render-time surprise. Pydantic's lax mode still coerces a numeric
#   string (``"6380"``) into an :class:`int` field -- the repo-wide convention
#   (memcached's :attr:`~reemote.roles.memcached.MemcachedVars.port` behaves
#   the same) -- and the render is identical either way.
# """

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import Host
from ..modules import apt, files, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path) --------

#: ``vars/Debian.yml``'s ``__redis_package`` -- what the ``set_fact`` task
#: copies into ``redis_package`` when the user has not defined it. It becomes
#: :attr:`RedisVars.package`'s default (D2).
PACKAGE_DEFAULT = "redis-server"

#: ``vars/Debian.yml``'s ``redis_daemon``: on Debian the unit is named after
#: the package. The service task and the ``restart redis`` handler act on it.
SERVICE = "redis-server"

#: ``vars/Debian.yml``'s ``redis_conf_path`` and its directory, frozen: the
#: Ubuntu path never overrides the destination, so they are constants, not
#: variables (the ``dirname`` the file task takes is :data:`CONF_DIR`).
CONF_PATH = "/etc/redis/redis.conf"
CONF_DIR = "/etc/redis"

#: ``vars/Debian.yml``'s ``redis_conf_dir_mode`` / ``redis_conf_mode``: the
#: setgid ``02770`` directory and the ``0640`` config the Debian packaging
#: expects.
CONF_DIR_MODE = "02770"
CONF_MODE = "0640"

#: The owner of the directory and the config file: the packaged unit's
#: ``User=redis``/``Group=redis``, so the daemon can read its config (D6 --
#: the template task's implicit ``root:root`` would leave a ``0640`` file the
#: service cannot open).
CONF_USER = "redis"
CONF_GROUP = "redis"

#: ``templates/redis.conf.j2``, verbatim except the frozen ``ansible_managed``
#: header (D1). Rendered control-side by :func:`render_redis_conf` with
#: Ansible's whitespace defaults. The ``{% if %}`` guards and the ``{% for %}``
#: loops are why this role needs Jinja2 where a flat config would take an
#: f-string (the memcached/nginx precedent for the environment, php for not).
REDIS_CONF_TEMPLATE = """\
# Ansible managed

daemonize yes
pidfile /var/run/redis/{{ redis_daemon }}.pid
port {{ redis_port }}
bind {{ redis_bind_interface }}

{% if redis_unixsocket %}
unixsocket {{ redis_unixsocket }}
{% endif %}

timeout {{ redis_timeout }}

loglevel {{ redis_loglevel }}
logfile {{ redis_logfile }}

# To enable logging to the system logger, just set 'syslog-enabled' to yes,
# and optionally update the other syslog parameters to suit your needs.
# syslog-enabled no
# syslog-ident redis
# syslog-facility local0

databases {{ redis_databases }}

{% for save in redis_save %}
save {{ save }}
{% endfor %}

rdbcompression {{ redis_rdbcompression }}
dbfilename {{ redis_dbfilename }}
dir {{ redis_dbdir }}

# maxclients 128

{% if redis_maxmemory %}
maxmemory {{ redis_maxmemory }}
maxmemory-policy {{ redis_maxmemory_policy }}
maxmemory-samples {{ redis_maxmemory_samples }}
{% endif %}

appendonly {{ redis_appendonly }}
appendfsync {{ redis_appendfsync }}
no-appendfsync-on-rewrite no

{% for include in redis_includes %}
include {{ include }}
{% endfor %}

{% if redis_requirepass %}
requirepass {{ redis_requirepass }}
{% endif %}

{% for redis_disabled_command in redis_disabled_commands %}
rename-command {{ redis_disabled_command }} ""
{% endfor %}

{{ redis_extra_config }}
"""

#: The request names that queue ``restart redis`` -- the role's one ``notify:``
#: channel, which sits on the template task. The metadata companion joins it
#: because Ansible's template module reports a change on owner/group/mode drift
#: as well (D3). The directory task carries no ``notify`` in the role, so its
#: name is deliberately absent.
NOTIFY_RESTART: frozenset[str] = frozenset(
    {
        "Configure Redis",
        "Set Redis configuration attributes",
    }
)


# --- variables ---------------------------------------------------------------


class RedisVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``redis`` role mapping, so ``port`` there sets
    :attr:`port`. :attr:`package` has no ``defaults/main.yml`` entry; the role
    defines it through ``set_fact: redis_package={{ __redis_package }} when:
    redis_package is not defined``, so :data:`PACKAGE_DEFAULT` is its model
    default and an explicit key wins over it exactly as the extra variable won
    over the ``set_fact`` (D2).

    The model is frozen and hashable, so every sequence is a ``tuple`` (never a
    ``list``) and a value that reaches :func:`~reemote.core.op` hashes (design
    doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``redis_package``: the apt package name (Debian default carried over).
    package: str = PACKAGE_DEFAULT
    #: ``redis_enabled``: whether the unit starts on boot.
    enabled: bool = True
    #: ``redis_port``: the listen port (``port``).
    port: int = 6379
    #: ``redis_bind_interface``: the listen address (``bind``).
    bind_interface: str = "127.0.0.1"
    #: ``redis_unixsocket``: an empty string renders no ``unixsocket`` line,
    #: exactly as the template's ``{% if %}`` guard does.
    unixsocket: str = ""
    #: ``redis_timeout``: idle-client disconnect seconds (``timeout``).
    timeout: int = 300
    #: ``redis_loglevel``: one of the four levels the README enumerates.
    loglevel: Literal["debug", "verbose", "notice", "warning"] = "notice"
    #: ``redis_logfile``: the daemon log file (``logfile``).
    logfile: str = "/var/log/redis/redis-server.log"
    #: ``redis_databases``: the database count (``databases``).
    databases: int = 16
    #: ``redis_save``: snapshot rules, one ``<seconds> <writes>`` string each
    #: (``save`` lines); an empty tuple disables persistence.
    save: tuple[str, ...] = ("900 1", "300 10", "60 10000")
    #: ``redis_rdbcompression``: the ``rdbcompression`` yes/no flag.
    rdbcompression: Literal["yes", "no"] = "yes"
    #: ``redis_dbfilename``: the RDB basename (``dbfilename``).
    dbfilename: str = "dump.rdb"
    #: ``redis_dbdir``: the data directory (``dir``).
    dbdir: str = "/var/lib/redis"
    #: ``redis_maxmemory``: the byte cap; ``0`` renders no maxmemory block,
    #: as the template's ``{% if %}`` falsiness gate does.
    maxmemory: int = 0
    #: ``redis_maxmemory_policy``: the eviction policy (``maxmemory-policy``).
    maxmemory_policy: str = "noeviction"
    #: ``redis_maxmemory_samples``: the LRU sample count (``maxmemory-samples``).
    maxmemory_samples: int = 5
    #: ``redis_appendonly``: the AOF flag (``appendonly``).
    appendonly: Literal["yes", "no"] = "no"
    #: ``redis_appendfsync``: the AOF fsync mode (``appendfsync``).
    appendfsync: Literal["always", "everysec", "no"] = "everysec"
    #: ``redis_includes``: extra ``include`` lines.
    includes: tuple[str, ...] = ()
    #: ``redis_requirepass``: the auth password; empty renders no line.
    requirepass: str = ""
    #: ``redis_disabled_commands``: each renders one ``rename-command <cmd> ""``.
    disabled_commands: tuple[str, ...] = ()
    #: ``redis_extra_config``: raw lines appended to the end of the config.
    extra_config: str = ""
    #: RHEL-only, out of scope: kept so an existing inventory binds and noops.
    enablerepo: str = "epel"

    @field_validator("package", "bind_interface", "logfile", "dbfilename", "dbdir")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty package/interface/log/dir is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value


@dataclass(frozen=True)
class RedisPlan:
    """One host's resolved configure target, built by the body.

    The role's resolved artifacts are the rendered configuration file plus the
    two variables the package and service requests read; the paths and modes
    are frozen constants. It replaces a per-host plan dict: under a per-host
    body the plan is a local, rendered after ``hv`` resolves and before the
    request wave is built.
    """

    config_content: str
    package: str
    enabled: bool


# --- pure helpers ------------------------------------------------------------


def render_redis_conf(hv: RedisVars) -> str:
    """``templates/redis.conf.j2``, pre-rendered control-side (recipe 4).

    The jinja2 import is inside this function (through
    :func:`reemote.roles._render.jinja2_environment`) so that importing
    ``reemote.roles`` stays clean without the ``reemote[templates]`` extra
    (§17.4). The environment reproduces Ansible's whitespace handling
    byte-for-byte (``trim_blocks=True``, ``lstrip_blocks=False``,
    ``keep_trailing_newline=True``, ``StrictUndefined``); the context uses the
    role's original ``redis_*`` names because the bundled template does.
    ``ansible_managed`` is the frozen literal (D1) and ``redis_daemon`` is
    :data:`SERVICE` -- a frozen Debian constant, not a variable (D2).
    """
    from ._render import jinja2_environment

    environment = jinja2_environment()
    template = environment.from_string(REDIS_CONF_TEMPLATE)
    return template.render(
        redis_daemon=SERVICE,
        redis_port=hv.port,
        redis_bind_interface=hv.bind_interface,
        redis_unixsocket=hv.unixsocket,
        redis_timeout=hv.timeout,
        redis_loglevel=hv.loglevel,
        redis_logfile=hv.logfile,
        redis_databases=hv.databases,
        redis_save=hv.save,
        redis_rdbcompression=hv.rdbcompression,
        redis_dbfilename=hv.dbfilename,
        redis_dbdir=hv.dbdir,
        redis_maxmemory=hv.maxmemory,
        redis_maxmemory_policy=hv.maxmemory_policy,
        redis_maxmemory_samples=hv.maxmemory_samples,
        redis_appendonly=hv.appendonly,
        redis_appendfsync=hv.appendfsync,
        redis_includes=hv.includes,
        redis_requirepass=hv.requirepass,
        redis_disabled_commands=hv.disabled_commands,
        redis_extra_config=hv.extra_config,
    )


def redis_plan(hv: RedisVars) -> RedisPlan:
    """Freeze the reads the deploy needs: the render, the package, the flag.

    Control-side work inside the body, before the request wave is built: the
    Jinja2 template becomes a plain ``str`` (hashable, identical across waves).
    The destination and both modes are constants, not plan fields.
    """
    return RedisPlan(
        config_content=render_redis_conf(hv),
        package=hv.package,
        enabled=hv.enabled,
    )


# --- the deploy stages --------------------------------------------------------


def configure(plan: RedisPlan) -> Deploy:
    """``tasks/configure.yml`` then ``tasks/setup-Debian.yml``, Ubuntu order.

    Branch-free: the plan is entirely static, so this stage is one pure request
    wave, minus the per-request ``when=`` guards the old two-``run()`` shape
    needed (the family guard covers them, rule 7). The order is the role's own
    Debian ordering -- the config lands **before** the package, because the
    package install starts Redis and it must start against the managed config.
    The notifying requests carry the stable names :data:`NOTIFY_RESTART`
    enumerates; the directory, package and service tasks carry no ``notify`` in
    the role, so their names are deliberately absent from the set.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # tasks/configure.yml: file state=directory mode=02770. The redis:redis
        # ownership is the packaging's own end state (D6). files.directory
        # reads the Directory fact, so a converged 02770 dir yields no command
        # and -- carrying no notify in the role -- notifies nobody.
        yield op(
            files.directory,
            name="Ensure Redis configuration directory",
            path=CONF_DIR,
            user=CONF_USER,
            group=CONF_GROUP,
            mode=CONF_DIR_MODE,
            sudo=True,
        )
        # The template task (dest=/etc/redis/redis.conf mode=0640), pre-rendered
        # control-side (recipe 4). Owned redis:redis so the User=redis daemon can
        # read it (D6). files.install's idempotence is content-only: a converged
        # file yields no commands even if its metadata drifted.
        yield op(
            files.install,
            name="Configure Redis",
            content=plan.config_content,
            dest=CONF_PATH,
            user=CONF_USER,
            group=CONF_GROUP,
            mode=CONF_MODE,
            sudo=True,
        )
        # The paired files.file converges the redis:redis 0640 metadata the
        # template task enforces on every run (D3, D6). It notifies too, because
        # Ansible's template reports a change on owner/group/mode drift as well.
        yield op(
            files.file,
            name="Set Redis configuration attributes",
            path=CONF_PATH,
            user=CONF_USER,
            group=CONF_GROUP,
            mode=CONF_MODE,
            sudo=True,
        )
        # tasks/setup-Debian.yml: apt name=redis-server state=present. The
        # Debian path carries no update_cache, so no apt.update request exists
        # here -- unlike the memcached role's.
        yield op(
            apt.packages,
            name="Install Redis",
            packages=(plan.package,),
            sudo=True,
        )
        # The final service task: state=started enabled={{ redis_enabled }}.
        # Both flags are fact-gated (SystemdServiceState / SystemdServiceEnabled),
        # so a running, (correctly) enabled unit yields no commands. It carries
        # no notify in the role -- its name is deliberately not in NOTIFY_RESTART.
        yield op(
            systemd.service,
            name="Ensure Redis is started and enabled",
            service=SERVICE,
            running=True,
            enabled=plan.enabled,
            sudo=True,
        )

    return deploy


def handler(notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart redis`` (the role's one handler).

    ``restart_handler`` closes over the notification set the checkpoint read
    produced and branches on ``host.name`` itself, so an unnotified host yields
    nothing and an empty set is the converged no-op. ``restarted=True`` restarts
    a running unit and starts a stopped one -- Ansible's ``state: restarted``,
    which on the first pass (right after the install the service task already
    started) reads as a start -- and the request keeps the factory's default
    name ``Restart redis-server (handler)``.
    """
    return restart_handler(notified, SERVICE)


# --- the role ----------------------------------------------------------------


def redis(v: VarsSet[RedisVars]) -> Deploy:
    """Install and configure Redis on Ubuntu hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`RedisVars` per host
            (``resolve_vars(RedisVars, "redis", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ImportError: The ``reemote[templates]`` extra is missing. The render
            happens inside the body, while the first request wave is built, so
            the failure arrives at the moment the consumer needs the extra.
            Otherwise the role has no in-body error path -- no barrier to
            misread, no version to gate on -- and bad *variables* are rejected
            earlier, by :func:`~reemote.rolevars.resolve_vars`.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the whole Debian task list -- static plan, no fact barriers --
        # closed by the role's only barrier. The checkpoint must immediately
        # follow the stage it reports on; the handler wave's entries would be
        # invisible to a second checkpoint, and nothing here needs one.
        yield from configure(redis_plan(hv))(host)
        wave_report = yield checkpoint("configure done")

        # Wave 1: the handler, only on the hosts this checkpoint selected.
        notified = changed_hosts(wave_report, NOTIFY_RESTART)
        yield from handler(notified)(host)

    return deploy


__all__ = [
    "RedisVars",
    "redis",
]
