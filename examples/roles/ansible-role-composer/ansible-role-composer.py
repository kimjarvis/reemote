# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the Composer Ansible role (``geerlingguy.composer``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-composer`` installs
Composer, the PHP dependency manager. This program is its **Ubuntu default
path** expressed as three reemote deploys with two action barriers between
them: the signature-verified installer download, the installer run, the phar
move into ``/usr/local/bin/composer``, the ``~/.composer`` home directory, the
optional GitHub OAuth ``auth.json``, the globally-required packages and the
``/etc/profile.d/composer.sh`` PATH entry. Every role variable is read from the
inventory entry's extra keys (reemote's ``host_vars`` mechanism), so
``composer_path`` and friends live beside ``host``/``connection`` in
``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the other
hosts in the shared inventory are not even contacted.

Implementation gaps: none open
------------------------------

Every task on the default path maps to a covered operation or a documented
recipe. The three library gaps this role's shape once hit are all CLOSED on
main and used directly, with no workaround left in the code:

* **issue #20** (``uri`` with ``return_content: true``) -- the installer
  signature is fetched control-side by ``files.fetch_url`` in ``main()`` and
  frozen into the plans (the fetch-once-then-freeze recipe).
* **issue #22** (remote-copy parity) -- the ``mv /tmp/composer.phar
  {{ composer_path }}`` task is ``files.copy`` (rename form, mode 0755),
  paired with a ``files.file(present=False)`` cleanup because copy keeps the
  source in place where ``mv`` consumes it (D5).
* **issue #23** (binary spool-and-install) -- ``files.download`` carries the
  installer with the role's ``sha384:`` checksum pin.

