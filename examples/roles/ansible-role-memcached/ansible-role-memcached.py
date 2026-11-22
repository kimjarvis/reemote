# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the Memcached Ansible role (``geerlingguy.memcached``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-memcached``
installs Memcached on Debian/Ubuntu or RedHat/CentOS, writes
``/etc/memcached.conf`` from an OS-specific Jinja2 template, and starts
and enables the service. This program is its **Ubuntu default path**
expressed as reemote deploys: the apt cache refresh, the ``memcached``
package, the rendered configuration file (root:root, mode 0644) and the
started-and-enabled service. Every role variable is read from the
inventory entry's extra keys (reemote's ``host_vars`` mechanism), so
``memcached_port``, ``memcached_listen_ip`` and friends live beside
``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the
other hosts in the shared inventory are not even contacted. The role's
RedHat branch (``vars/RedHat.yml``, ``memcached-RedHat.conf.j2``, the
sysconfig destination) is out of scope (a Phase 0 single-OS decision).
The vars split only by OS family, so the single-OS scope collapses the
``include_vars`` dispatch to frozen module constants -- no
``LinuxDistribution`` gather is needed (the nginx precedent).

Why two deploys
---------------

The template task carries ``notify: restart memcached``. A deploy body
cannot observe its own outcomes (the determinism guard runs it twice),
so the handler migrates as the documented two-deploy outcome split:

1. ``role_settings`` + ``render_memcached_conf`` per host -- the static
   variables and the Jinja2 render, frozen control-side in ``main()``
2. ``build_memcached(plans)`` -- cache refresh, package, config install
   plus its metadata companion, service start/enable
3. ``changed_hosts(report, NOTIFY_RESTART)`` -- the notification read,
   taken in the gap between the two runs
4. ``restart_handler(frozen_set, "memcached")`` -- the handler deploy,
   empty when nothing changed

Implementation gaps
-------------------

None. Every task on the Ubuntu path maps to a covered operation or a
documented recipe -- see ``ansible-role-memcached.md`` for the coverage
table and every deliberate deviation. Every library issue this role
could touch is closed (#9 ``cache_valid_time``, #12 ``0640`` read-back,
#13 apt ``latest``), so no ticket was filed and no ``GAP-n`` marker
appears below.

Prerequisites: Jinja2 renders ``memcached-Debian.conf.j2`` (an ``is
string`` test and a for-loop -- the nginx/apache precedent). It is
imported inside the render function, never at module scope; the repo's
``dev`` group declares it, and a standalone run supplies it with
``--with jinja2``.

Run it with::

    uv run python examples/roles/ansible-role-memcached/ansible-role-memcached.py
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
    OperationRequest,
    PrivilegeError,
    apt,
    changed_hosts,
    files,
    format_log,
    format_plan,
    op,
    restart_handler,
    run,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path) --------

#: examples/roles/ansible-role-memcached/ansible-role-memcached.py -> project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

#: ``vars/Debian.yml``, frozen: the include_vars OS dispatch collapses to
#: these constants under the single-OS scope. ``__memcached_user`` is the
#: private default the ``set_fact`` task copies into ``memcached_user``
#: when the variable is not defined.
DEBIAN_VARS: dict[str, str] = {
    "__memcached_user": "memcache",
    "memcached_config_file": "/etc/memcached.conf",
}

#: ``defaults/main.yml``. ``memcached_user`` is deliberately absent: the
#: role defines it through ``set_fact: memcached_user={{ __memcached_user }}
#: when: memcached_user is not defined``, so its default comes from the
#: Debian vars table above and an explicit host.extra key wins over both.
ROLE_DEFAULTS: dict[str, Any] = {
    "memcached_port": 11211,
    "memcached_listen_ip": "127.0.0.1",
    "memcached_memory_limit": 64,
    "memcached_connections": 1024,
    "memcached_log_file": "/var/log/memcached.log",
    "memcached_log_verbosity": "",
    "memcached_max_item_size": "1m",
    "memcached_threads": 4,
}

