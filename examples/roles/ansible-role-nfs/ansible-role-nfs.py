# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the NFS Ansible role (``geerlingguy.nfs``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-nfs`` installs the
NFS server packages, creates the directories to export, renders
``/etc/exports`` from a template, starts and enables the NFS server when any
export is configured, and re-exports the filesystems (``exportfs -ra``)
whenever the exports file changes. This program is its **Ubuntu default
path** expressed as a reemote deploy. The role variable ``nfs_exports`` is
read from the inventory entry's extra keys (reemote's ``host_vars``
mechanism), so it lives beside ``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the other
hosts in the shared inventory are not even contacted. The role's RedHat and
Fedora branches are out of scope (a Phase 0 single-OS decision). The vars
split only by OS family (``vars/Debian.yml`` sets ``nfs_server_daemon``), so
the single-OS scope collapses the ``include_vars`` dispatch to frozen module
constants -- no fact gather is needed: the template consumes only the static
``nfs_exports`` list.

Why one deploy plus one action gather
-------------------------------------

The role's only handler is ``reload nfs: command: exportfs -ra`` -- a
*command*, not a systemd service action. A bare command cannot live inside a
deploy (``ActionInDeployError``), and no operation wraps it, so the handler
is the two-deploy outcome split composed with the command-task recipe:

1. ``nfs_plan`` per host -- freeze the exports tuple, the deduplicated export
   directories, the rendered ``/etc/exports`` content and the service gate
2. ``build_nfs(plans)`` -- packages, export directories, exports file,
   gated service start/enable
3. ``changed_hosts(report, NOTIFYING)`` -- the notification read, between runs
4. ``reload_nfs(targets, changed)`` -- ``gather_action(Shell,
   command="exportfs -ra", sudo=True)`` on the notified hosts only

``reload_handler``/``service_handler(action="reloaded")`` would be
*unfaithful* here: ``systemd.service(reloaded=True)`` reloads only a unit
that is already running, and on the role's default path (``nfs_exports: []``)
the service-start task is skipped -- yet Ansible's command handler still
runs whenever the exports file changed. The ``Shell`` gather reproduces the
command exactly, once per notified host, after every notifying request has
landed (Ansible's end-of-play flush).

Implementation gaps
-------------------

None. Every task on the Ubuntu path maps to a covered operation or a
documented recipe -- see ``ansible-role-nfs.md`` for the coverage table and
every deliberate deviation. The library's one open gap (#20, no control-side
HTTP content fetch for ``uri`` ``return_content`` parity) concerns a module
this role never uses, so no ticket was filed and no workaround marker
appears below.

Prerequisites: none beyond reemote itself. The role's single template is a
static comment header plus one line per export -- a plain Python join
renders it byte-for-byte (verified against Jinja2 with Ansible's
``trim_blocks=True``, ``keep_trailing_newline=True`` environment), so no
Jinja2 or PyYAML is needed.

Run it with::

    uv run python examples/roles/ansible-role-nfs/ansible-role-nfs.py
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
    Shell,
    apt,
    changed_hosts,
    files,
    format_log,
    format_plan,
    gather_action,
    op,
    run,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path) --------

#: ``vars/Debian.yml``: the service the ``service`` task manages. The
#: include_vars OS dispatch collapses to this frozen constant (Ubuntu is in
#: the Debian family); the RedHat/Fedora vars files are out of scope.
NFS_SERVER_DAEMON = "nfs-kernel-server"

#: ``tasks/setup-Debian.yml``'s apt task, frozen as a tuple.
NFS_PACKAGES = ("nfs-common", "nfs-kernel-server")

#: ``tasks/main.yml``'s template task destination.
EXPORTS_PATH = "/etc/exports"

#: ``templates/exports.j2``'s static comment header, byte-for-byte (the
#: template's first ten lines, each newline-terminated).
EXPORTS_HEADER = (
    "# /etc/exports: the access control list for filesystems which may be exported\n"
    "#   to NFS clients.  See exports(5).\n"
    "#\n"
    "# Example for NFSv2 and NFSv3:\n"
    "# /srv/homes       hostname1(rw,sync,no_subtree_check) "
    "hostname2(ro,sync,no_subtree_check)\n"
    "#\n"
    "# Example for NFSv4:\n"
    "# /srv/nfs4        gss/krb5i(rw,sync,fsid=0,crossmnt,no_subtree_check)\n"
    "# /srv/nfs4/homes  gss/krb5i(rw,sync,no_subtree_check)\n"
    "#\n"
)

#: ``handlers/main.yml``: the ``reload nfs`` handler's command.
EXPORTFS_COMMAND = "exportfs -ra"

#: The molecule converge playbook's pre_task refreshes the apt index with
#: ``cache_valid_time: 600`` before the role runs; the role itself does not.
#: Folded into the install request so the example works on a fresh host
#: (deviation D1 in ansible-role-nfs.md).
APT_CACHE_VALID_TIME = 600