Program shape: three deploys, two action barriers
-------------------------------------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence, so every
mutable read moves out of the deploy bodies::

    main()
     ├─ load inventory; resolve role_settings per Ubuntu target (fail fast)
     ├─ gather FileExists(composer_path)      the stat register's exists gate
     │   gather server.Home(home_owner)       tilde-expansion of ~/.composer
     │   gather FileExists(home/vendor/name)  the global require creates= gate
     ├─ [any host needs install] files.fetch_url(installer.sig)  (#20)
     │   → frozen plan per host
     ├─ deploy 1: build_download   files.download(installer, sha384 pin)
     ├─ ACTION: Shell installer run (cd /tmp && php composer-installer.php)
     ├─ deploy 2: build_configure  files.copy phar → composer_path + cleanup,
     │                             files.directory(home), auth.json install
     ├─ ACTION: Shell composer global require (env COMPOSER_HOME, sudo)
     └─ deploy 3: build_profile    /etc/profile.d/composer.sh install

The barriers are load-bearing: deploy 2's ``files.copy`` validates its source
in *both* phases, so the installer action must have produced
``/tmp/composer.phar`` before deploy 2 begins; the global require actions need
the composer binary deploy 2 installed. The ``command:`` tasks never enter a
deploy (``ActionInDeployError``); their exit status is data on the returned
``Result``, interpreted here and raised as ``CommandError`` on failure.

Deliberate deviations from Ansible semantics
--------------------------------------------

Recorded at the point of use and in ``docs/examples/roles/ansible-role-composer.md``:

* **D1** -- the signature and the installer are fetched **control-side**
  (``files.fetch_url`` / ``files.download``); ``uri``/``get_url`` fetch on the
  target. The control machine needs egress to ``composer.github.io`` and
  ``getcomposer.org``; the host never downloads anything.
* **D2** -- every elevated request carries ``sudo=True`` explicitly; the
  role's ``become_user: {{ composer_home_owner }}`` maps to the inventory
  ``privilege`` section (a non-root ``composer_home_owner`` needs
  ``privilege.sudo_user``), never to an operation argument.
* **D3** -- the demo default for ``composer_global_packages`` mirrors the
  role's molecule converge (``phpunit/phpunit @stable``) so the global-require
  path is exercised; the role's own default is ``[]`` (set it to disable).
* **D4** -- the signature is fetched **once** control-side, gated on any host
  needing the install, and frozen into every plan; Ansible fetches per host.
* **D5** -- ``mv`` becomes ``files.copy`` + a paired removal: copy is
  sha1-idempotent and keeps the source, so the cleanup request reproduces
  mv's source consumption (issue #22, closed).
* **D6** -- the installer download and run are **unprivileged**: the role's
  ``get_url``/``command`` tasks carry no ``become`` and write only to /tmp.
* **D7** -- the two opt-in branches are scoped out (a Phase 0 decision):
  ``composer_keep_updated`` (self-update is stateless and never converges)
  and ``composer_add_project_to_path``. Setting either true fails fast.
* **D8** -- the templates are rendered control-side with ``json.dumps`` /
  an f-string; the profile entry uses the *resolved absolute* home path
  where the Jinja2 template interpolates the raw ``~/.composer``.

Version pinning follows the role's own precedence: ``composer_version_branch``
(default ``--2``) wins; a ``composer_version`` pin applies only when the branch
is set to ``""`` (the installer task's ``{% if %}…{% elif %}``).

reemote is the only control-side dependency. The target needs PHP (>= 5.4)
and git to *use* Composer -- the role does not install them either (its README
lists them as prerequisites); this program does not install them.

Run it with::

    uv run python examples/roles/ansible-role-composer/ansible-role-composer.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FileExists,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    Shell,
    files,
    format_log,
    format_plan,
    gather_action,
    gather_fact,
    op,
    run,
    server,
    validate_inventory,
)

# --- role constants (defaults/main.yml, default path only) --------------------

#: examples/roles/ansible-role-composer/ansible-role-composer.py -> project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

#: The installer signature and installer URLs (hardcoded in tasks/main.yml).
SIGNATURE_URL = "https://composer.github.io/installer.sig"
INSTALLER_URL = "https://getcomposer.org/installer"
#: The installer's /tmp staging paths (tasks/main.yml) and the profile entry.
INSTALLER_PATH = "/tmp/composer-installer.php"
PHAR_PATH = "/tmp/composer.phar"
PROFILE_PATH = "/etc/profile.d/composer.sh"

#: The role defaults this migration honours (``defaults/main.yml``). The
#: ``composer_`` prefix is kept so an inventory entry reads like the role's
#: ``host_vars``. D3: ``composer_global_packages`` mirrors the molecule
#: converge (the role's own default is ``[]``) so the global-require path is
#: demonstrated; set it to ``[]`` in the inventory to disable.
#: ``php_executable`` is the role's ``set_fact`` default (task 2).
ROLE_DEFAULTS: dict[str, Any] = {
    "composer_path": "/usr/local/bin/composer",
    "composer_keep_updated": False,
    "composer_version": "",
    "composer_version_branch": "--2",
    "composer_home_path": "~/.composer",
    "composer_home_owner": "root",
    "composer_home_group": "root",
    "composer_global_packages": (("phpunit/phpunit", "@stable"),),
    "composer_add_to_path": True,
    "composer_add_project_to_path": False,
    "composer_github_oauth_token": "",
    "php_executable": "php",
}


# --- pure helpers --------------------------------------------------------------


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of entries, each carrying at least ``host``
    and ``connection``; every other key is preserved and reaches the deploy as
    ``host.extra`` -- the role variables live there.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


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


def _global_packages(value: Any) -> tuple[tuple[str, str], ...]:
    """Normalize ``composer_global_packages`` to frozen (name, release) pairs.

    Ansible's loop is over a list of dicts (``{name: …, release: …}``); reemote
    op arguments must be hashable, so the dicts flatten to tuples of scalars.
    Both the JSON shape (list of dicts) and an already-flattened pair sequence
    are accepted; ``release`` defaults to ``@stable`` exactly as the role's
    ``item.release | default('@stable')``.
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError(
            "composer_global_packages must be a list of {name, release} objects, "
            f"got {value!r}"
        )
    pairs: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
            release = item.get("release", "@stable")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            name, release = item
        else:
            raise TypeError(
                "composer_global_packages items must be {name, release} objects "
                f"or (name, release) pairs, got {item!r}"
            )
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"composer_global_packages name must be a non-empty string, got {name!r}"
            )
        if not isinstance(release, str) or not release:
            raise ValueError(
                "composer_global_packages release must be a non-empty string, "
                f"got {release!r}"
            )
        pairs.append((name, release))
    return tuple(pairs)


def role_settings(host: Host) -> dict[str, Any]:
    """Resolve a host's role variables: defaults overlaid with ``host.extra``.

    Validates every value before any host is contacted, so a bad setting fails
    fast with a clear message rather than mid-deploy. The role's first task
    (``fail:`` when ``composer_version != '' and composer_keep_updated``) is
    subsumed: D7 scopes ``composer_keep_updated`` out entirely, so any true
    value raises here -- with the role's own message when a version is also
    pinned.
    """
    settings = {**ROLE_DEFAULTS, **host.extra}
    settings["composer_keep_updated"] = _ansible_bool(
        settings["composer_keep_updated"], "composer_keep_updated"
    )
    settings["composer_add_to_path"] = _ansible_bool(
        settings["composer_add_to_path"], "composer_add_to_path"
    )
    settings["composer_add_project_to_path"] = _ansible_bool(
        settings["composer_add_project_to_path"], "composer_add_project_to_path"
    )
    if settings["composer_keep_updated"]:
        # D7: the self-update branch is opt-in (default false) and scoped out;
        # its changed_when ('Updating to version' in stdout) never converges.
        if settings["composer_version"]:
            raise ValueError(
                "You cannot request a specific version AND keep the composer up "
                "to date. Set either composer_version or composer_keep_updated, "
                "but not both."
            )
        raise ValueError(
            "composer_keep_updated is scoped out of this migration (the role's "
            "self-update branch is opt-in and stateless); leave it false"
        )
    if settings["composer_add_project_to_path"]:
        # D7: the project-bin.yml branch is opt-in (default false), scoped out.
        raise ValueError(
            "composer_add_project_to_path is scoped out of this migration (the "
            "role's project-bin branch is opt-in); leave it false"
        )
    for key in (
        "composer_path",
        "composer_home_owner",
        "composer_home_group",
        "php_executable",
    ):
        if not isinstance(settings[key], str) or not settings[key]:
            raise ValueError(f"{key} must be a non-empty string, got {settings[key]!r}")
    if not settings["composer_path"].startswith("/"):
        raise ValueError(
            f"composer_path must be absolute, got {settings['composer_path']!r}"
        )
    for key in (
        "composer_version",
        "composer_version_branch",
        "composer_github_oauth_token",
    ):
        if not isinstance(settings[key], str):
            raise TypeError(f"{key} must be a string, got {settings[key]!r}")
    home_path = settings["composer_home_path"]
    if not isinstance(home_path, str) or not home_path:
        raise ValueError(f"composer_home_path must be a non-empty string, got {home_path!r}")
    if not home_path.startswith(("/", "~")):
        raise ValueError(
            "composer_home_path must be absolute or start with '~', got "
            f"{home_path!r}"
        )
    settings["composer_global_packages"] = _global_packages(
        settings["composer_global_packages"]
    )
    return settings


def resolved_vars(settings: dict[str, Any]) -> dict[str, Any]:
    """The plan's static half: everything decidable before a host is contacted.

    The installer argument follows the role's ``{% if composer_version_branch
    %}…{% elif composer_version %}--version=…{% endif %}`` precedence: the
    branch (default ``--2``) wins; a version pin applies only when the branch
    is explicitly empty. ``auth.json`` is rendered here (the role's template is
    a plain JSON object with 4-space indent); the profile entry needs the
    *resolved* home and is rendered by ``freeze_plan``.
    """
    branch = settings["composer_version_branch"]
    version = settings["composer_version"]
    token = settings["composer_github_oauth_token"]
    return {
        "composer_path": settings["composer_path"],
        "php": settings["php_executable"],
        "installer_arg": branch or (f"--version={version}" if version else ""),
        "home_path": settings["composer_home_path"],
        "home_owner": settings["composer_home_owner"],
        "home_group": settings["composer_home_group"],
        "packages": settings["composer_global_packages"],
        "add_to_path": settings["composer_add_to_path"],
        "auth_content": (
            json.dumps({"github-oauth": {"github.com": token}}, indent=4) + "\n"
            if token
            else None
        ),
    }


def resolve_home(home_path: str, home_base: str | None) -> str:
    """Expand ``composer_home_path`` against the owner's gathered home.

    The role's ``~/.composer`` is tilde-expanded by Ansible in the
    ``become_user``'s context (root by default); reemote gathers
    ``server.Home(home_owner)`` and expands control-side. An absolute override
    is used as-is; an unknown owner (``Home`` returned None) fails fast rather
    than inventing a path.
    """
    if home_path.startswith("/"):
        return home_path
    if home_base is None:
        raise ValueError(
            f"cannot resolve composer_home_path {home_path!r}: the home "
            "directory of composer_home_owner is unknown on the target"
        )
    if home_path == "~":
        return home_base
    return home_base + home_path[1:]


def installer_command(plan: dict[str, Any]) -> str:
    """The role's installer ``command:`` with ``chdir=/tmp`` inlined.

    reemote's ``Shell`` action has no ``chdir`` parameter, so the working
    directory becomes a ``cd /tmp &&`` prefix (the recipe for ``command:``
    kwargs the action surface does not carry).
    """
    command = f"cd /tmp && {plan['php']} composer-installer.php"
    if plan["installer_arg"]:
        command += f" {plan['installer_arg']}"
    return command


def require_command(composer_path: str, name: str, release: str) -> str:
    """The role's per-package ``composer global require`` command text."""
    return (
        f"{composer_path} global require {name}:{release} "
        "--no-progress --no-interaction"
    )