#: ``templates/memcached-Debian.conf.j2``, verbatim. Rendered control-side
#: with Ansible's Jinja2 environment (see ``render_memcached_conf``).
MEMCACHED_CONF_TEMPLATE = """\
# {{ ansible_managed }}
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

#: ``ansible_managed``'s ansible.cfg default, frozen (deviation D1).
#: Ansible's real default is a *timestamped* string ("Ansible managed:
#: <file> modified on <date> by <uid> on <host>"), which would change the
#: rendered content on every run -- re-installing the config and firing
#: the restart handler forever. The php migration's freeze precedent.
ANSIBLE_MANAGED = "Ansible managed"

#: The role's apt task: ``update_cache=yes cache_valid_time=86400``.
CACHE_VALID_TIME = 86400

#: The request names that queue ``restart memcached``. The role's only
#: notifying task is "Copy Memcached configuration."; the metadata
#: companion notifies too, because Ansible's template module reports a
#: change on owner/group/mode drift as well (deviation D3).
NOTIFY_RESTART = frozenset(
    {
        "Copy Memcached configuration",
        "Set Memcached configuration attributes",
    }
)


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


def role_settings(host: Host) -> dict[str, Any]:
    """Freeze one host's role variables: defaults overlaid with ``host.extra``.

    Reads only static inventory data. ``memcached_user`` defaults to the
    Debian vars table's ``__memcached_user`` (the role's ``set_fact ...
    when: memcached_user is not defined``); an explicit override wins.
    ``memcached_listen_ip`` keeps the role's str-or-list shape: the
    template's ``is string`` test renders one ``-l`` line for a string
    and one per item for a sequence (normalized to a tuple here).
    Type gates raise ``TypeError`` (ruff TRY004) before any host is
    contacted.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}
    merged.setdefault("memcached_user", DEBIAN_VARS["__memcached_user"])

    for key in (
        "memcached_port",
        "memcached_memory_limit",
        "memcached_connections",
        "memcached_threads",
    ):
        if not isinstance(merged[key], int):
            raise TypeError(f"{host.name}: {key} must be an integer")
    for key in (
        "memcached_user",
        "memcached_log_file",
        "memcached_log_verbosity",
        "memcached_max_item_size",
    ):
        if not isinstance(merged[key], str):
            raise TypeError(f"{host.name}: {key} must be a string")

    listen_ip = merged["memcached_listen_ip"]
    if isinstance(listen_ip, str):
        merged["memcached_listen_ip"] = listen_ip
    elif isinstance(listen_ip, (list, tuple)):
        if not all(isinstance(ip, str) for ip in listen_ip):
            raise TypeError(
                f"{host.name}: memcached_listen_ip must hold string addresses"
            )
        merged["memcached_listen_ip"] = tuple(listen_ip)
    else:
        raise TypeError(
            f"{host.name}: memcached_listen_ip must be a string or a list of strings"
        )
    return merged


def render_memcached_conf(settings: dict[str, Any]) -> str:
    """``templates/memcached-Debian.conf.j2``, pre-rendered control-side.

    Jinja2 is imported *here*, not at module scope: it is not a reemote
    runtime dependency, so importing this module never requires it. It is
    in the project's ``dev`` group (the offline example tests render this
    template) and ``--with jinja2`` supplies it for a standalone run. The
    environment uses Ansible's whitespace defaults (``trim_blocks=True``,
    ``lstrip_blocks=False``, ``keep_trailing_newline=True``) and
    ``StrictUndefined`` so an unset variable fails loudly rather than
    rendering empty -- the nginx/apache precedent. ``ansible_managed`` is
    the frozen static string (deviation D1).
    """
    import jinja2

    environment = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    template = environment.from_string(MEMCACHED_CONF_TEMPLATE)
    return template.render(**settings, ansible_managed=ANSIBLE_MANAGED)


def memcached_plan(settings: dict[str, Any]) -> dict[str, Any]:
    """Freeze the one read the deploy needs: the rendered config content.

    Control-side work, performed once in ``main`` (recipe 4): the Jinja2
    template becomes a plain ``str`` (hashable, equal across phases). The
    destination is a frozen Debian-vars constant, so the plan carries
    only the content.
    """
    return {"config_content": render_memcached_conf(settings)}


