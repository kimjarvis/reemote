# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the NTP Ansible role (``geerlingguy.ntp``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-ntp`` installs an
NTP daemon, sets the timezone, disables the competing ``systemd-timesyncd``
unit, brings the daemon up (or down) per ``ntp_enabled`` and optionally
templates its configuration file. This program is its **Ubuntu path**
expressed as reemote deploys: the three-level ``with_first_found`` vars
dispatch, the four ``set_fact`` overrides, the ``apt`` installs, the
``timezone`` task, the post-install ``service_facts`` probe, the
timesyncd/daemon service tasks, the config template and the two handlers
(``restart ntp``, ``restart cron``). Every role variable is read from the
inventory entry's extra keys (reemote's ``host_vars`` mechanism), so
``ntp_enabled``, ``ntp_timezone``, ``ntp_manage_config`` and friends live
beside ``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the
Alpine and CentOS hosts in the shared inventory are not even contacted.
The role's RedHat, Suse, Archlinux and FreeBSD branches are out of scope (a
Phase 0 single-OS decision). The single-OS scope does *not* collapse the
vars dispatch to a constant: the role selects ``vars/Ubuntu-24.04.yml`` ->
``vars/Debian-<major>.yml`` -> ``vars/Debian.yml`` by gathered facts, and
``Debian-11`` differs from ``Debian`` (ntp vs ntpsec), so the
gather -> snapshot -> factory recipe stays.

Why three deploys and two gathers
---------------------------------

The role reads facts mid-play that a single deploy may not branch on:

1. ``gather_fact(LinuxDistribution)`` -- the distribution/version that the
   ``include_vars`` ``with_first_found`` chain dispatches on. Frozen into a
   per-host plan (vars table + ``host.extra`` overlays + config render).
2. **Deploy 1** ``build_install(plans)`` -- the NTP package, tzdata and the
   timezone task (which notifies ``restart cron``).
3. ``gather_fact(SystemdUnitFiles)`` -- the role's ``service_facts`` runs
   *after* the install, because installing ntpsec can remove
   systemd-timesyncd (both conflict through the virtual package
   ``time-daemon``; upstream issue #135). The unit's presence is frozen
   into the plan, so the timesyncd task's three-condition ``when`` becomes
   a static gate.
4. **Deploy 2** ``build_configure(plans)`` -- timesyncd stop/disable, the
   daemon's running/enabled state, and the config template (which notifies
   ``restart ntp``).
5. ``changed_hosts`` over the two notifying name sets, then **Deploy 3**
   ``build_handlers(plans, ntp_notified, cron_notified)`` -- ``restart ntp``
   then ``restart cron`` in handler DEFINITION order, each gated on its
   role flag. The handlers are hand-built rather than ``service_handler``
   because the daemon names (``ntp_daemon``/``ntp_cron_daemon``) are
   per-host overridable, while ``service_handler`` takes a fixed tuple.

Implementation gaps
-------------------

None. Every task and handler on the Ubuntu path maps to a covered
operation or a documented recipe -- see
``docs/examples/roles/ansible-role-ntp.md`` for the coverage table and
every deliberate deviation. All of the library's tracked issues are closed
and none concerns this role's surface, so no ticket was filed and no
``GAP-n`` workaround marker appears below.

Prerequisites: ``jinja2`` on the control machine renders the role's
``ntp.conf.j2`` / ``chrony.conf.j2`` (loops and the ``is sameas true``
test). reemote ships no template engine, so Jinja2 is imported inside the
render function (importing this module never requires it); it is in the
project's ``dev`` group for the offline tests and ``--with jinja2``
supplies it for a standalone run. The Ansible-specific ``comment`` filter
on the ``ansible_managed`` header is avoided by freezing that header to
its default output (deviation D2).

