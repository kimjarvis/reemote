# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Memcached role (migrated from ``geerlingguy.memcached``).
#
# Installs Memcached on Ubuntu hosts, writes ``/etc/memcached.conf`` from the
# role's Debian Jinja2 template (root:root, mode ``0644``), and starts and
# enables the service; a config change queues the role's one handler,
# ``restart memcached``. :func:`memcached` is the whole task list and the
# handler, :class:`MemcachedVars` is ``defaults/main.yml`` (plus the
# ``memcached_user`` default the ``set_fact`` task derives from
# ``vars/Debian.yml``), bound from the inventory entry's ``memcached`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all -- the Alpine and RedHat hosts in a shared inventory are
# never contacted. The role's RedHat branch (``vars/RedHat.yml``,
# ``memcached-RedHat.conf.j2``, the sysconfig destination) is out of scope,
# and because the vars split only by OS family -- not by release -- the
# single-OS scope collapses the ``include_vars`` dispatch to frozen module
# constants: no ``LinuxDistribution`` barrier exists (the nginx precedent, and
# unlike java).
#
# The stream is two waves and one checkpoint (design doc section 18.13):
#
# 0. :func:`configure` -- the cache refresh, the ``memcached`` package, the
#    rendered config install and its metadata companion (both notify), and the
#    started-and-enabled service -- closed by ``checkpoint("configure done")``.
# 1. :func:`handler` -- ``restart memcached``, only on the hosts that
#    checkpoint selected. An empty notification set yields no request, the
#    converged no-op Ansible's handler gives for free.
#
# **No facts at all.** Every input is a resolved variable or a frozen
# constant, so the checkpoint is the only barrier in the whole role; a
# ``fact`` or ``act`` barrier here would be speculatively added. The old
# example's two-``run()`` split and its per-host ``plans`` dict collapse into
# the in-stream checkpoint read: the notification is a wave outcome the body
# consumes itself.
#
# Prerequisites: the config render uses Jinja2 (the template's ``is string``
# test and the ``-l`` for-loop), imported only through
# :mod:`reemote.roles._render` inside :func:`render_memcached_conf`, so
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
# * the package task's ``register: memcached_install`` is dropped (D2): no
#   later task or ``when:`` reads it, and reemote reads outcomes at the
#   checkpoint.
# * a ``files.file`` metadata companion is added, and its name notifies (D3):
#   ``files.install``'s idempotence is content-only, so a root:root ``0644``
#   drift on unchanged content would never converge, while Ansible's template
#   module reports a change on owner/group/mode drift too (the java/apache
#   companion precedent).
# * the RedHat path is scoped out (D4): a Phase 0 single-OS decision.
# * type gates move to :class:`MemcachedVars` (D5): a wrongly typed variable
#   is a pydantic rejection at ``resolve_vars`` time, before any host is
#   contacted, not a render-time surprise. Pydantic's lax mode still coerces a
#   numeric string (``"11211"``) into an :class:`int` field, which the old
#   example's ``isinstance`` gate refused -- the repo-wide convention (apache's
#   :attr:`~reemote.roles.apache.ApacheVars.listen_port` behaves the same),
#   and the render is identical either way.
# """

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import Host
from ..modules import apt, files, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path) --------

#: ``vars/Debian.yml``'s ``memcached_config_file``, frozen: the Ubuntu path
#: never overrides the destination, so it is a constant, not a variable.
CONFIG_FILE = "/etc/memcached.conf"

#: ``vars/Debian.yml``'s ``__memcached_user``: the private default the
#: ``set_fact`` task copies into ``memcached_user`` when the variable is not
#: defined -- it becomes :attr:`MemcachedVars.user`'s default.
DEFAULT_USER = "memcache"

#: The role's apt task: ``update_cache=yes cache_valid_time=86400``. The
#: refresh is gated on the ``AptUpdateStamp`` fact against the controller's
#: UTC clock, so a converged host with a cache fresher than 24 hours yields
#: no update command.
CACHE_VALID_TIME = 86400