#: The request names that queue ``reload nfs`` -- the template task's
#: ``notify:``. Both the content install and its metadata companion carry
#: the notification (the apache migration's ports.conf precedent): Ansible
#: reports the template task changed on a content *or* mode/owner drift.
NOTIFYING = frozenset({"Copy exports file", "Set exports file mode and owner"})

#: ``defaults/main.yml``: ``nfs_exports: []``. The ``nfs_rpcbind_state`` /
#: ``nfs_rpcbind_enabled`` defaults are consumed only by setup-RedHat.yml
#: and are out of scope.
ROLE_DEFAULTS: dict[str, Any] = {"nfs_exports": ()}


# --- pure helpers ------------------------------------------------------------



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

    Reads only static inventory data. ``nfs_exports`` is the empty tuple by
    default (the role's ``nfs_exports: []``); an override becomes a tuple of
    export lines. Type gates raise ``TypeError`` (ruff TRY004), value gates
    ``ValueError`` -- both before any host is contacted. Each line must
    begin with a directory path: the role's directory loop takes the first
    whitespace-separated token, and Ansible's ``map('first')`` on an empty
    split yields Undefined, failing the ``file`` task the same way.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}

    exports = merged["nfs_exports"]
    if not isinstance(exports, (list, tuple)):
        raise TypeError(f"{host.name}: nfs_exports must be a list of export lines")
    if not all(isinstance(export, str) and export.split() for export in exports):
        raise ValueError(
            f"{host.name}: nfs_exports must hold non-empty export lines "
            "beginning with a directory path"
        )

    return {"nfs_exports": tuple(exports)}


def render_exports(exports: tuple[str, ...]) -> str:
    """``templates/exports.j2``, pre-rendered control-side (deviation D2).

    The template is the static ``EXPORTS_HEADER`` comment block followed by
    ``{% for export in nfs_exports %}{{ export }}{% endfor %}`` -- with
    Ansible's ``trim_blocks=True`` the block tags' own newlines vanish, so
    the render is the header plus one newline-terminated line per export.
    A plain join reproduces it byte-for-byte (verified against Jinja2 with
    Ansible's environment: ``trim_blocks=True``, ``lstrip_blocks=False``,
    ``keep_trailing_newline=True``), so no Jinja2 dependency is needed.
    """
    return EXPORTS_HEADER + "".join(f"{export}\n" for export in exports)


def export_directories(exports: tuple[str, ...]) -> tuple[str, ...]:
    """The directory loop's items: ``nfs_exports | map('split') |
    map('first') | unique``.

    Jinja's ``split`` with no arguments splits on whitespace runs and
    ``unique`` preserves first-occurrence order -- ``str.split()`` and
    ``dict.fromkeys`` are the exact Python equivalents.
    """
    return tuple(dict.fromkeys(export.split()[0] for export in exports))


def nfs_plan(host: Host) -> dict[str, Any]:
    """Freeze every read the deploy needs: exports, directories, render, gate.

    Control-side work, performed once in ``main`` (gather -> snapshot ->
    factory, with no gather: every input is static inventory data). The
    ``service`` task's ``when: nfs_exports|length > 0`` freezes to a bool,
    and the template becomes a plain ``str`` (hashable, equal across
    phases).
    """
    exports = role_settings(host)["nfs_exports"]
    return {
        "exports": exports,
        "directories": export_directories(exports),
        "exports_content": render_exports(exports),
        "start_service": bool(exports),
    }


# --- the deploy ---------------------------------------------------------------