Run it with::

    uv run --with jinja2 python examples/roles/ansible-role-ntp/ansible-role-ntp.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    LinuxDistribution,
    OperationRequest,
    PrivilegeError,
    SystemdUnitFiles,
    apt,
    changed_hosts,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    server,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/*.yml, Ubuntu path) -------------

#: examples/roles/ansible-role-ntp/ansible-role-ntp.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

#: The molecule converge playbook's pre_task refreshes the apt index with
#: ``cache_valid_time: 600`` before the role runs; the role itself does not.
#: Folded into the NTP install request so the example works on a fresh host
#: (deviation D1 in the role doc).
APT_CACHE_VALID_TIME = 600

#: The unit the role stops/disables when it is still present after the
#: install and ``ntp_enabled`` is true.
TIMESYNCD_UNIT = "systemd-timesyncd.service"

#: ``vars/Ubuntu-24.04.yml`` and ``vars/Debian.yml`` are byte-identical: the
#: ntpsec daemon, its config/drift paths, tzdata and the cron daemon. Frozen
#: as a module constant (the ``__ntp_*`` values the set_fact tasks read).
NTPSEC_VARS: dict[str, str] = {
    "daemon": "ntpsec",
    "package": "ntpsec",
    "config_file": "/etc/ntpsec/ntp.conf",
    "driftfile": "/var/lib/ntpsec/ntp.drift",
    "tzdata_package": "tzdata",
    "cron_daemon": "cron",
}

#: ``vars/Debian-11.yml``: the legacy ntp daemon and paths (Debian 11 /
#: ancient Ubuntu 11.x selects this through the family-major chain level).
DEBIAN_11_VARS: dict[str, str] = {
    "daemon": "ntp",
    "package": "ntp",
    "config_file": "/etc/ntp.conf",
    "driftfile": "/var/lib/ntp/drift",
    "tzdata_package": "tzdata",
    "cron_daemon": "cron",
}

#: The role's ``with_first_found`` chain, keyed by vars-file stem. Only the
#: Debian-family files the Ubuntu target can reach are shipped; the first
#: candidate present wins, exactly as ``with_first_found`` resolves. A chain
#: that hits nothing raises -- the role's ``include_vars`` (no ``skip: true``)
#: fails with "file not found" the same way.
VARS_FILES: dict[str, dict[str, str]] = {
    "Ubuntu-24.04": NTPSEC_VARS,
    "Debian-11": DEBIAN_11_VARS,
    "Debian": NTPSEC_VARS,
}

#: ``defaults/main.yml``. The four ``ntp_daemon``/``ntp_package``/
#: ``ntp_config_file``/``ntp_driftfile`` keys are commented out there -- None
#: means "derive from the vars table", the role's ``set_fact: ... when: ...
#: is not defined``. ``ntp_servers`` is None to reproduce the default's
#: embedded Jinja (the four pool strings interpolated with ``ntp_area``).
ROLE_DEFAULTS: dict[str, Any] = {
    "ntp_enabled": True,
    "ntp_timezone": "Etc/UTC",
    "ntp_manage_config": False,
    "ntp_area": "",
    "ntp_servers": None,
    "ntp_restrict": ("127.0.0.1", "::1"),
    "ntp_cron_handler_enabled": False,
    "ntp_tinker_panic": False,
    "ntp_chrony_maxdistance": "3",
    "ntp_daemon": None,
    "ntp_package": None,
    "ntp_config_file": None,
    "ntp_driftfile": None,
}

#: The notifying request names -- the ``notify:`` channel. The timezone task
#: queues ``restart cron``; the config template queues ``restart ntp``.
NOTIFY_CRON: frozenset[str] = frozenset({"Set timezone."})
NOTIFY_NTP: frozenset[str] = frozenset({"Generate ntp configuration file."})

#: ``templates/ntp.conf.j2``, verbatim except line 1: the Ansible-specific
#: ``{{ ansible_managed | comment }}`` is frozen to its default output
#: "# Ansible managed" (deviation D2), so only built-in Jinja2 syntax
#: remains. Rendered with Ansible's whitespace defaults (see ``_render``).
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

#: ``templates/chrony.conf.j2``, verbatim except the frozen managed header
#: (D2). Reachable only when ``ntp_config_file`` is overridden to a
#: ``chrony.conf`` path -- the Ubuntu default selects ntp.conf (deviation D6).
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


# --- pure helpers ------------------------------------------------------------


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of entries, each carrying at least
    ``host`` and ``connection``; every other key is preserved and reaches
    the deploy as ``host.extra`` -- the role variables live there.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data and stays a stable module-level
    function, so both deploy phases reach the same eligibility decision.
    A skipped request is never materialized, so the Alpine and CentOS
    hosts in the shared inventory are not contacted at all.
    """
    return host.family == "Ubuntu"


def _require_bool(value: Any, key: str, name: str) -> bool:
    """A role flag must be a real bool (the inventory is JSON)."""
    if not isinstance(value, bool):
        raise TypeError(f"{name}: {key} must be a boolean")
    return value


def _require_str(value: Any, key: str, name: str) -> str:
    """A role string variable must be a non-empty string."""
    if not isinstance(value, str):
        raise TypeError(f"{name}: {key} must be a string")
    if not value:
        raise ValueError(f"{name}: {key} must not be empty")
    return value


def _require_str_list(value: Any, key: str, name: str) -> tuple[str, ...]:
    """A role list variable becomes a tuple of non-empty strings."""
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name}: {key} must be a list of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{name}: {key} must hold non-empty strings")
    return tuple(value)


def role_settings(host: Host) -> dict[str, Any]:
    """Freeze one host's role variables: defaults overlaid with ``host.extra``.

    Reads only static inventory data. The four ``set_fact`` variables
    (``ntp_daemon``/``ntp_package``/``ntp_config_file``/``ntp_driftfile``)
    stay None when unset -- the vars table decides later, once the
    distribution is gathered (the role's ``when: ... is not defined``).
    ``ntp_servers`` stays None to derive the pool strings from ``ntp_area``.
    Type gates raise ``TypeError`` (ruff TRY004), value gates ``ValueError``
    -- both before any host is contacted.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}
    name = host.name

    settings: dict[str, Any] = {
        "ntp_enabled": _require_bool(merged["ntp_enabled"], "ntp_enabled", name),
        "ntp_timezone": _require_str(merged["ntp_timezone"], "ntp_timezone", name),
        "ntp_manage_config": _require_bool(
            merged["ntp_manage_config"], "ntp_manage_config", name
        ),
        "ntp_area": merged["ntp_area"],
        "ntp_restrict": _require_str_list(merged["ntp_restrict"], "ntp_restrict", name),
        "ntp_cron_handler_enabled": _require_bool(
            merged["ntp_cron_handler_enabled"], "ntp_cron_handler_enabled", name
        ),
        "ntp_tinker_panic": _require_bool(
            merged["ntp_tinker_panic"], "ntp_tinker_panic", name
        ),
        "ntp_chrony_maxdistance": _require_str(
            merged["ntp_chrony_maxdistance"], "ntp_chrony_maxdistance", name
        ),
    }
    if not isinstance(settings["ntp_area"], str):
        raise TypeError(f"{name}: ntp_area must be a string")

    servers = merged["ntp_servers"]
    settings["ntp_servers"] = (
        None if servers is None else _require_str_list(servers, "ntp_servers", name)
    )

    # The four set_fact overrides: None (derive from the vars table) or a
    # non-empty string. ntp_tzdata_package and ntp_cron_daemon come straight
    # from include_vars (no set_fact guard), so they are not overridable here
    # -- faithful to the role's variable precedence.
    for key in ("ntp_daemon", "ntp_package", "ntp_config_file", "ntp_driftfile"):
        value = merged[key]
        settings[key] = None if value is None else _require_str(value, key, name)

    return settings


def default_servers(area: str) -> tuple[str, ...]:
    """The role's default ``ntp_servers`` with ``ntp_area`` interpolated.

    ``defaults/main.yml`` spells each entry
    ``"0{{ '.' + ntp_area if ntp_area else '' }}.pool.ntp.org iburst"``;
    resolved control-side here (an empty area yields the worldwide pool).
    """
    zone = f".{area}" if area else ""
    return tuple(f"{i}{zone}.pool.ntp.org iburst" for i in range(4))


def resolve_vars(info: dict[str, Any]) -> dict[str, str]:
    """The ``include_vars`` ``with_first_found`` chain, over frozen tables.

    Three candidate stems in order: ``{distribution}-{distribution_version}``
    (``Ubuntu-24.04``), ``{os_family}-{distribution_major_version}``
    (``Debian-24``), ``{os_family}`` (``Debian``). The first present in
    ``VARS_FILES`` wins. The migration targets Ubuntu, so a host reporting
    another distribution is a hard error -- the inventory family said ``Ubuntu`` (the java migration's fail-fast precedent). This narrows the
    role's multi-OS ``include_vars`` dispatch to the Debian family
    (deviation D5).
    """
    name = info.get("name")
    if name != "Ubuntu":
        raise ValueError(
            f"this migration targets Ubuntu; the host reports {name!r} "
            "(LinuxDistribution.name) -- check the inventory family"
        )
    try:
        major = int(info["major"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "cannot read the Ubuntu major version from the distribution "
            f"facts (major={info.get('major')!r})"
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

    Jinja2 is imported *here*, not at module scope: it is not a reemote
    runtime dependency, so importing this module never requires it. It is
    in the project's ``dev`` group (the offline tests render these
    templates) and ``--with jinja2`` supplies it for a standalone run.
    ``trim_blocks=True`` / ``lstrip_blocks=False`` /
    ``keep_trailing_newline=True`` reproduce Ansible's whitespace handling
    byte-for-byte; ``StrictUndefined`` makes an unset variable fail loudly.
    """
    import jinja2

    environment = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    return environment.from_string(template_text).render(**context)


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
    ``/etc/ntpsec/ntp.conf`` and chrony.conf.j2 for a ``chrony.conf`` path.
    An unknown basename raises -- Ansible fails with "template not found"
    the same way (the role ships no clock.j2 task; that template is dead).
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


def ntp_plan(host: Host, info: dict[str, Any]) -> dict[str, Any]:
    """Freeze every read the deploys need: vars chain, overrides, render.

    Control-side work, performed once in ``main`` (gather -> snapshot ->
    factory): the gathered distribution selects the vars table (the
    include_vars chain), the four set_fact overrides win when defined, the
    pool servers are derived from ``ntp_area`` when unset, and the config
    template becomes a plain ``str`` (hashable, equal across phases). The
    config is rendered only when ``ntp_manage_config`` is true, so an
    unknown basename does not raise on the default path -- the role's
    template task is skipped there too. The timesyncd-presence gate is
    added later, after the post-install ``SystemdUnitFiles`` gather.
    """
    settings = role_settings(host)
    table = resolve_vars(info)

    daemon = settings["ntp_daemon"]
    if daemon is None:
        daemon = table["daemon"]
    package = settings["ntp_package"]
    if package is None:
        package = table["package"]
    config_file = settings["ntp_config_file"]
    if config_file is None:
        config_file = table["config_file"]
    driftfile = settings["ntp_driftfile"]
    if driftfile is None:
        driftfile = table["driftfile"]

    servers = settings["ntp_servers"]
    if servers is None:
        servers = default_servers(settings["ntp_area"])

    config_content = None
    if settings["ntp_manage_config"]:
        config_content = render_config(
            config_file,
            driftfile,
            servers,
            settings["ntp_restrict"],
            settings["ntp_tinker_panic"],
            settings["ntp_chrony_maxdistance"],
        )

    return {
        "packages": (package,),
        "tzdata_packages": (table["tzdata_package"],),
        "timezone": settings["ntp_timezone"],
        "daemon": daemon,
        "cron_daemon": table["cron_daemon"],
        "config_file": config_file,
        "enabled": settings["ntp_enabled"],
        "manage_config": settings["ntp_manage_config"],
        "cron_handler_enabled": settings["ntp_cron_handler_enabled"],
        "config_content": config_content,
    }


# --- the deploys -------------------------------------------------------------


def build_install(
    plans: dict[str, dict[str, Any]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 1: the NTP package, tzdata and the timezone task.

    The package list and timezone come from the frozen plan, so this deploy
    is a pure function of static input. The timezone request is named for
    the ``restart cron`` notification channel (``NOTIFY_CRON``).
    """

    def install(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return

        # Deviation D1: the role's package task carries no update_cache; its
        # molecule converge playbook refreshes the index in a pre_task
        # (update_cache=true cache_valid_time=600). Folded in here so the
        # example is self-contained on a fresh host. The refresh is gated on
        # the AptUpdateStamp fact against the controller's UTC clock, so a
        # converged host with a fresh cache yields no update command.
        yield op(
            apt.packages,
            name="Ensure NTP package is installed.",
            packages=plan["packages"],
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
            when=is_ubuntu,
        )
        # The tzdata task's ``when: ansible_facts.system == "Linux"`` is
        # statically true on Ubuntu, so it is unconditional here.
        yield op(
            apt.packages,
            name="Ensure tzdata package is installed (Linux).",
            packages=plan["tzdata_packages"],
            sudo=True,
            when=is_ubuntu,
        )
        # The timezone module's keyword is ``timezone`` (not Ansible's
        # ``name``); idempotent via the Timezone fact. Notifies restart cron.
        yield op(
            server.timezone,
            name="Set timezone.",
            timezone=plan["timezone"],
            sudo=True,
            when=is_ubuntu,
        )

    return install


def build_configure(
    plans: dict[str, dict[str, Any]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2: timesyncd, the daemon state and the config template.

    Every mid-play read was frozen into the plan: ``timesyncd`` is the
    post-install ``SystemdUnitFiles`` presence (the role's ``service_facts``
    ``status != "not-found"`` gate, deviation D4), ``enabled`` is the static
    ``ntp_enabled`` branch, and ``config_content`` is the pre-rendered
    template. The config request is named for the ``restart ntp`` channel
    (``NOTIFY_NTP``).
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return

        # The role's three-condition when: ntp_enabled AND the unit is in
        # service_facts AND its status != "not-found". The frozen presence
        # gate covers the last two (a unit absent from list-unit-files is
        # exactly "not-found"); ntp_enabled is the static plan flag.
        if plan["enabled"] and plan["timesyncd"]:
            yield op(
                systemd.service,
                name="Disable systemd-timesyncd if it's running but ntp is enabled.",
                service=TIMESYNCD_UNIT,
                running=False,
                enabled=False,
                sudo=True,
                when=is_ubuntu,
            )

        # Deviation D3: the role's ``ignore_errors: "{{ ansible_check_mode }}"``
        # is dropped -- reemote's dry=True never applies commands, so the
        # check-mode tolerance is moot; a real failure raises CommandError.
        if plan["enabled"]:
            yield op(
                systemd.service,
                name="Ensure NTP is running and enabled as configured.",
                service=plan["daemon"],
                running=True,
                enabled=True,
                sudo=True,
                when=is_ubuntu,
            )
        else:
            yield op(
                systemd.service,
                name="Ensure NTP is stopped and disabled as configured.",
                service=plan["daemon"],
                running=False,
                enabled=False,
                sudo=True,
                when=is_ubuntu,
            )

        if plan["manage_config"]:
            # The template task, pre-rendered control-side (recipe 4).
            # files.install's idempotence is content-only: a converged file
            # yields no commands. mode 0644 as the role's template task sets.
            yield op(
                files.install,
                name="Generate ntp configuration file.",
                content=plan["config_content"],
                dest=plan["config_file"],
                mode="0644",
                sudo=True,
                when=is_ubuntu,
            )

    return configure


def build_handlers(
    plans: dict[str, dict[str, Any]],
    ntp_notified: frozenset[str],
    cron_notified: frozenset[str],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 3: ``handlers/main.yml`` -- restart ntp, then restart cron.

    Ansible flushes handlers in DEFINITION order, so restart ntp comes
    first and restart cron second. Each is gated on its role flag
    (``ntp_enabled`` / ``ntp_cron_handler_enabled``) read from the frozen
    plan, and on membership in the matching ``changed_hosts`` set. The
    daemon names are per-host (overridable via ``host.extra``), so the
    deploy is hand-built rather than ``service_handler`` (which takes a
    fixed service tuple). ``restarted=True`` restarts a running unit and
    starts a stopped one (the operation's default ``running=True``) --
    ``state: restarted``. An empty notification set yields an empty deploy:
    the converged no-op Ansible's handler gives for free.
    """

    def handlers(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        if host.name in ntp_notified and plan["enabled"]:
            yield op(
                systemd.service,
                name="restart ntp",
                service=plan["daemon"],
                restarted=True,
                sudo=True,
                when=is_ubuntu,
            )
        if host.name in cron_notified and plan["cron_handler_enabled"]:
            yield op(
                systemd.service,
                name="restart cron",
                service=plan["cron_daemon"],
                restarted=True,
                sudo=True,
                when=is_ubuntu,
            )

    return handlers


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot, which is what makes the determinism
    guard hold. A dry preview of an outcome-fed deploy (the handlers)
    consults the frozen apply outcome of the runs before it.
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
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        targets = [host for host in hosts if is_ubuntu(host)]
        skipped = [host.name for host in hosts if not is_ubuntu(host)]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        if not targets:
            print("\nNo host in the Ubuntu family; nothing to deploy.")
            return

        # The role's variable gates, before any host is contacted.
        for host in targets:
            role_settings(host)

        # The include_vars dispatch: the distribution/version selects the
        # vars file through the with_first_found chain. Gathered once up
        # front (static OS identity, not a deploy outcome). gather_fact does
        # not close its hosts, so the finally does; run() builds its own.
        try:
            distributions = await gather_fact(targets, LinuxDistribution)
        finally:
            for host in hosts:
                await host.aclose()
        plans = {
            host.name: ntp_plan(host, distributions[host.name]) for host in targets
        }
        print("\n== NTP plan (LinuxDistribution -> with_first_found vars) ==")
        for name, plan in plans.items():
            packages = ", ".join(plan["packages"])
            config = "managed" if plan["manage_config"] else "unmanaged"
            print(
                f"{name}: daemon={plan['daemon']} packages={packages} "
                f"enabled={plan['enabled']} config={config}"
            )

        # Deploy 1: the NTP package, tzdata and the timezone (notifies cron).
        install_report = await execute(
            inventory,
            build_install(plans),
            "Deploy 1: install NTP + tzdata, set timezone",
        )

        # The role's service_facts runs AFTER the install: ntpsec can remove
        # systemd-timesyncd (time-daemon conflict, upstream #135). Gather the
        # unit files now and freeze the timesyncd presence into each plan.
        # gather_fact does not close its hosts, so the finally does.
        try:
            unit_files = await gather_fact(targets, SystemdUnitFiles)
        finally:
            for host in targets:
                await host.aclose()
        for host in targets:
            plans[host.name]["timesyncd"] = TIMESYNCD_UNIT in unit_files[host.name]
        print("\n== systemd-timesyncd present after install (service_facts) ==")
        for name, plan in plans.items():
            print(f"{name}: timesyncd={plan['timesyncd']}")

        # Deploy 2: timesyncd, the daemon state, the config (notifies ntp).
        configure_report = await execute(
            inventory,
            build_configure(plans),
            "Deploy 2: timesyncd + daemon state + config",
        )

        # The handlers: changed_hosts over the two notifying name sets, read
        # from the deploy that carries each notifying request.
        cron_notified = changed_hosts(install_report, NOTIFY_CRON)
        ntp_notified = changed_hosts(configure_report, NOTIFY_NTP)
        print("\n== Handler notification sets ==")
        print(f"restart ntp:  {sorted(ntp_notified) or '(none)'}")
        print(f"restart cron: {sorted(cron_notified) or '(none)'}")
        handler_report = await execute(
            inventory,
            build_handlers(plans, ntp_notified, cron_notified),
            "Deploy 3: restart ntp / restart cron handlers",
        )

        changed = sum(
            1
            for report in (install_report, configure_report, handler_report)
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across the three deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, a role variable is
        # invalid, or the host reports an unsupported distribution/release.
        # No host mutation is implied by the variable gates, so the message
        # is the whole report.
        print(f"\nERROR: {exc}", file=sys.stderr)
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
