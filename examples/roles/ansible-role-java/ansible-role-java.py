# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the Java Ansible role (``geerlingguy.java``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-java`` installs
the distribution's default OpenJDK package and optionally exports
``JAVA_HOME`` through a ``/etc/profile.d`` script. This program is its
**Ubuntu default path** expressed as a reemote deploy: the version-keyed
``vars/Ubuntu-<major>.yml`` package selection, the ``man1`` directory
workaround (Debian bug 863199 / role issue #64), the ``apt`` install, and
the optional ``java_home.sh`` profile template. Both role variables are
read from the inventory entry's extra keys (reemote's ``host_vars``
mechanism), so ``java_packages`` and ``java_home`` live beside
``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the
other hosts in the shared inventory are not even contacted. The role's
RedHat, Amazon and FreeBSD branches are out of scope (a Phase 0 single-OS
decision). Unlike a role whose vars split only by OS family, this role's
Ubuntu vars split by *major version* (``vars/Ubuntu-12.yml`` through
``vars/Ubuntu-26.yml``), so the single-OS scope still needs the
gather -> snapshot -> factory recipe: ``LinuxDistribution`` is gathered
once, the major version selects the frozen package table, and the deploy
is built over the frozen per-host plan.

Why one deploy is enough
------------------------

The role has no handlers, no ``register``/``when`` outcome reads and no
command/shell tasks; its only mid-play read is the ``include_vars`` OS
dispatch, which happens before any task runs. Freezing that read (plus
the static role variables) into a per-host plan makes the whole role a
single deploy that is branch-free with respect to facts:

1. ``gather_fact(LinuxDistribution)`` -- the Ubuntu major version
2. ``java_plan`` per host -- the vars table selection, the >= 18 man1
   gate and the JAVA_HOME render, frozen control-side
3. ``build_java(plans)`` -- man1 directory, apt packages, JAVA_HOME profile

Implementation gaps
-------------------

None. Every task on the Ubuntu path maps to a covered operation or a
documented recipe -- see ``ansible-role-java.md`` for the coverage table
and every deliberate deviation. The library's one open gap (#17,
``apt.sources_file(signed_by=)`` cannot take a key URL) concerns
repository management this role never does, so no ticket was filed and
no workaround marker appears below.

Prerequisites: none beyond reemote itself. The role's single template is
one ``export JAVA_HOME={{ java_home }}`` line -- a plain f-string renders
it byte-for-byte, so no Jinja2 or PyYAML is needed.

Run it with::

    uv run python examples/roles/ansible-role-java/ansible-role-java.py
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
    apt,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Ubuntu-*.yml, Ubuntu path) ------

#: examples/roles/ansible-role-java/ansible-role-java.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

#: ``setup-Debian.yml``'s man-directory workaround: openjdk's postinst fails
#: on a minimal Ubuntu image without /usr/share/man/man1 (Debian bug 863199,
#: geerlingguy/ansible-role-java#64). The task runs on Ubuntu >= 18 only.
MAN_DIR = "/usr/share/man/man1"

#: ``tasks/main.yml``'s final template task destination.
JAVA_HOME_PROFILE = "/etc/profile.d/java_home.sh"

#: The molecule converge playbook's pre_task refreshes the apt index with
#: ``cache_valid_time: 600`` before the role runs; the role itself does not.
#: Folded into the install request so the example works on a fresh host
#: (deviation D1 in ansible-role-java.md).
APT_CACHE_VALID_TIME = 600

#: The role's ``vars/Ubuntu-<major>.yml`` ``__java_packages`` lists, frozen
#: as tuples (one per supported release, exactly as the role ships them).
#: The include_vars dispatch selects by the gathered major version; an
#: unsupported major raises, as the role's include_vars would fail with
#: "file not found".
UBUNTU_JAVA_PACKAGES: dict[int, tuple[str, ...]] = {
    12: ("openjdk-7-jdk",),
    14: ("openjdk-7-jdk",),
    16: ("openjdk-8-jdk",),
    18: ("openjdk-11-jdk",),
    20: ("openjdk-11-jdk",),
    22: ("openjdk-17-jdk",),
    24: ("openjdk-21-jdk",),
    26: ("openjdk-25-jdk",),
}

#: ``defaults/main.yml``: ``java_home: ""`` and ``java_packages`` commented
#: out -- None means "derive from the vars table", the role's
#: ``set_fact: java_packages={{ __java_packages | list }} when: java_packages
#: is not defined``.
ROLE_DEFAULTS: dict[str, Any] = {
    "java_packages": None,
    "java_home": "",
}


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

    Reads only static inventory data. ``java_packages`` stays None when
    unset (the vars table decides later, once the major version is
    gathered); an override becomes a tuple of names. ``java_home`` is the
    empty string by default -- the template task's
    ``when: java_home is defined and java_home != ""`` then skips it.
    Type gates raise ``TypeError`` (ruff TRY004), value gates
    ``ValueError`` -- both before any host is contacted.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}

    packages = merged["java_packages"]
    if packages is not None:
        if not isinstance(packages, (list, tuple)):
            raise TypeError(f"{host.name}: java_packages must be a list of names")
        if not all(isinstance(package, str) and package for package in packages):
            raise ValueError(
                f"{host.name}: java_packages must hold non-empty package names"
            )
        packages = tuple(packages)

    java_home = merged["java_home"]
    if not isinstance(java_home, str):
        raise TypeError(f"{host.name}: java_home must be a string path")

    return {"java_packages": packages, "java_home": java_home}