def build_nfs(
    plans: dict[str, dict[str, Any]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """The whole Ubuntu role: packages, export directories, exports, service.

    Every read the role takes mid-play was frozen into the plan
    control-side, so this deploy is a pure function of static input. The
    handler is *not* part of the deploy: it is a command (``exportfs -ra``),
    gathered as a ``Shell`` action between runs -- see ``reload_nfs``.
    """

    def nfs(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return

        # Deviation D1: the role's apt task carries no update_cache; its
        # molecule converge playbook refreshes the index in a pre_task
        # (update_cache=yes cache_valid_time=600). Folded in here so the
        # example is self-contained on a fresh host. The refresh is gated
        # on the AptUpdateStamp fact against the controller's UTC clock,
        # so a converged host with a fresh cache yields no update command.
        yield op(
            apt.packages,
            name="Ensure NFS utilities are installed",
            packages=NFS_PACKAGES,
            update=True,
            cache_valid_time=APT_CACHE_VALID_TIME,
            sudo=True,
            when=is_ubuntu,
        )

        # The directory loop, one request per unique export path. The role
        # sets no mode (its `file:` task carries ansible-lint's noqa 208
        # exemption deliberately), so mode is left unset: files.directory
        # creates a missing path with mkdir -p and leaves an existing
        # directory's permissions untouched -- Ansible's mode-less
        # `state: directory` exactly.
        for directory in plan["directories"]:
            yield op(
                files.directory,
                name=f"Ensure export directory {directory} exists",
                path=directory,
                sudo=True,
                when=is_ubuntu,
            )

        # The template task, pre-rendered control-side (recipe 4).
        # files.install's idempotence is content-only: a converged file
        # yields no commands even if its mode or owner drifted.
        yield op(
            files.install,
            name="Copy exports file",
            content=plan["exports_content"],
            dest=EXPORTS_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )
        # The paired files.file converges the mode:0644 owner:group root:root
        # the template task enforces on every run -- the apache migration's
        # ports.conf precedent. A no-op when the metadata already matches.
        # Both requests notify: Ansible's template task reports changed on
        # a content *or* metadata drift, and either queues `reload nfs`.
        yield op(
            files.file,
            name="Set exports file mode and owner",
            path=EXPORTS_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )

        # The service task's `when: nfs_exports|length > 0`, frozen into the
        # plan. running/enabled are fact-gated, so a converged host yields
        # no systemctl command.
        if plan["start_service"]:
            yield op(
                systemd.service,
                name="Ensure nfs is running",
                service=NFS_SERVER_DAEMON,
                running=True,
                enabled=True,
                sudo=True,
                when=is_ubuntu,
            )

    return nfs


# --- the handler (an action gather, not a deploy) ------------------------------


async def reload_nfs(targets: list[Host], changed: frozenset[str]) -> None:
    """The role's ``reload nfs`` handler: ``command: exportfs -ra``.

    A command handler has no factory in ``reemote.handlers`` -- those wrap
    ``systemd.service`` only -- and no operation can run a bare command
    inside a deploy. The faithful shape composes the two documented
    recipes: ``changed_hosts`` freezes the notified hosts between runs,
    then ``gather_action`` runs the handler's command on exactly those
    hosts, once each, after every notifying request has landed (Ansible's
    end-of-play flush). A converged re-run freezes an empty set and this
    function returns without contacting a host.

    ``reload_handler(changed, NFS_SERVER_DAEMON)`` would *not* be faithful:
    ``systemd.service(reloaded=True)`` reloads only an already-running
    unit -- a stopped one is started instead (the default ``running=True``)
    and the reload verb is skipped. On the role's default path
    (``nfs_exports: []``) the service-start task is skipped, so the handler
    would start the unit the gate left stopped and never run ``exportfs``
    -- yet Ansible's ``exportfs -ra`` still runs whenever the exports file
    changed. The command runs regardless of the unit's state, exactly as
    the role wrote it.

    gather_action closes its own hosts. A non-zero exit is data in the
    Result TypedDict (read by key), re-raised here as the typed
    ``CommandError`` so ``main`` reports it like any failed apply.
    """
    notified = [host for host in targets if host.name in changed]
    if not notified:
        return
    results = await gather_action(
        notified, Shell, command=EXPORTFS_COMMAND, sudo=True
    )
    for host in notified:
        result = results[host.name]
        if result["exit_status"] != 0:
            raise CommandError(
                host.name, EXPORTFS_COMMAND, result["exit_status"], result["stderr"]
            )


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
        targets = [host for host in hosts if is_ubuntu(host)]
        skipped = [host.name for host in hosts if not is_ubuntu(host)]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        if not targets:
            print("\nNo host in the Ubuntu family; nothing to deploy.")
            return

        # The role's variable gates and the frozen plan, before any host is
        # contacted. No fact gather: the vars split only by OS family (a
        # frozen constant on the Ubuntu path) and the template consumes
        # only the static nfs_exports list.
        plans = {host.name: nfs_plan(host) for host in targets}
        print("\n== NFS plan (defaults + host_vars, frozen) ==")
        for name, plan in plans.items():
            directories = ", ".join(plan["directories"]) or "(none)"
            print(
                f"{name}: exports={len(plan['exports'])} "
                f"directories={directories} "
                f"service={'start+enable' if plan['start_service'] else 'skipped'}"
            )

        report = await execute(
            inventory,
            build_nfs(plans),
            "Deploy: NFS (geerlingguy.nfs, Ubuntu path)",
        )

        # The handler: the notification read between runs, then the command
        # gather on the notified hosts only. A dry preview of the notifying
        # deploy cannot know whether the handler will fire (Ansible check
        # mode skips handlers for the same reason); the frozen set makes
        # the real gather honest.
        changed = changed_hosts(report, NOTIFYING)
        print("\n== Handler notification set (reload nfs: exportfs -ra) ==")
        print(sorted(changed) or "(none)")
        await reload_nfs(targets, changed)

        changed_count = sum(1 for entry in report.log if entry.changed)
        print(
            f"\n== Done: {changed_count} changed request(s), "
            f"{len(changed)} host(s) re-exported =="
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, or a role variable
        # is invalid. No host mutation is implied by the variable gates, so
        # the message is the whole report.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema; the
        # message names the offending entry by its list index.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Every host-level failure is a typed error naming the host and the
        # failing command (the handler's exportfs included). Print that, no
        # traceback, and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
