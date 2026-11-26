# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The NTP role (migrated from ``geerlingguy.ntp``).
#
# Installs an NTP daemon on Ubuntu hosts, sets the timezone, disables the competing
# ``systemd-timesyncd`` unit when it survives the install, brings the daemon up (or
# down) per ``enabled`` and optionally templates its configuration file.
# :func:`ntp` is the whole task list and :class:`NtpVars` is ``defaults/main.yml``,
# bound from the inventory entry's ``ntp`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family returns
# before its first ``yield`` and emits no requests, no barriers and no log lines at
# all -- so the Alpine and CentOS hosts in a shared inventory are never contacted.
# The role's RedHat, Suse, Archlinux and FreeBSD branches are out of scope, which
# narrows the ``include_vars`` dispatch to the Debian family but does not collapse
# it to a constant: the vars table still splits by gathered release major (``ntp``
# on the legacy Debian-11 line vs ``ntpsec`` on the modern one), so the distribution
# read stays a barrier.
#
# The stream is five waves -- two ``fact`` barriers, two checkpoints and the request
# waves they close (design doc section 18.10):
#
# 0. ``fact(LinuxDistribution)`` -- the release the ``with_first_found`` vars chain
#    dispatches on. It selects the frozen vars table (the daemon, its package, the
#    config/drift paths, tzdata and the cron daemon), so the install wave is a pure
#    function of it.
# 1. :func:`install` -- the NTP package, tzdata and the timezone task (which queues
#    ``restart cron``) -- closed by ``checkpoint("install done")``. The cron
#    notification set is read from this report.
# 2. ``fact(SystemdUnitFiles)`` -- the role's ``service_facts`` probe, which runs
#    *after* the install because installing ntpsec can remove systemd-timesyncd
#    (both conflict through the virtual package ``time-daemon``). The unit's
#    presence is the gate for the timesyncd task; the branch stays inside the body.
# 3. :func:`configure` -- timesyncd stop/disable, the daemon's running/enabled
#    state and the config template (which queues ``restart ntp``) -- closed by
#    ``checkpoint("configure done")``. The ntp notification set is read from *this*
#    report.
# 4. :func:`handlers` -- ``restart ntp`` then ``restart cron`` in handler DEFINITION
#    order, each gated on its own role flag and its own notification set.
#
# **Two checkpoints, not one.** ``cron_notified`` reads the *install* report and
# ``ntp_notified`` reads the *configure* report -- two different reports, so unlike
# nginx's two channels (both read the configure checkpoint) they cannot share one
# checkpoint. The install checkpoint is taken and its result held while the body
# continues; both handler restarts are emitted after the configure checkpoint.
#
# The three reads the example took *between* its three ``run()`` calls -- the
# distribution gather, the post-install unit-files gather and the two
# ``changed_hosts`` reads -- are barriers now, so nothing is frozen in a module-level
# plan dict and no name set is unioned across the inventory: the per-host body *is*
# the plan. The old example's ``plans`` dict and its hand-built ``build_handlers``
# partition (needed only because one ``compose`` served every host) both disappear.
#
# Deviations from the Ansible role, all deliberate:
#
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: an off-target host returns before it yields anything, so it emits no
#   requests and no ``[skipped]`` log lines (the old example logged each).
# * the molecule converge playbook's apt index refresh (``cache_valid_time: 600``)
#   is folded into the NTP install request so the role is self-contained on a fresh
#   host. The refresh is gated on the ``AptUpdateStamp`` fact, so a converged host
#   with a fresh cache yields no update command.
# * the role's ``ignore_errors: ansible_check_mode`` on the daemon task is dropped:
#   reemote's ``dry`` never applies commands, so there is no check-mode tolerance to
#   reproduce, and a real failure raises ``CommandError``.
# * the ``service_facts`` ``status != "not-found"`` gate becomes ``SystemdUnitFiles``
#   membership read after the install -- a unit absent from ``list-unit-files`` is
#   exactly ``not-found``.
# * ``ansible_managed`` is frozen to its default output ``# Ansible managed``: a
#   timestamped header would rewrite every config on every run and break both
#   idempotence and the ``restart ntp`` notification.
# * the multi-OS ``include_vars`` dispatch is narrowed to the Debian family with a
#   fail-fast raise: a host whose gathered ``LinuxDistribution.name`` is not
#   ``Ubuntu`` raises ``ValueError`` (the inventory said ``Ubuntu``), following the
#   java precedent. The role's dead ``clock.j2`` template is not migrated.
# * the chrony renderer is reachable only through an explicit ``config_file``
#   override to a ``chrony.conf`` path; the Ubuntu default selects ``ntp.conf``.
# * ``ntp_enabled`` and ``ntp_cron_handler_enabled`` are carried as variables that
#   gate the two handler restarts, exactly as ``handlers/main.yml``'s ``when:`` does
#   -- so a stopped daemon whose config changed is notified but not restarted.
# """

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts
from ..hosts import Host
from ..modules import apt, files, server, systemd
from ..modules.server import LinuxDistribution
from ..modules.systemd import SystemdUnitFiles
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