def freeze_plan(
    rv: dict[str, Any],
    *,
    need_install: bool,
    home: str,
    signature: str,
    require: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    """Assemble one host's frozen plan from its resolved vars and gathers.

    ``need_install`` is the stat register's exists gate, ``home`` the resolved
    composer home, ``signature`` the control-side installer signature (D4,
    empty when no host needs the install) and ``require`` the packages whose
    ``creates=`` vendor directory is still absent. The profile entry (D8) is
    rendered only when the role would reach it: global packages configured
    (the include gate) and ``composer_add_to_path`` true.
    """
    return {
        "composer_path": rv["composer_path"],
        "php": rv["php"],
        "installer_arg": rv["installer_arg"],
        "need_install": need_install,
        "signature": signature,
        "home": home,
        "home_owner": rv["home_owner"],
        "home_group": rv["home_group"],
        "auth_content": rv["auth_content"],
        "profile_content": (
            f"export PATH=$PATH:{home}/vendor/bin\n"
            if rv["packages"] and rv["add_to_path"]
            else None
        ),
        "require": require,
    }


# --- the deploy factories (close over the frozen per-host plans) ---------------


def build_download(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 1: the role's "Download Composer installer." task.

    ``files.download`` (issues #20/#23, closed) fetches the installer
    control-side, verifies the role's ``sha384:`` checksum pin against the
    frozen signature and spools it to ``/tmp/composer-installer.php`` at 0755.
    The pin is what makes the two-phase fetch deterministic: both phases
    verify the same digest, so both install byte-identical content. D6: no
    sudo -- the role's get_url task carries no become and /tmp is
    login-writable.
    """

    def download(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None or not plan["need_install"]:
            return
        yield op(
            files.download,
            name="Download Composer installer.",
            url=INSTALLER_URL,
            dest=INSTALLER_PATH,
            mode="0755",
            checksum=f"sha384:{plan['signature']}",
            when=is_ubuntu,
        )

    return download


def build_configure(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2: the phar move, the composer home and the OAuth token.

    Runs after the installer action barrier, so ``files.copy``'s source
    (``/tmp/composer.phar``) exists in both phases. D5: the role's ``mv``
    becomes copy + a paired removal -- copy (issue #22, closed) renames into
    place at 0755 sha1-idempotently but keeps the source, so the cleanup
    request reproduces mv's source consumption. ``overwrite=True`` is safe:
    the frozen exists gate means the destination was absent at gather time.

    The home directory and auth.json run for every target (the role's tasks
    are not gated on the stat register). D2: ``become_user`` maps to the
    inventory privilege section; the owner/group converge as operation
    arguments.
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        if plan["need_install"]:
            yield op(
                files.copy,
                name="Move Composer into globally-accessible location.",
                src=PHAR_PATH,
                dest=plan["composer_path"],
                mode="0755",
                overwrite=True,
                sudo=True,
                when=is_ubuntu,
            )
            # D5: mv consumes its source; files.copy does not. The paired
            # removal keeps /tmp clean and the second pass a no-op.
            yield op(
                files.file,
                name="Remove the staged composer.phar from /tmp.",
                path=PHAR_PATH,
                present=False,
                sudo=True,
                when=is_ubuntu,
            )
        yield op(
            files.directory,
            name="Ensure composer directory exists.",
            path=plan["home"],
            mode="0755",
            user=plan["home_owner"],
            group=plan["home_group"],
            sudo=True,
            when=is_ubuntu,
        )
        if plan["auth_content"] is not None:
            yield op(
                files.install,
                name="Add GitHub OAuth token for Composer.",
                content=plan["auth_content"],
                dest=f"{plan['home']}/auth.json",
                mode="0644",
                user=plan["home_owner"],
                group=plan["home_group"],
                sudo=True,
                when=is_ubuntu,
            )

    return configure


def build_profile(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 3: the role's "Add composer_home_path bin directory to $PATH."

    Runs after the global-require action barrier, faithful to the role's
    ordering (the template task lives in global-require.yml, after the
    require loop). The content is the pre-rendered ``composer.sh.j2`` (D8);
    ``files.install`` is content-idempotent, so a converged entry is a no-op.
    """

    def profile(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None or plan["profile_content"] is None:
            return
        yield op(
            files.install,
            name="Add composer home bin directory to global $PATH.",
            content=plan["profile_content"],
            dest=PROFILE_PATH,
            mode="0644",
            sudo=True,
            when=is_ubuntu,
        )

    return profile


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

        # The role's variable resolution, before any host is contacted.
        resolved = {host.name: resolved_vars(role_settings(host)) for host in targets}

        # The role's mutable reads, frozen: FileExists(composer_path) is the
        # stat register's exists gate, server.Home resolves the owner's ~,
        # FileExists(home/vendor/name) is the global require creates= gate.
        # gather_fact does not close its hosts, so the finally does.
        gathered: dict[str, Any] = {}
        try:
            for host in targets:
                rv = resolved[host.name]
                exists = (await gather_fact([host], FileExists, rv["composer_path"]))[
                    host.name
                ]
                home_base = (
                    await gather_fact([host], server.Home, user=rv["home_owner"])
                )[host.name]
                home = resolve_home(rv["home_path"], home_base)
                missing: list[tuple[str, str]] = []
                for name, release in rv["packages"]:
                    vendor = (
                        await gather_fact([host], FileExists, f"{home}/vendor/{name}")
                    )[host.name]
                    if not vendor:
                        missing.append((name, release))
                gathered[host.name] = (not exists, home, tuple(missing))
        finally:
            for host in targets:
                await host.aclose()

        # The role's uri task (#20, closed): one control-side signature fetch,
        # gated on any host needing the install (D4), frozen into every plan.
        signature = ""
        if any(need for need, _home, _require in gathered.values()):
            signature = (await files.fetch_url(SIGNATURE_URL)).strip()
        plans = {
            host.name: freeze_plan(
                resolved[host.name],
                need_install=gathered[host.name][0],
                home=gathered[host.name][1],
                signature=signature,
                require=gathered[host.name][2],
            )
            for host in targets
        }
        print("\n== Frozen composer plans ==")
        for host in targets:
            plan = plans[host.name]
            state = "absent, will install" if plan["need_install"] else "present"
            print(
                f"{host.name}: composer -> {plan['composer_path']} ({state}), "
                f"home {plan['home']}, {len(plan['require'])} package(s) to require"
            )

        reports = []
        if any(plan["need_install"] for plan in plans.values()):
            reports.append(
                await execute(
                    inventory, build_download(plans), "Deploy 1: Composer installer"
                )
            )
            # The role's "Run Composer installer." command task: a Shell
            # action between the deploys (never inside one). D6: unprivileged,
            # like the role task. The Result's exit status is data -- raise
            # CommandError on failure so a broken installer never silently
            # feeds deploy 2.
            for host in targets:
                plan = plans[host.name]
                if not plan["need_install"]:
                    continue
                command = installer_command(plan)
                result = (await gather_action([host], Shell, command=command))[
                    host.name
                ]
                if result["exit_status"] != 0:
                    raise CommandError(
                        host.name, command, result["exit_status"], result["stderr"]
                    )
                print(f"\n{host.name}: Composer installer ran ({command})")
        else:
            print("\nComposer already installed on every target; no installer run.")

        reports.append(
            await execute(
                inventory,
                build_configure(plans),
                "Deploy 2: install Composer, configure its home",
            )
        )

        # The role's global-require loop: one Shell action per missing
        # package, with the role's environment (COMPOSER_HOME) and become.
        # The creates= gate was frozen above, so a converged package never
        # re-runs.
        for host in targets:
            plan = plans[host.name]
            for name, release in plan["require"]:
                command = require_command(plan["composer_path"], name, release)
                result = (
                    await gather_action(
                        [host],
                        Shell,
                        command=command,
                        env={"COMPOSER_HOME": plan["home"]},
                        sudo=True,
                    )
                )[host.name]
                if result["exit_status"] != 0:
                    raise CommandError(
                        host.name, command, result["exit_status"], result["stderr"]
                    )
                print(f"\n{host.name}: globally required {name}:{release}")

        if any(plan["profile_content"] is not None for plan in plans.values()):
            reports.append(
                await execute(
                    inventory, build_profile(plans), "Deploy 3: composer PATH entry"
                )
            )

        changed = sum(
            1 for report in reports for entry in report.log if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, a control-side fetch
        # failed (URLError/HTTPError are OSError subclasses), or a role
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