# --- the deploys --------------------------------------------------------------


def build_memcached(
    plans: dict[str, dict[str, Any]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """The Ubuntu role: cache refresh, package, config, service.

    Every read the role takes mid-play was frozen into the plan
    control-side (the template render), so this deploy is a pure function
    of static input. The notifying requests carry the stable names
    ``NOTIFY_RESTART`` enumerates; the final service task carries no
    ``notify`` in the role, so its name is deliberately absent.
    """

    def deploy_memcached(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return

        # The role's apt task: update_cache=yes cache_valid_time=86400.
        # The refresh is gated on the AptUpdateStamp fact against the
        # controller's UTC clock, so a converged host with a cache fresher
        # than 24 hours yields no update command (#9, closed).
        yield op(
            apt.update,
            name="Update apt cache",
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
            when=is_ubuntu,
        )

        # The package task. Its ``register: memcached_install`` is never
        # consumed by any later task or ``when`` in the role, so it is
        # dropped (deviation D2) -- reemote reads outcomes between runs.
        yield op(
            apt.packages,
            name="Install Memcached",
            packages=("memcached",),
            sudo=True,
            when=is_ubuntu,
        )

        # The template task, pre-rendered control-side (recipe 4).
        # files.install's idempotence is content-only: a converged file
        # yields no commands even if its mode or ownership drifted.
        yield op(
            files.install,
            name="Copy Memcached configuration",
            content=plan["config_content"],
            dest=DEBIAN_VARS["memcached_config_file"],
            mode="0644",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )
        # The paired files.file converges the root:root 0644 metadata the
        # template task enforces on every run (deviation D3 -- the java
        # and apache migrations' companion precedent). It notifies too:
        # Ansible's template reports a change on metadata drift as well.
        yield op(
            files.file,
            name="Set Memcached configuration attributes",
            path=DEBIAN_VARS["memcached_config_file"],
            mode="0644",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )

        # The final service task: state=started enabled=yes. Both flags
        # are fact-gated (SystemdServiceState / SystemdServiceEnabled),
        # so a running, enabled unit yields no commands.
        yield op(
            systemd.service,
            name="Ensure Memcached is started and enabled",
            service="memcached",
            running=True,
            enabled=True,
            sudo=True,
            when=is_ubuntu,
        )

    return deploy_memcached


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot, which is what makes the determinism
    guard hold. A dry preview of the handler deploy consults the frozen
    apply outcome of the run before it, so it is honest; a dry preview of
    the notifying deploy cannot know whether the handler will fire.
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

        # The role's variable resolution and the template render, before
        # any host is contacted: every value is static (defaults + the
        # entry's extra keys), so no fact gather is needed at all.
        plans = {
            host.name: memcached_plan(role_settings(host)) for host in targets
        }
        print("\n== Memcached plan (defaults + host.extra, rendered control-side) ==")
        for name in plans:
            print(f"{name}: {DEBIAN_VARS['memcached_config_file']}")

        # Deploy 1: the notifying tasks plus the final service task.
        report = await execute(
            inventory,
            build_memcached(plans),
            "Deploy 1: Memcached (geerlingguy.memcached, Ubuntu path)",
        )

        # The handler notification read, in the gap between the two runs:
        # only hosts whose config content or metadata really changed queue
        # ``restart memcached``.
        restart_notified = changed_hosts(report, NOTIFY_RESTART)
        print("\n== Handler notification set ==")
        print(f"restart memcached: {sorted(restart_notified) or '(none)'}")

        # Deploy 2: the handler. An empty set yields an empty deploy --
        # the converged no-op Ansible's handler gives for free.
        handler_report = await execute(
            inventory,
            restart_handler(restart_notified, "memcached", when=is_ubuntu),
            "Deploy 2: restart memcached handler",
        )

        changed = sum(
            1
            for rep in (report, handler_report)
            for entry in rep.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across the two deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, or a role variable
        # is invalid. No host mutation is implied by the variable gates,
        # so the message is the whole report.
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