# --- role constants (defaults/main.yml + vars/*.yml, Ubuntu path) -------------

#: The molecule converge playbook's pre_task refreshes the apt index with
#: ``cache_valid_time: 600`` before the role runs; the role itself does not.
#: Folded into the NTP install request so the role works on a fresh host (D2).
APT_CACHE_VALID_TIME = 600

#: The unit the role stops/disables when it is still present after the install and
#: ``enabled`` is true.
TIMESYNCD_UNIT = "systemd-timesyncd.service"

#: ``vars/Ubuntu-24.04.yml`` and ``vars/Debian.yml`` are byte-identical: the ntpsec
#: daemon, its config/drift paths, tzdata and the cron daemon.
NTPSEC_VARS: dict[str, str] = {
    "daemon": "ntpsec",
    "package": "ntpsec",
    "config_file": "/etc/ntpsec/ntp.conf",
    "driftfile": "/var/lib/ntpsec/ntp.drift",
    "tzdata_package": "tzdata",
    "cron_daemon": "cron",
}

#: ``vars/Debian-11.yml``: the legacy ntp daemon and paths (Debian 11 / an ancient
#: Ubuntu selects this through the family-major chain level).
DEBIAN_11_VARS: dict[str, str] = {
    "daemon": "ntp",
    "package": "ntp",
    "config_file": "/etc/ntp.conf",
    "driftfile": "/var/lib/ntp/drift",
    "tzdata_package": "tzdata",
    "cron_daemon": "cron",
}

#: The role's ``with_first_found`` chain, keyed by vars-file stem; the first present
#: candidate wins. A chain that hits nothing raises, exactly as the role's
#: ``include_vars`` (no ``skip: true``) fails with "file not found".
VARS_FILES: dict[str, dict[str, str]] = {
    "Ubuntu-24.04": NTPSEC_VARS,
    "Debian-11": DEBIAN_11_VARS,
    "Debian": NTPSEC_VARS,
}

#: The notifying request names -- the ``notify:`` channel. The timezone task queues
#: ``restart cron``; the config template queues ``restart ntp``.
NOTIFY_CRON: frozenset[str] = frozenset({"Set timezone."})
NOTIFY_NTP: frozenset[str] = frozenset({"Generate ntp configuration file."})