def render_java_home(java_home: str) -> str:
    """``templates/java_home.sh.j2``, pre-rendered control-side.

    The template is a single ``export JAVA_HOME={{ java_home }}`` line with
    no other Jinja2 syntax, so a plain f-string is the whole renderer --
    no Jinja2 dependency (the apache migration needs one; this role does
    not). The trailing newline is kept, matching the template source
    byte-for-byte (the apache precedent's ``keep_trailing_newline=True``).
    """
    return f"export JAVA_HOME={java_home}\n"


def ubuntu_major(info: dict[str, Any]) -> int:
    """``ansible_facts.distribution_version.split('.')[0]`` from a fact read.

    The migration targets Ubuntu, so a host reporting another distribution
    is a hard error -- the inventory family said ``Ubuntu``. The
    ``LinuxDistribution`` fact's ``major`` may arrive as an int or a str;
    both normalize here.
    """
    name = info.get("name")
    if name != "Ubuntu":
        raise ValueError(
            f"this migration targets Ubuntu; the host reports {name!r} "
            "(LinuxDistribution.name) -- check the inventory family"
        )
    try:
        return int(info["major"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "cannot read the Ubuntu major version from the distribution "
            f"facts (major={info.get('major')!r})"
        ) from exc


def java_plan(host: Host, info: dict[str, Any]) -> dict[str, Any]:
    """Freeze every read the deploy needs: vars table, man1 gate, render.

    Control-side work, performed once in ``main`` (gather -> snapshot ->
    factory): the gathered major version selects the role's
    ``vars/Ubuntu-<major>.yml`` package list (the include_vars task), the
    ``>= 18`` man-directory gate is frozen, and the JAVA_HOME template
    becomes a plain ``str`` (hashable, equal across phases). An unknown
    major raises here -- the role's include_vars fails with "file not
    found" the same way, even when java_packages is overridden.
    """
    settings = role_settings(host)
    major = ubuntu_major(info)
    if major not in UBUNTU_JAVA_PACKAGES:
        supported = ", ".join(str(m) for m in sorted(UBUNTU_JAVA_PACKAGES))
        raise ValueError(
            f"{host.name}: the role ships no vars/Ubuntu-{major}.yml "
            f"(supported Ubuntu majors: {supported}); the role's "
            "include_vars task fails on an unsupported release the same way"
        )
    packages = settings["java_packages"]
    if packages is None:
        packages = UBUNTU_JAVA_PACKAGES[major]
    java_home = settings["java_home"]
    return {
        "packages": packages,
        "man_dir": major >= 18,
        "java_home_content": render_java_home(java_home) if java_home else None,
    }


# --- the deploy ---------------------------------------------------------------


def build_java(
    plans: dict[str, dict[str, Any]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """The whole Ubuntu role: man1 directory, apt packages, JAVA_HOME profile.

    Every read the role takes mid-play (the include_vars major-version
    dispatch) was frozen into the plan control-side, so this deploy is a
    pure function of static input. The role has no handlers, so there is
    no notification split and no second deploy.
    """

    def java(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return

        if plan["man_dir"]:
            # setup-Debian.yml's first task, gated on Ubuntu >= 18 (frozen
            # into the plan). files.directory converges the mode on an
            # existing directory too, as Ansible's file module does.
            yield op(
                files.directory,
                name="Ensure 'man' directory exists",
                path=MAN_DIR,
                mode="0755",
                sudo=True,
                when=is_ubuntu,
            )

        # Deviation D1: the role's apt task carries no update_cache; its
        # molecule converge playbook refreshes the index in a pre_task
        # (update_cache=true cache_valid_time=600). Folded in here so the
        # example is self-contained on a fresh host. The refresh is gated
        # on the AptUpdateStamp fact against the controller's UTC clock,
        # so a converged host with a fresh cache yields no update command.
        yield op(
            apt.packages,
            name="Ensure Java is installed",
            packages=plan["packages"],
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
            when=is_ubuntu,
        )

        if plan["java_home_content"] is not None:
            # The template task, pre-rendered control-side (recipe 4).
            # files.install's idempotence is content-only: a converged
            # file yields no commands even if its mode drifted.
            yield op(
                files.install,
                name="Set JAVA_HOME if configured",
                content=plan["java_home_content"],
                dest=JAVA_HOME_PROFILE,
                mode="0644",
                sudo=True,
                when=is_ubuntu,
            )
            # The paired files.file converges the mode:0644 the template
            # task enforces on every run -- the apache migration's
            # ports.conf precedent. A no-op when the mode already matches.
            yield op(
                files.file,
                name="Set JAVA_HOME profile script mode",
                path=JAVA_HOME_PROFILE,
                mode="0644",
                sudo=True,
                when=is_ubuntu,
            )

    return java


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

        # The include_vars dispatch: the Ubuntu major version selects
        # vars/Ubuntu-<major>.yml. Gathered once up front (it is static OS
        # identity, not a deploy outcome). gather_fact does not close its
        # hosts, so the finally does; run() builds and closes its own.
        try:
            distributions = await gather_fact(targets, LinuxDistribution)
        finally:
            for host in hosts:
                await host.aclose()
        plans = {
            host.name: java_plan(host, distributions[host.name]) for host in targets
        }
        print("\n== Java plan (LinuxDistribution -> vars/Ubuntu-<major>.yml) ==")
        for name, plan in plans.items():
            packages = ", ".join(plan["packages"]) or "(none)"
            java_home = "set" if plan["java_home_content"] else "unset"
            print(
                f"{name}: packages={packages} "
                f"man_dir={plan['man_dir']} java_home={java_home}"
            )

        report = await execute(
            inventory,
            build_java(plans),
            "Deploy: Java (geerlingguy.java, Ubuntu path)",
        )
        changed = sum(1 for entry in report.log if entry.changed)
        print(f"\n== Done: {changed} changed request(s) ==")
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