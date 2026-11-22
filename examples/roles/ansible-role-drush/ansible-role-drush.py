# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the Drush Ansible role (``geerlingguy.drush``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-drush`` installs
Drush, the Drupal command-line shell. This program is its **Ubuntu default
(launcher) path** expressed as one reemote deploy: the old-symlink cleanup, the
``/usr/local/bin`` directory, the Drush launcher phar download and the 0755
executable bit. Every role variable is read from the inventory entry's extra
keys (reemote's ``host_vars`` mechanism), so ``drush_launcher_version`` and
friends live beside ``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the other
hosts in the shared inventory are not even contacted. The role's two opt-in
branches (``drush_composer_global_install`` and ``drush_install_from_source``,
both default-false) are out of scope (a Phase 0 decision), so the launcher's
five tasks are the whole migration.

The implementation gap this migration found is now CLOSED on main
-----------------------------------------------------------------

**issue #23** (``files: no binary spool-and-install to a root-owned path``) --
closed by commits ``134a122`` + ``da8a623``. The role's
``get_url: url=<phar> dest=/usr/local/bin/drush`` task downloads a *binary*
(the launcher phar is not valid UTF-8) into a root-owned directory. That is now
ONE in-deploy ``files.download`` request: the URL is fetched control-side as
bytes, a non-UTF-8 body installs through ``files.install(content=<bytes>)`` --
an unprivileged scp spool to ``/tmp`` plus an elevated ``cp``/``chmod`` -- and
the install is sha1-idempotent (``RemoteFileSha1`` read-back). The former
two-deploy workaround (a ``files.put`` spool deploy, then an elevated
``files.move`` into place) is gone; no hand-rolled fetch helper, no spool
directory and no between-deploy bridge remain.

Why ONE deploy suffices
-----------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role's
``stat:`` + ``register: drush_path_state`` + ``when: …islnk`` construct reads
mutable target state a deploy body may not branch on, so it becomes a
**gather -> snapshot -> factory** step: ``main()`` gathers ``FileLink`` (the
symlink target, or None) and ``File`` (regular-file metadata, False for a
non-file, None when absent) on the launcher path, freezes the two booleans
into the per-host plan, and ``build_launcher`` branches on the frozen plan
only. The content gate needs no snapshot at all -- ``files.download`` resolves
it in-operation through the sha1 read-back.

One caveat the fix documents: the binary sha1 read-back is **unprivileged**
(``sha1sum`` as the login user), so a login-unreadable destination would read
as absent and re-install on every deploy. The launcher's 0755 mode keeps it
login-readable -- give any binary install a login-readable mode.

Deliberate deviations from Ansible semantics
--------------------------------------------

Recorded at the point of use and in ``ansible-role-drush.md``:

* the phar is fetched **control-side** (``files.download``) and spooled to the
  host, where ``get_url`` downloads on the target itself. The control machine
  needs internet egress to ``github.com``; the host never downloads anything.
* ``get_url`` with the default ``force: no`` skips an *existing* destination
  regardless of content; ``files.download`` is sha1-idempotent -- equal bytes
  no-op, a drifted binary converges to the release content once.
* the 0755 mode is applied **at install time** (``files.download(mode="0755")``)
  rather than by a separate chmod after a default-mode download. The role's
  final "Ensure Drush is executable." task is kept as a ``files.file(mode=…)``
  metadata-convergence request for an already-present destination, because
  install/download idempotence is content-only and never compares mode.
* no checksum pin: the role sets none, and GitHub release assets are
  immutable. The request sequence is deterministic without a pin; a pin would
  only guard against the URL body changing mid-run.
* the symlink cleanup uses ``force=True`` -- the ``File`` fact reads a symlink
  as a non-file, so ``files.file(present=False)`` would raise without it.
* every request carries ``sudo=True`` explicitly (the role relies on
  play-level ``become``); run-as users/passwords come from the inventory
  ``privilege`` section, never from an operation.

reemote is the only control-side dependency (the fetch uses the standard
library). The target needs PHP to *run* drush -- the launcher is a phar that
executes ``php`` -- but this program does not install PHP (the role does not
either; its README lists PHP as a prerequisite).

Run it with::

    uv run python examples/roles/ansible-role-drush/ansible-role-drush.py
"""

import asyncio
import json
import posixpath
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    File,
    FileLink,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# --- role constants (defaults/main.yml, launcher path only) ------------------

#: The role defaults this migration honours (``defaults/main.yml``). The
#: ``drush_launcher_`` prefix is kept so an inventory entry reads like the
#: role's ``host_vars``. ``drush_launcher_phar_url`` defaults to None -- the
#: role's default is a Jinja2 interpolation of ``drush_launcher_version``, so
#: ``resolved_vars`` rebuilds it from the (possibly overridden) version unless
#: the user pins an explicit URL.
ROLE_DEFAULTS: dict[str, Any] = {
    "drush_launcher_install": True,
    "drush_launcher_version": "0.10.1",
    "drush_launcher_phar_url": None,
    "drush_launcher_path": "/usr/local/bin/drush",
}


# --- pure helpers ------------------------------------------------------------



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data and stays a stable module-level function,
    so both deploy phases reach the same eligibility decision. A skipped request
    is never materialized, so the Alpine and CentOS hosts in the shared
    inventory are not contacted at all.
    """
    return host.family == "Ubuntu"


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


def role_settings(host: Host) -> dict[str, Any]:
    """Resolve a host's role variables: defaults overlaid with ``host.extra``.

    Validates the flag and the paths before any host is contacted, so a bad
    value fails fast with a clear message rather than mid-deploy.
    """
    settings = {**ROLE_DEFAULTS, **host.extra}
    settings["drush_launcher_install"] = _ansible_bool(
        settings["drush_launcher_install"], "drush_launcher_install"
    )
    for key in ("drush_launcher_version", "drush_launcher_path"):
        if not isinstance(settings[key], str) or not settings[key]:
            raise ValueError(f"{key} must be a non-empty string, got {settings[key]!r}")
    if not settings["drush_launcher_path"].startswith("/"):
        raise ValueError(
            "drush_launcher_path must be absolute, got "
            f"{settings['drush_launcher_path']!r}"
        )
    url = settings["drush_launcher_phar_url"]
    if url is not None and (not isinstance(url, str) or not url):
        raise ValueError(
            f"drush_launcher_phar_url must be a non-empty string or null, got {url!r}"
        )
    return settings


def resolved_vars(settings: dict[str, Any]) -> dict[str, Any]:
    """The launcher plan's static half: version, URL, path and its directory.

    The role's ``drush_launcher_phar_url`` default interpolates
    ``drush_launcher_version``; an explicit user URL wins over the version
    (Ansible's variable precedence). The directory is the role's
    ``drush_launcher_path | dirname`` filter. Everything here is a hashable
    scalar, frozen control-side before the deploy.
    """
    version = settings["drush_launcher_version"]
    path = settings["drush_launcher_path"]
    return {
        "launcher_install": settings["drush_launcher_install"],
        "version": version,
        "phar_url": settings["drush_launcher_phar_url"]
        or (
            "https://github.com/drush-ops/drush-launcher/releases/download/"
            f"{version}/drush.phar"
        ),
        "path": path,
        "path_dir": posixpath.dirname(path),
    }


# --- the deploy factory (closes over the frozen per-host plans) ---------------


def build_launcher(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """The one deploy: the role's five launcher tasks, in role order.

    Each plan freezes the role's ``stat`` register (``is_symlink`` from
    ``FileLink``, ``dest_exists`` from ``File``) beside the resolved variables,
    so the deploy branches on frozen data only -- the gather -> snapshot ->
    factory recipe for ``register`` + ``when``. A host without a plan (not
    Ubuntu, or ``drush_launcher_install`` false) yields nothing.

    ``files.download`` (issue #23, closed) is the ``get_url`` task as one
    request: the binary phar is fetched control-side, spooled unprivileged and
    installed elevated, sha1-idempotent. The final ``files.file(mode="0755")``
    is the role's "Ensure Drush is executable." task -- it converges a drifted
    mode on an already-present destination, which the content-only install
    gate would never fix.
    """

    def launcher(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        if plan["is_symlink"]:
            # The File fact reads a symlink as a non-file, so force=True takes
            # the remove-the-link branch (Ansible's state: absent on a link).
            yield op(
                files.file,
                name="Perform cleanup of old symlink.",
                path=plan["path"],
                present=False,
                force=True,
                sudo=True,
                when=is_ubuntu,
            )
        yield op(
            files.directory,
            name="Ensure Drush path directory exists.",
            path=plan["path_dir"],
            mode="0755",
            sudo=True,
            when=is_ubuntu,
        )
        # 0755 at install time: the binary sha1 read-back is unprivileged, so
        # the launcher must stay login-readable for the no-op gate to work.
        yield op(
            files.download,
            name="Install Drush.",
            url=plan["phar_url"],
            dest=plan["path"],
            mode="0755",
            sudo=True,
            when=is_ubuntu,
        )
        if plan["dest_exists"]:
            # Metadata convergence for a pre-existing regular file. (A symlink
            # reads as a non-file, so dest_exists is False on that branch.)
            yield op(
                files.file,
                name="Ensure Drush is executable.",
                path=plan["path"],
                mode="0755",
                sudo=True,
                when=is_ubuntu,
            )

    return launcher


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host. Both
    runs use the very same deploy object -- for a factory-built deploy that means
    the same frozen snapshot, which is what makes the determinism guard hold.
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

        # The role's variable resolution, before any host is contacted.
        resolved = {host.name: resolved_vars(role_settings(host)) for host in targets}
        active = [host for host in targets if resolved[host.name]["launcher_install"]]
        disabled = [
            host.name for host in targets if not resolved[host.name]["launcher_install"]
        ]
        if disabled:
            print(
                "\n== Skipped (drush_launcher_install false): "
                + ", ".join(disabled)
                + " =="
            )
        if not active:
            print("\nNo host enables drush_launcher_install; nothing to deploy.")
            return

        # The role's stat register, frozen: FileLink answers the islnk gate,
        # File answers whether a regular file already sits at the path (its
        # metadata is what the executable-bit task converges). gather_fact
        # does not close its hosts, so the finally does.
        plans: dict[str, Any] = {}
        try:
            for host in active:
                rv = resolved[host.name]
                link = (await gather_fact([host], FileLink, rv["path"]))[host.name]
                info = (await gather_fact([host], File, rv["path"]))[host.name]
                plans[host.name] = {
                    **rv,
                    "is_symlink": link is not None,
                    "dest_exists": isinstance(info, dict),
                }
        finally:
            for host in active:
                await host.aclose()
        print("\n== Frozen launcher plans ==")
        for host in active:
            plan = plans[host.name]
            state = (
                "symlink, will clean up"
                if plan["is_symlink"]
                else "present"
                if plan["dest_exists"]
                else "absent"
            )
            print(
                f"{host.name}: drush {plan['version']} -> {plan['path']} ({state})"
            )

        # The one deploy. On a converged host every request is a no-op: the
        # directory matches, the phar's sha1 matches, the mode matches.
        report = await execute(
            inventory, build_launcher(plans), "Deploy: install the Drush launcher"
        )
        changed = sum(1 for entry in report.log if entry.changed)
        print(f"\n== Done: {changed} changed request(s) ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, the control-side phar
        # fetch failed (URLError/HTTPError are OSError subclasses), or a role
        # variable is invalid. The message is the whole report.
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