#: ``templates/ntp.conf.j2``, verbatim except the frozen managed header (D5).
#: Rendered with Ansible's whitespace defaults by :func:`_render`.
NTP_CONF_TEMPLATE = """# Ansible managed

# For more information about this file, see the man pages
# ntp.conf(5), ntp_acc(5), ntp_auth(5), ntp_clock(5), ntp_misc(5), ntp_mon(5).

driftfile {{ ntp_driftfile }}

# Enable this if you want statistics to be logged.
#statsdir /var/log/ntpstats/

statistics loopstats peerstats clockstats
filegen loopstats file loopstats type day enable
filegen peerstats file peerstats type day enable
filegen clockstats file clockstats type day enable

{% if ntp_tinker_panic is sameas true %}
# Always reset the clock, even if the new time is more than 1000s away
# from the current system time. Usefull for VMs that can be paused
# and much later resumed.
tinker panic 0
{% endif %}

# Use public servers from the pool.ntp.org project.
# Please consider joining the pool (http://www.pool.ntp.org/join.html).
{% for item in ntp_servers %}
pool {{ item }}
{% endfor %}

# Permit time synchronization with our time source, but do not
# permit the source to query or modify the service on this system.
restrict default nomodify notrap nopeer noquery

# Allow pool associations
restrict source nomodify notrap noquery

# Permit all access over the loopback interface.  This could
# be tightened as well, but to do so would effect some of
# the administrative functions.
{% for item in ntp_restrict %}
restrict {{ item }}
{% endfor %}

# Clients from this (example!) subnet have unlimited access, but only if
# cryptographically authenticated.
#restrict 192.168.123.0 mask 255.255.255.0 notrust

# Enable public key cryptography.
#crypto

#includefile /etc/ntp/crypto/pw

# Key file containing the keys and key identifiers used when operating
# with symmetric key cryptography.
#keys /etc/ntp/keys

# Specify the key identifiers which are trusted.
#trustedkey 4 8 42

# Specify the key identifier to use with the ntpdc utility.
#requestkey 8

# Specify the key identifier to use with the ntpq utility.
#controlkey 8

# Enable writing of statistics records.
#statistics clockstats cryptostats loopstats peerstats

# Disable the monitoring facility to prevent amplification attacks using ntpdc
# monlist command when default restrict does not include the noquery flag. See
# CVE-2013-5211 for more details.
# Note: Monitoring will not be disabled with the limited restriction flag.
disable monitor

# If you want to provide time to your local subnet, change the next line.
# (Again, the address is an example only.)
#broadcast 192.168.123.255

# If you want to listen to time broadcasts on your local subnet, de-comment the
# next lines. Please do this only if you trust everybody on the network!
#disable auth
#broadcastclient
"""

#: ``templates/chrony.conf.j2``, verbatim except the frozen managed header (D5).
#: Reachable only when ``config_file`` is overridden to a ``chrony.conf`` path.
CHRONY_CONF_TEMPLATE = """# Ansible managed

# These servers were defined in the installation:
{% for server in ntp_servers %}
server {{ server }}
{% endfor %}
# Use public servers from the pool.ntp.org project.
# Please consider joining the pool (http://www.pool.ntp.org/join.html).

# Record the rate at which the system clock gains/losses time.
driftfile /var/lib/chrony/drift

# Allow the system clock to be stepped in the first three updates
# if its offset is larger than 1 second.
makestep 1.0 3

# Enable kernel synchronization of the real-time clock (RTC).
rtcsync

# Enable hardware timestamping on all interfaces that support it.
#hwtimestamp *

# Increase the minimum number of selectable sources required to adjust
# the system clock.
#minsources 2

# Allow NTP client access from local network.
#allow 192.168.0.0/16
{% for item in ntp_restrict %}
allow {{ item }}
{% endfor %}

# Serve time even if not synchronized to a time source.
#local stratum 10

# Specify file containing keys for NTP authentication.
#keyfile /etc/chrony.keys

# Specify directory for log files.
logdir /var/log/chrony

# Select which information is logged.
#log measurements statistics tracking

# sets the maximum root distance of a source to be acceptable for synchronisation of the clock.
maxdistance {{ ntp_chrony_maxdistance }}
"""


# --- variables ---------------------------------------------------------------


class NtpVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``ntp`` role mapping, so ``timezone`` there sets
    :attr:`timezone`. The four ``set_fact`` overrides (:attr:`daemon`,
    :attr:`package`, :attr:`config_file`,
    :attr:`driftfile`) default to ``None`` -- "derive from the gathered vars table",
    exactly as the role's ``when: ... is not defined`` guards.

    The model is frozen and hashable, so every sequence is a ``tuple`` (never a
    ``list``) and a value that reaches :func:`~reemote.core.op` hashes (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``ntp_enabled``: start/enable the daemon (true) or stop/disable it (false).
    #: Also gates the ``restart ntp`` handler, as ``handlers/main.yml``'s ``when:``.
    enabled: bool = True
    #: ``ntp_timezone``: the zone ``server.timezone`` sets (queues ``restart cron``).
    timezone: str = "Etc/UTC"
    #: ``ntp_manage_config``: when true, the config template is rendered and written.
    manage_config: bool = False
    #: ``ntp_area``: the pool sub-domain interpolated into the default servers.
    area: str = ""
    #: ``ntp_servers``: ``None`` derives the four pool strings from :attr:`area`.
    servers: tuple[str, ...] | None = None
    #: ``ntp_restrict``: the loopback ``restrict`` lines for ntp.conf / ``allow`` for
    #: chrony.conf.
    restrict: tuple[str, ...] = ("127.0.0.1", "::1")
    #: ``ntp_cron_handler_enabled``: gates the ``restart cron`` handler.
    cron_handler_enabled: bool = False
    #: ``ntp_tinker_panic``: the ``is sameas true`` branch in ntp.conf.j2.
    tinker_panic: bool = False
    #: ``ntp_chrony_maxdistance``: the chrony.conf ``maxdistance`` value.
    chrony_maxdistance: str = "3"
    #: ``ntp_daemon``: ``None`` derives from the vars table; else the unit name.
    daemon: str | None = None
    #: ``ntp_package``: ``None`` derives from the vars table; else the package name.
    package: str | None = None
    #: ``ntp_config_file``: ``None`` derives from the vars table; its basename selects
    #: the ntp.conf vs chrony.conf renderer.
    config_file: str | None = None
    #: ``ntp_driftfile``: ``None`` derives from the vars table.
    driftfile: str | None = None

    @field_validator("timezone", "chrony_maxdistance")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty timezone/maxdistance is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("servers", "restrict", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through for pydantic to reject."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("servers", "restrict")
    @classmethod
    def _entries_non_empty(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Every entry must be a non-empty string."""
        if value is None:
            return value
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("must hold non-empty strings")
        return value

    @field_validator("daemon", "package", "config_file", "driftfile")
    @classmethod
    def _override_non_empty(cls, value: str | None) -> str | None:
        """A set_fact override, when given, must be a non-empty string."""
        if value is not None and not value:
            raise ValueError("must be null or a non-empty string")
        return value


@dataclass(frozen=True)
class NtpPlan:
    """One host's resolved install/configure targets, built by :func:`ntp_plan`.

    Every value the install and configure stages need except the timesyncd presence
    is decided from the resolved variables and the gathered distribution -- the vars
    table, the set_fact overrides, the derived pool servers and the rendered config.
    It replaces the example's ``plans`` dict keyed by host name: under a per-host
    body, the plan is a local. ``timesyncd`` is decided later, after the post-install
    :class:`~reemote.modules.systemd.SystemdUnitFiles` barrier, so it is a
    :func:`configure` argument rather than a field.
    """

    packages: tuple[str, ...]
    tzdata_packages: tuple[str, ...]
    timezone: str
    daemon: str
    cron_daemon: str
    config_file: str
    enabled: bool
    manage_config: bool
    cron_handler_enabled: bool
    config_content: str | None


# --- pure helpers ------------------------------------------------------------


def default_servers(area: str) -> tuple[str, ...]:
    """The role's default ``ntp_servers`` with ``ntp_area`` interpolated.

    ``defaults/main.yml`` spells each entry
    ``"0{{ '.' + ntp_area if ntp_area else '' }}.pool.ntp.org iburst"``; resolved
    control-side here (an empty area yields the worldwide pool).
    """
    zone = f".{area}" if area else ""
    return tuple(f"{i}{zone}.pool.ntp.org iburst" for i in range(4))


def select_vars_table(info: dict[str, Any]) -> dict[str, str]:
    """The ``include_vars`` ``with_first_found`` chain, over frozen tables.

    Three candidate stems in order: ``{distribution}-{distribution_version}``
    (``Ubuntu-24.04``), ``{os_family}-{distribution_major_version}`` (``Debian-24``),
    ``{os_family}`` (``Debian``); the first present in :data:`VARS_FILES` wins. The
    migration targets Ubuntu, so a host reporting another distribution is a hard
    error (the inventory's family said ``Ubuntu`` -- D6).

    Raises:
        ValueError: The gathered distribution is not ``Ubuntu``, its major version
            is unreadable, or no shipped vars file matches the chain. Raised inside
            the body, after the :class:`~reemote.modules.server.LinuxDistribution`
            barrier and before the install wave is built.
    """
    name = info.get("name")
    if name != "Ubuntu":
        raise ValueError(
            f"this role targets Ubuntu; the host reports {name!r} "
            "(LinuxDistribution.name) -- check the inventory family"
        )
    try:
        major = int(info["major"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "cannot read the Ubuntu major version from the distribution facts "
            f"(major={info.get('major')!r})"
        ) from exc
    family = info.get("family") or "Debian"
    meta = info.get("release_meta") or {}
    version = meta.get("VERSION_ID")
    if not version:
        minor = info.get("minor")
        version = f"{major}.{minor}" if minor is not None else str(major)

    candidates = (f"{name}-{version}", f"{family}-{major}", f"{family}")
    for stem in candidates:
        if stem in VARS_FILES:
            return VARS_FILES[stem]
    raise ValueError(
        f"the role ships no vars file for {name} {version}; tried "
        f"{', '.join(stem + '.yml' for stem in candidates)} -- the role's "
        "include_vars fails with 'file not found' the same way"
    )


def _render(template_text: str, **context: Any) -> str:
    """Render a bundled template with Ansible's Jinja2 environment.

    The jinja2 import is inside this function (through
    :func:`reemote.roles._render.jinja2_environment`) so that importing
    ``reemote.roles`` stays clean without the ``reemote[templates]`` extra (§17.4).
    The environment reproduces Ansible's whitespace handling byte-for-byte.
    """
    from ._render import jinja2_environment

    return jinja2_environment().from_string(template_text).render(**context)


def render_ntp_conf(
    driftfile: str,
    servers: tuple[str, ...],
    restrict: tuple[str, ...],
    tinker_panic: bool,
) -> str:
    """``templates/ntp.conf.j2``, pre-rendered control-side (recipe 4)."""
    return _render(
        NTP_CONF_TEMPLATE,
        ntp_driftfile=driftfile,
        ntp_servers=servers,
        ntp_restrict=restrict,
        ntp_tinker_panic=tinker_panic,
    )


def render_chrony_conf(
    servers: tuple[str, ...],
    restrict: tuple[str, ...],
    maxdistance: str,
) -> str:
    """``templates/chrony.conf.j2``, pre-rendered control-side (recipe 4)."""
    return _render(
        CHRONY_CONF_TEMPLATE,
        ntp_servers=servers,
        ntp_restrict=restrict,
        ntp_chrony_maxdistance=maxdistance,
    )


def render_config(
    config_file: str,
    driftfile: str,
    servers: tuple[str, ...],
    restrict: tuple[str, ...],
    tinker_panic: bool,
    maxdistance: str,
) -> str:
    """Dispatch on the config-file basename, as the role's template task does.

    ``src: "{{ ntp_config_file | basename }}.j2"`` selects ntp.conf.j2 for
    ``/etc/ntpsec/ntp.conf`` and chrony.conf.j2 for a ``chrony.conf`` path. An
    unknown basename raises -- Ansible fails with "template not found" the same way
    (the role ships no clock.j2 task; that template is dead).
    """
    basename = Path(config_file).name
    if basename == "ntp.conf":
        return render_ntp_conf(driftfile, servers, restrict, tinker_panic)
    if basename == "chrony.conf":
        return render_chrony_conf(servers, restrict, maxdistance)
    raise ValueError(
        f"no bundled template for {basename}.j2 (ntp_config_file={config_file!r}); "
        "the role ships ntp.conf.j2 and chrony.conf.j2 only"
    )


def ntp_plan(hv: NtpVars, info: dict[str, Any]) -> NtpPlan:
    """Freeze the install/configure targets from the resolved vars and distribution.

    The gathered distribution selects the vars table (the ``include_vars`` chain), the
    four set_fact overrides win when defined, the pool servers derive from ``area``
    when unset, and the config template becomes a plain ``str`` (rendered only when
    ``manage_config`` is true, so an unknown basename does not raise on the default
    path -- the role's template task is skipped there too).
    """
    table = select_vars_table(info)

    daemon = hv.daemon if hv.daemon is not None else table["daemon"]
    package = hv.package if hv.package is not None else table["package"]
    config_file = hv.config_file if hv.config_file is not None else table["config_file"]
    driftfile = hv.driftfile if hv.driftfile is not None else table["driftfile"]
    servers = hv.servers if hv.servers is not None else default_servers(hv.area)

    config_content = None
    if hv.manage_config:
        config_content = render_config(
            config_file,
            driftfile,
            servers,
            hv.restrict,
            hv.tinker_panic,
            hv.chrony_maxdistance,
        )

    return NtpPlan(
        packages=(package,),
        tzdata_packages=(table["tzdata_package"],),
        timezone=hv.timezone,
        daemon=daemon,
        cron_daemon=table["cron_daemon"],
        config_file=config_file,
        enabled=hv.enabled,
        manage_config=hv.manage_config,
        cron_handler_enabled=hv.cron_handler_enabled,
        config_content=config_content,
    )


# --- helper deploys ----------------------------------------------------------


def install(plan: NtpPlan) -> Deploy:
    """Deploy stage 1: the NTP package, tzdata and the timezone task.

    Branch-free with respect to facts: the package list and the timezone come from
    the frozen plan. The NTP package folds the molecule pre_task's cache refresh
    (``update=True, cache_valid_time``), gated on the ``AptUpdateStamp`` fact so a
    converged host yields no update command. The timezone request is named for the
    ``restart cron`` channel (:data:`NOTIFY_CRON`).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name="Ensure NTP package is installed.",
            packages=plan.packages,
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
        )
        # The tzdata task's ``when: ansible_facts.system == "Linux"`` is statically
        # true on Ubuntu, so it is unconditional here.
        yield op(
            apt.packages,
            name="Ensure tzdata package is installed (Linux).",
            packages=plan.tzdata_packages,
            sudo=True,
        )
        # The timezone module's keyword is ``timezone`` (not Ansible's ``name``);
        # idempotent via the Timezone fact. Notifies restart cron.
        yield op(
            server.timezone,
            name="Set timezone.",
            timezone=plan.timezone,
            sudo=True,
        )

    return deploy


def configure(plan: NtpPlan, timesyncd: bool) -> Deploy:
    """Deploy stage 3: timesyncd, the daemon state and the config template.

    ``timesyncd`` is the post-install :class:`SystemdUnitFiles` presence read by the
    body's barrier (D4); the rest is a pure function of the plan. The config request
    is named for the ``restart ntp`` channel (:data:`NOTIFY_NTP`).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        # The role's three-condition when: ntp_enabled AND the unit is in
        # service_facts AND status != "not-found". The frozen presence gate covers
        # the last two; enabled is the plan flag.
        if plan.enabled and timesyncd:
            yield op(
                systemd.service,
                name="Disable systemd-timesyncd if it's running but ntp is enabled.",
                service=TIMESYNCD_UNIT,
                running=False,
                enabled=False,
                sudo=True,
            )

        if plan.enabled:
            yield op(
                systemd.service,
                name="Ensure NTP is running and enabled as configured.",
                service=plan.daemon,
                running=True,
                enabled=True,
                sudo=True,
            )
        else:
            yield op(
                systemd.service,
                name="Ensure NTP is stopped and disabled as configured.",
                service=plan.daemon,
                running=False,
                enabled=False,
                sudo=True,
            )

        if plan.manage_config:
            # The template task, pre-rendered control-side (recipe 4). files.install's
            # idempotence is content-only: a converged file yields no commands.
            yield op(
                files.install,
                name="Generate ntp configuration file.",
                content=plan.config_content,
                dest=plan.config_file,
                mode="0644",
                sudo=True,
            )

    return deploy


def handlers(
    plan: NtpPlan, ntp_notified: frozenset[str], cron_notified: frozenset[str]
) -> Deploy:
    """``handlers/main.yml``: restart ntp, then restart cron (definition order).

    A per-host body needs none of the example's machinery. Each restart is gated on
    *this* host being in the matching ``changed_hosts`` set -- the notification read
    the body took from that channel's own checkpoint -- and on the role flag the
    handler's ``when:`` uses (``enabled`` for ntp, ``cron_handler_enabled`` for cron).
    ``restarted=True`` restarts a running unit and starts a stopped one -- Ansible's
    ``state: restarted``. An empty notification set yields nothing, the converged
    no-op Ansible's handler gives for free.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if host.name in ntp_notified and plan.enabled:
            yield op(
                systemd.service,
                name="restart ntp",
                service=plan.daemon,
                restarted=True,
                sudo=True,
            )
        if host.name in cron_notified and plan.cron_handler_enabled:
            yield op(
                systemd.service,
                name="restart cron",
                service=plan.cron_daemon,
                restarted=True,
                sudo=True,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def ntp(v: VarsSet[NtpVars]) -> Deploy:
    """Install and configure an NTP daemon on Ubuntu hosts, and run its handlers.

    Args:
        v: The role's resolved variables, one :class:`NtpVars` per host
            (``resolve_vars(NtpVars, "ntp", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no other
        role consumes them -- so it returns ``None`` on every path, including the
        family guard's early return.

    Raises:
        ValueError: The gathered distribution is not ``Ubuntu``, its major version is
            unreadable, no shipped vars file matches the ``with_first_found`` chain,
            or ``manage_config`` is on with an unknown config basename. Each is
            raised inside the body, after the ``LinuxDistribution`` barrier and
            before the install wave is built, so nothing is half-applied.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the release read that selects the vars table. Its barrier closes
        # before install builds, so the package set is a pure function of it.
        info = yield fact(LinuxDistribution)
        plan = ntp_plan(hv, info)

        # Wave 1: install, then read its own outcome for the cron channel. The
        # checkpoint must immediately follow the stage it reports on.
        yield from install(plan)(host)
        install_report = yield checkpoint("install done")
        cron_notified = changed_hosts(install_report, NOTIFY_CRON)

        # Wave 2: the post-install unit-files probe -- ntpsec can remove timesyncd,
        # so the presence gate is read only after the install wave applied.
        unit_files = yield fact(SystemdUnitFiles)
        timesyncd = TIMESYNCD_UNIT in unit_files

        # Wave 3: configure, then read its own outcome for the ntp channel. The two
        # channels read two different reports, hence two checkpoints (design doc
        # section 18.10), not the one nginx shares.
        yield from configure(plan, timesyncd)(host)
        configure_report = yield checkpoint("configure done")
        ntp_notified = changed_hosts(configure_report, NOTIFY_NTP)

        # Wave 4: the handlers, each gated on its own notification set and flag.
        yield from handlers(plan, ntp_notified, cron_notified)(host)

    return deploy


__all__ = [
    "NtpVars",
    "ntp",
]