#: ``templates/memcached-Debian.conf.j2``, verbatim except the frozen
#: ``ansible_managed`` header (D1). Rendered control-side with Ansible's
#: whitespace defaults by :func:`render_memcached_conf`.
MEMCACHED_CONF_TEMPLATE = """\
# Ansible managed
# memcached default config file
# 2003 - Jay Bonci <jaybonci@debian.org>
# This configuration file is read by the start-memcached script provided as
# part of the Debian GNU/Linux distribution.

# Run memcached as a daemon. This command is implied, and is not needed for the
# daemon to run. See the README.Debian that comes with this package for more
# information.
-d

# Threads count
-t {{ memcached_threads }}

# Log memcached's output to /var/log/memcached
logfile {{ memcached_log_file }}
{{ memcached_log_verbosity }}

# Start with a cap of 64 megs of memory. It's reasonable, and the daemon default
# Note that the daemon will grow to this size, but does not start out holding this much
# memory
-m {{ memcached_memory_limit }}

# Default connection port is 11211
-p {{ memcached_port }}

# Run the daemon as root. The start-memcached will default to running as root if no
# -u command is present in this config file
-u {{ memcached_user }}

# Specify which IP address to listen on. The default is to listen on all IP addresses
# This parameter is one of the only security measures that memcached has, so make sure
# it's listening on a firewalled interface.
{% if memcached_listen_ip is string %}
-l {{ memcached_listen_ip }}
{% else %}
{% for ip in memcached_listen_ip %}
-l {{ ip }}
{% endfor %}
{% endif %}

# Limit the number of simultaneous incoming connections. The daemon default is 1024
-c {{ memcached_connections }}

# Lock down all paged memory. Consult with the README and homepage before you do this
# -k

# Return error when memory is exhausted (rather than removing items)
# -M

# Maximize core file limit
# -r

-I {{ memcached_max_item_size }}
"""

#: The systemd unit the service task and the ``restart memcached`` handler act on.
SERVICE = "memcached"

#: The request names that queue ``restart memcached`` -- the role's one
#: ``notify:`` channel. The template task is "Copy Memcached configuration.";
#: the metadata companion joins it because Ansible's template module reports a
#: change on owner/group/mode drift as well (D3).
NOTIFY_RESTART: frozenset[str] = frozenset(
    {
        "Copy Memcached configuration",
        "Set Memcached configuration attributes",
    }
)


# --- variables ---------------------------------------------------------------


class MemcachedVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``memcached`` role mapping, so ``port`` there sets
    :attr:`port`. :attr:`user` has no ``defaults/main.yml`` entry at all; the
    role defines it through ``set_fact: memcached_user={{ __memcached_user }}
    when: memcached_user is not defined``, so :data:`DEFAULT_USER` is its
    model default and an explicit key wins over it exactly as the extra key
    won over the ``set_fact``.

    The model is frozen and hashable, so every sequence is a ``tuple`` (never a
    ``list``) and a value that reaches :func:`~reemote.core.op` hashes (design
    doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``memcached_user``: the user the daemon runs as (``-u``).
    user: str = DEFAULT_USER
    #: ``memcached_port``: the listen port (``-p``).
    port: int = 11211
    #: ``memcached_listen_ip``: a string renders one ``-l`` line; a sequence
    #: renders one per address (the template's ``is string`` branch); an empty
    #: sequence renders none -- memcached then listens on every interface.
    listen_ip: str | tuple[str, ...] = "127.0.0.1"
    #: ``memcached_memory_limit``: the memory cap in MB (``-m``).
    memory_limit: int = 64
    #: ``memcached_connections``: the simultaneous connection cap (``-c``).
    connections: int = 1024
    #: ``memcached_log_file``: the daemon log file (``logfile``).
    log_file: str = "/var/log/memcached.log"
    #: ``memcached_log_verbosity``: ``-v``/``-vv`` enable logging; the empty
    #: default renders a blank line, exactly as the template does.
    log_verbosity: str = ""
    #: ``memcached_max_item_size``: the per-item memory limit (``-I``).
    max_item_size: str = "1m"
    #: ``memcached_threads``: the thread count (``-t``).
    threads: int = 4

    @field_validator("user", "log_file", "max_item_size")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty user/log-file/item-size is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("listen_ip", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; anything else falls through to the union."""
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("listen_ip")
    @classmethod
    def _addresses_are_strings(cls, value: str | tuple[str, ...]) -> Any:
        """Every sequence entry must be a non-empty address string."""
        if isinstance(value, str):
            return value
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("must be a string or non-empty string addresses")
        return value


@dataclass(frozen=True)
class MemcachedPlan:
    """One host's resolved configure target, built by the body.

    The role's only resolved artifact is the rendered configuration file --
    the package, the service and the destination are frozen constants. It
    replaces the example's ``plans`` dict keyed by host name: under a per-host
    body the plan is a local, rendered after ``hv`` resolves and before the
    request wave is built.
    """

    config_content: str


# --- pure helpers ------------------------------------------------------------


def render_memcached_conf(hv: MemcachedVars) -> str:
    """``templates/memcached-Debian.conf.j2``, pre-rendered control-side (recipe 4).

    The jinja2 import is inside this function (through
    :func:`reemote.roles._render.jinja2_environment`) so that importing
    ``reemote.roles`` stays clean without the ``reemote[templates]`` extra
    (§17.4). The environment reproduces Ansible's whitespace handling
    byte-for-byte (``trim_blocks=True``, ``lstrip_blocks=False``,
    ``keep_trailing_newline=True``, ``StrictUndefined``); the context uses the
    role's original ``memcached_*`` names because the bundled template does.
    ``ansible_managed`` is the frozen literal (D1).
    """
    from ._render import jinja2_environment

    environment = jinja2_environment()
    template = environment.from_string(MEMCACHED_CONF_TEMPLATE)
    return template.render(
        memcached_user=hv.user,
        memcached_port=hv.port,
        memcached_listen_ip=hv.listen_ip,
        memcached_memory_limit=hv.memory_limit,
        memcached_connections=hv.connections,
        memcached_log_file=hv.log_file,
        memcached_log_verbosity=hv.log_verbosity,
        memcached_max_item_size=hv.max_item_size,
        memcached_threads=hv.threads,
    )


def memcached_plan(hv: MemcachedVars) -> MemcachedPlan:
    """Freeze the one read the deploy needs: the rendered config content.

    Control-side work inside the body, before the request wave is built: the
    Jinja2 template becomes a plain ``str`` (hashable, identical across waves).
    The destination is :data:`CONFIG_FILE`, not a plan field.
    """
    return MemcachedPlan(config_content=render_memcached_conf(hv))


# --- the deploy stages --------------------------------------------------------


def configure(plan: MemcachedPlan) -> Deploy:
    """``tasks/main.yml``, Ubuntu path: cache refresh, package, config, service.

    Branch-free: the plan is entirely static, so this stage is what the old
    two-deploy example's ``build_memcached`` deploy was, minus the per-request
    ``when=is_ubuntu`` (the family guard covers it, rule 7). The notifying
    requests carry the stable names :data:`NOTIFY_RESTART` enumerates; the
    package and service tasks carry no ``notify`` in the role, so their names
    are deliberately absent from the set.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # The role's first task: apt update_cache=yes cache_valid_time=86400,
        # gated on the AptUpdateStamp fact (a converged fresh cache is quiet).
        yield op(
            apt.update,
            name="Update apt cache",
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
        )
        # The package task; its dead ``register: memcached_install`` is
        # dropped (D2).
        yield op(
            apt.packages,
            name="Install Memcached",
            packages=(SERVICE,),
            sudo=True,
        )
        # The template task, pre-rendered control-side (recipe 4).
        # files.install's idempotence is content-only: a converged file
        # yields no commands even if its mode or ownership drifted.
        yield op(
            files.install,
            name="Copy Memcached configuration",
            content=plan.config_content,
            dest=CONFIG_FILE,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        # The paired files.file converges the root:root 0644 metadata the
        # template task enforces on every run (D3). It notifies too, because
        # Ansible's template reports a change on metadata drift as well.
        yield op(
            files.file,
            name="Set Memcached configuration attributes",
            path=CONFIG_FILE,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        # The final service task: state=started enabled=yes. Both flags are
        # fact-gated (SystemdServiceState / SystemdServiceEnabled), so a
        # running, enabled unit yields no commands. It carries no notify in
        # the role -- its name is deliberately not in NOTIFY_RESTART.
        yield op(
            systemd.service,
            name="Ensure Memcached is started and enabled",
            service=SERVICE,
            running=True,
            enabled=True,
            sudo=True,
        )

    return deploy


def handler(notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``restart memcached`` (the role's one handler).

    ``restart_handler`` closes over the notification set the checkpoint read
    produced and branches on ``host.name`` itself, so an unnotified host
    yields nothing and an empty set is the converged no-op. ``restarted=True``
    restarts a running unit and starts a stopped one -- Ansible's
    ``state: restarted`` -- and the request keeps the factory's default name
    ``Restart memcached (handler)``.
    """
    return restart_handler(notified, SERVICE)


# --- the role ----------------------------------------------------------------


def memcached(v: VarsSet[MemcachedVars]) -> Deploy:
    """Install and configure Memcached on Ubuntu hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`MemcachedVars` per host
            (``resolve_vars(MemcachedVars, "memcached", inventory)``).

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

        # Wave 0: the whole task list -- static plan, no fact barriers --
        # closed by the role's only barrier. The checkpoint must immediately
        # follow the stage it reports on; the handler wave's entries would be
        # invisible to a second checkpoint, and nothing here needs one.
        yield from configure(memcached_plan(hv))(host)
        wave_report = yield checkpoint("configure done")

        # Wave 1: the handler, only on the hosts this checkpoint selected.
        notified = changed_hosts(wave_report, NOTIFY_RESTART)
        yield from handler(notified)(host)

    return deploy


__all__ = [
    "MemcachedVars",
    "memcached",
]
