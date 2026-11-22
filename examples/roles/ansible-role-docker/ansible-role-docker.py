# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the Docker Ansible role (``geerlingguy.docker``) to reemote.

The role at ``https://github.com/geerlingguy/ansible-role-docker`` installs
and configures Docker CE from the official Docker apt repository. This
program is its **Ubuntu default path** expressed as a reemote deploy: the
legacy key/source/package cleanup, the repository dependencies, the Docker
signing key and deb822 ``.sources`` file, the Docker packages (with the
compose and buildx plugins), the optional ``/etc/docker/daemon.json`` and
``docker.service`` ExecStart override, the service's running/enabled state,
the role's ``restart docker`` handler, and the ``docker_users`` group
memberships. Every role variable is read from the inventory entry's extra
keys (reemote's ``host_vars`` mechanism), so ``docker_users`` and friends
live beside ``host``/``connection`` in ``inventory.json``.

Target: hosts in the ``"Ubuntu"`` OS family. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the
other hosts in the shared inventory are not even contacted. The role's
RedHat and Suse branches are out of scope (a Phase 0 single-OS decision),
so the ``include_vars`` OS dispatch collapses to frozen Debian constants
below, and the standalone docker-compose binary install
(``docker_install_compose``, default false) is scoped out: requesting it
raises a clear error instead of silently dropping the feature.

Why the program is a *sequence* of deploys rather than one
-------------------------------------------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
feeds ``ansible_facts.distribution_release`` into the repository's ``Suites``
and gates the user tasks on a ``getent`` read -- mutable reads a single
deploy may not branch on. Each becomes a **gather -> snapshot -> factory**
step (or a gate between runs), and the role's two ``meta: flush_handlers``
become deploy barriers:

1. ``gather_fact(LinuxDistribution)`` -- the Ubuntu codename for ``Suites``
2. ``build_setup(codenames)``  -- cleanup, dependencies, key, deb822 repo;
   the repo's ``notify: apt update`` + flush is ``update_cache=True``
3. ``deploy_packages``         -- Docker + compose plugin (branch-free)
4. ``deploy_configure``        -- daemon.json, ExecStart drop-in, service
5. ``service_handler``         -- the ``restart docker`` handler, fired by
   ``changed_hosts`` over the notifying names (the second flush_handlers)
6. ``gather_fact(Groups)``     -- the getent gate: the docker group must
   exist before ``deploy_users`` appends memberships

Implementation gaps
-------------------

One genuine library gap was proven while mapping the role: GAP-1 (#17) --
``apt.sources_file(signed_by=...)`` accepted an existing keyring *path*
only, while Ansible's ``deb822_repository`` ``signed_by`` also accepts a
URL and downloads the key itself. Issue #17 closed that gap: the URL form
now fetches the ASCII-armored key control-side and installs the repo-named
``/etc/apt/keyrings/<filename>.asc`` keyring itself, so this program passes
``signed_by`` the key URL directly -- the former ``files.download``
workaround and its key-removal request are gone. See
``ansible-role-docker.md`` for the full coverage table and every
deliberate deviation.

Prerequisites: none beyond reemote itself. The role's ``to_nice_json``
filter maps to the standard library's ``json.dumps(indent=4,
sort_keys=True)`` -- no PyYAML or Jinja2 is needed.

Run it with::

    uv run python examples/roles/ansible-role-docker/ansible-role-docker.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Groups,
    Host,
    HostConnectionError,
    InventoryError,
    LinuxDistribution,
    OperationRequest,
    PrivilegeError,
    apt,
    changed_hosts,
    compose,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    server,
    service_handler,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml + vars/Debian.yml, Ubuntu path only) ---

#: The OS dispatch frozen to the single Ubuntu target: the role derives the
#: repo URL segment from ``ansible_facts.distribution`` (lower-cased), with a
#: Pop!_OS / Linux Mint workaround that also resolves to ``ubuntu``.
UBUNTU_DISTRIBUTION = "ubuntu"

#: ``setup-Debian.yml``'s three legacy artifacts, removed before the repo is
#: (re-)created. The old ``.list`` name embeds the distribution segment.
LEGACY_KEY_PATH = "/etc/apt/trusted.gpg.d/docker.asc"
LEGACY_REPO_LIST = (
    f"/etc/apt/sources.list.d/download_docker_com_linux_{UBUNTU_DISTRIBUTION}.list"
)
LEGACY_DOCKER_LIST = "/etc/apt/sources.list.d/docker.list"

#: ``setup-Debian.yml``'s ``apt: name=[ca-certificates, python3-debian]``.
#: python3-debian is what Ansible's deb822_repository module needs on the
#: host; reemote generates the deb822 text control-side, but the dependency
#: is kept so the host matches the role's post-state.
DEPENDENCY_PACKAGES = ("ca-certificates", "python3-debian")

#: ``vars/Debian.yml``'s ``docker_obsolete_packages`` (identical to the
#: defaults list): the distro-packaged Docker bits the official repo replaces.
DEBIAN_OBSOLETE_PACKAGES = (
    "docker",
    "docker.io",
    "docker-engine",
    "docker-doc",
    "docker-compose",
    "docker-compose-v2",
    "podman-docker",
    "containerd",
    "runc",
)

DOCKER_CONF_DIR = "/etc/docker"
DAEMON_JSON_PATH = f"{DOCKER_CONF_DIR}/daemon.json"

#: The ExecStart override drop-in (``docker.service.d/<name>.conf``). The role
#: patches the packaged unit's FragmentPath in place; a drop-in is the
#: systemd-native override that survives package upgrades (see the deploy).
EXEC_START_DROPIN = "reemote-exec-start"

#: The request names that notify ``restart docker`` (handlers/main.yml).
#: Enumerated per host by ``notifying_names`` from the same frozen settings
#: the deploys read, so the notification set matches the requests exactly.
NOTIFY_PACKAGE = "Install Docker packages"
NOTIFY_COMPOSE_PLUGIN = "Install docker-compose-plugin"
NOTIFY_DAEMON_JSON = "Configure Docker daemon options"
NOTIFY_EXEC_START = "Override docker.service ExecStart"

#: The role defaults this migration honours. ``docker_packages``,
#: ``docker_apt_repo_url`` and ``docker_apt_gpg_key`` default to None -- the
#: role derives them from ``docker_edition`` / ``docker_repo_url`` when they
#: are not overridden (``role_settings`` reproduces the derivation). The
#: RedHat-only ``docker_yum_*`` variables are deliberately absent.
ROLE_DEFAULTS: dict[str, Any] = {
    "docker_edition": "ce",
    "docker_packages": None,
    "docker_packages_state": "present",
    "docker_obsolete_packages": DEBIAN_OBSOLETE_PACKAGES,
    "docker_service_manage": True,
    "docker_service_state": "started",
    "docker_service_enabled": True,
    "docker_service_start_command": "",
    "docker_restart_handler_state": "restarted",
    "docker_install_compose_plugin": True,
    "docker_compose_package": "docker-compose-plugin",
    "docker_compose_package_state": "present",
    "docker_install_compose": False,
    "docker_add_repo": True,
    "docker_repo_url": "https://download.docker.com/linux",
    "docker_apt_release_channel": "stable",
    "docker_apt_repo_url": None,
    "docker_apt_gpg_key": None,
    "docker_apt_filename": "docker",
    "docker_users": [],
    "docker_daemon_options": {},
}


# --- pure helpers ------------------------------------------------------------



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data and stays a stable module-level
    function, so both deploy phases reach the same eligibility decision.
    A skipped request is never materialized, so the Alpine and CentOS
    hosts in the shared inventory are not contacted at all.
    """
    return host.family == "Ubuntu"


def _package_state(value: Any, key: str, host: Host) -> str:
    """Validate a ``state:`` enum the package module accepts."""
    state = str(value)
    if state not in ("present", "latest", "absent"):
        raise ValueError(
            f"{host.name}: {key} must be present/latest/absent, got {state!r}"
        )
    return state


def role_settings(host: Host) -> dict[str, Any]:
    """Freeze one host's role variables: defaults overlaid with ``host.extra``.

    Reads only static inventory data, so a deploy body may call it and branch
    on the result -- both phases see the same dict. Reproduces the role's
    variable derivations: ``docker_packages`` from ``docker_edition``,
    ``docker_apt_repo_url`` / ``docker_apt_gpg_key`` from ``docker_repo_url``
    (the key URL follows the *upstream* repo, not a mirror override, exactly
    as the role's defaults template it), and the ``to_nice_json`` render of
    ``docker_daemon_options``. Type gates raise ``TypeError`` (ruff TRY004),
    enum gates ``ValueError`` -- both before any host is contacted.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}

    if merged["docker_install_compose"]:
        raise ValueError(
            f"{host.name}: docker_install_compose (the standalone binary "
            "install) is scoped out of this migration -- it downloads a "
            "binary release artifact, which the text-only files.download "
            "does not cover. Use docker_install_compose_plugin (the default) "
            "instead."
        )

    edition = str(merged["docker_edition"])
    packages = merged["docker_packages"]
    if packages is None:
        packages = (
            f"docker-{edition}",
            f"docker-{edition}-cli",
            f"docker-{edition}-rootless-extras",
            "containerd.io",
            "docker-buildx-plugin",
        )
    if not isinstance(packages, (list, tuple)):
        raise TypeError(f"{host.name}: docker_packages must be a list of names")

    users = merged["docker_users"]
    if not isinstance(users, (list, tuple)):
        raise TypeError(f"{host.name}: docker_users must be a list of names")

    options = merged["docker_daemon_options"]
    if not isinstance(options, dict):
        raise TypeError(f"{host.name}: docker_daemon_options must be a mapping")

    service_state = str(merged["docker_service_state"])
    if service_state not in ("started", "stopped", "restarted", "reloaded"):
        raise ValueError(
            f"{host.name}: docker_service_state must be "
            f"started/stopped/restarted/reloaded, got {service_state!r}"
        )
    restart_state = str(merged["docker_restart_handler_state"])
    if restart_state not in ("restarted", "reloaded", "started"):
        raise ValueError(
            f"{host.name}: docker_restart_handler_state must be "
            f"restarted/reloaded/started, got {restart_state!r}"
        )

    repo_url = str(merged["docker_repo_url"])
    apt_repo_url = merged["docker_apt_repo_url"]
    if apt_repo_url is None:
        apt_repo_url = f"{repo_url}/{UBUNTU_DISTRIBUTION}"
    gpg_key_url = merged["docker_apt_gpg_key"]
    if gpg_key_url is None:
        gpg_key_url = f"{repo_url}/{UBUNTU_DISTRIBUTION}/gpg"
    filename = str(merged["docker_apt_filename"])

    return {
        "packages": tuple(packages),
        "packages_state": _package_state(
            merged["docker_packages_state"], "docker_packages_state", host
        ),
        "obsolete_packages": tuple(merged["docker_obsolete_packages"]),
        "install_compose_plugin": bool(merged["docker_install_compose_plugin"]),
        "compose_package": str(merged["docker_compose_package"]),
        "compose_package_state": _package_state(
            merged["docker_compose_package_state"], "docker_compose_package_state", host
        ),
        "service_manage": bool(merged["docker_service_manage"]),
        "service_state": service_state,
        "service_enabled": bool(merged["docker_service_enabled"]),
        "start_command": str(merged["docker_service_start_command"]),
        "restart_handler_state": restart_state,
        "add_repo": bool(merged["docker_add_repo"]),
        "repo_url": str(apt_repo_url),
        "gpg_key_url": str(gpg_key_url),
        "apt_filename": filename,
        "release_channel": str(merged["docker_apt_release_channel"]),
        "users": tuple(users),
        # Ansible's to_nice_json is json.dumps(indent=4, sort_keys=True)
        # (its explicit separators are the indent-mode defaults), with no
        # trailing newline -- the copy module writes the string verbatim.
        "daemon_json": (
            json.dumps(options, indent=4, sort_keys=True) if options else None
        ),
    }


def distribution_codename(info: dict[str, Any]) -> str:
    """``ansible_facts.distribution_release`` from a LinuxDistribution read.

    Ubuntu's ``/etc/os-release`` carries ``VERSION_CODENAME`` (e.g.
    ``noble``); the fact upper-cases the os-release keys into
    ``release_meta``. A missing codename is a hard error -- the role's
    ``Suites: {{ ansible_facts.distribution_release }}`` would render empty
    and apt would reject the source.
    """
    codename = (info.get("release_meta") or {}).get("VERSION_CODENAME")
    if not codename:
        raise ValueError(
            "cannot read VERSION_CODENAME from the distribution facts "
            f"(release_meta={info.get('release_meta')!r}); the Docker apt "
            "repository needs the Ubuntu release codename for its Suites"
        )
    return str(codename)


def notifying_names(settings: dict[str, Any]) -> tuple[str, ...]:
    """Every request name that queues ``restart docker`` for one host.

    Enumerated from the same frozen settings the deploys were built from.
    The directory, service-state and user tasks carry no ``notify`` in the
    role, so their names are deliberately absent.
    """
    names = [NOTIFY_PACKAGE]
    if settings["install_compose_plugin"]:
        names.append(NOTIFY_COMPOSE_PLUGIN)
    if settings["daemon_json"] is not None:
        names.append(NOTIFY_DAEMON_JSON)
    if settings["start_command"]:
        names.append(NOTIFY_EXEC_START)
    return tuple(names)


# --- deploys -----------------------------------------------------------------


def build_setup(
    codenames: dict[str, str],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``tasks/setup-Debian.yml``: cleanup, dependencies, key and repository.

    The only fact the setup reads is the release codename for the repo's
    ``Suites`` -- frozen into ``codenames`` by the ``LinuxDistribution``
    gather in ``main`` (gather -> snapshot -> factory). Everything else is
    static ``host.extra``.

    The role's ``notify: apt update`` + ``meta: flush_handlers`` pair is
    ``update_cache=True`` on the sources request: the ``AptUpdateCommand``
    is yielded only after a real sources change (issue #14, closed), which
    is exactly the handler's fire-on-change semantics, in the same barrier.
    """

    def setup(host: Host) -> Generator[OperationRequest]:
        codename = codenames.get(host.name)
        if codename is None:
            return
        settings = role_settings(host)

        yield op(
            files.file,
            name="Remove Docker key from trusted.gpg.d",
            path=LEGACY_KEY_PATH,
            present=False,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            files.file,
            name="Remove old Docker apt source list",
            path=LEGACY_REPO_LIST,
            present=False,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            apt.packages,
            name="Remove obsolete Docker packages",
            packages=settings["obsolete_packages"],
            present=False,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            files.file,
            name="Remove legacy Docker repo file",
            path=LEGACY_DOCKER_LIST,
            present=False,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            apt.packages,
            name="Install Docker repository dependencies",
            packages=DEPENDENCY_PACKAGES,
            sudo=True,
            when=is_ubuntu,
        )

        # issue #17 (closed): signed_by takes the key URL directly --
        # apt.sources_file fetches the ASCII-armored key control-side and
        # installs /etc/apt/keyrings/<filename>.asc itself (Ansible's
        # deb822_repository parity), and present=False removes the managed
        # keyrings too, so no separate download or key-removal request is
        # needed.
        yield op(
            apt.sources_file,
            name="Add or remove Docker repository",
            filename=settings["apt_filename"],
            types=("deb",),
            uris=(settings["repo_url"],),
            suites=(codename,),
            components=(settings["release_channel"],),
            signed_by=settings["gpg_key_url"],
            present=settings["add_repo"],
            update_cache=True,
            sudo=True,
            when=is_ubuntu,
        )

    return setup


def deploy_packages(host: Host) -> Generator[OperationRequest]:
    """``tasks/main.yml``: the Docker packages and the compose plugin.

    The role carries two mutually exclusive install tasks per package set,
    split on the Ansible version; on modern Ansible (>= 2.12) the Debian
    path is the ``allow_downgrade: true`` one, so each set is a single
    ``apt.packages`` request. Deviation: Ansible spells the flag
    ``allow_downgrade`` (singular), reemote ``allow_downgrades`` (plural) --
    same semantics (``--allow-downgrades`` on the apt-get line). Both
    requests notify ``restart docker``.
    """
    settings = role_settings(host)
    yield op(
        apt.packages,
        name=NOTIFY_PACKAGE,
        packages=settings["packages"],
        present=settings["packages_state"] != "absent",
        latest=settings["packages_state"] == "latest",
        allow_downgrades=True,
        sudo=True,
        when=is_ubuntu,
    )
    if settings["install_compose_plugin"]:
        yield op(
            apt.packages,
            name=NOTIFY_COMPOSE_PLUGIN,
            packages=(settings["compose_package"],),
            present=settings["compose_package_state"] != "absent",
            latest=settings["compose_package_state"] == "latest",
            allow_downgrades=True,
            sudo=True,
            when=is_ubuntu,
        )


def deploy_configure(host: Host) -> Generator[OperationRequest]:
    """``tasks/main.yml``: daemon.json, the ExecStart override, the service.

    All three blocks are gated on static role variables, so this deploy is
    branch-free with respect to facts. The ExecStart block is a shape
    change: the role queries the unit's FragmentPath with
    ``systemd_service`` and ``replace``s the packaged ``ExecStart=`` line in
    place; reemote writes a ``systemd.dropin`` override instead -- the
    systemd-native mechanism, which leaves the packaged unit untouched
    (an in-place edit would be reverted by the next package upgrade). The
    empty ``ExecStart=`` line clears the packaged value before the
    replacement, per systemd's override semantics. ``daemon_reload=True``
    reproduces the role's conditional ``daemon_reload`` (the operation
    chains it only on a real content change); the restart itself rides the
    handler, as in the role (``restart=False``).
    """
    settings = role_settings(host)

    if settings["daemon_json"] is not None:
        yield op(
            files.directory,
            name="Ensure /etc/docker directory exists",
            path=DOCKER_CONF_DIR,
            mode="0755",
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            files.install,
            name=NOTIFY_DAEMON_JSON,
            content=settings["daemon_json"],
            dest=DAEMON_JSON_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )

    if settings["start_command"]:
        yield op(
            systemd.dropin,
            name=NOTIFY_EXEC_START,
            unit="docker",
            filename=EXEC_START_DROPIN,
            content=f"[Service]\nExecStart=\nExecStart={settings['start_command']}\n",
            daemon_reload=True,
            sudo=True,
            when=is_ubuntu,
        )

    if settings["service_manage"]:
        # docker_service_state maps to the systemd.service verbs;
        # restarted/reloaded are stateless (they always report changed),
        # faithful to Ansible's service module -- the default started is
        # fact-gated and converges.
        state = settings["service_state"]
        yield op(
            systemd.service,
            name="Ensure Docker is started and enabled at boot",
            service="docker",
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=settings["service_enabled"],
            sudo=True,
            when=is_ubuntu,
        )


def deploy_users(host: Host) -> Generator[OperationRequest]:
    """``tasks/docker-users.yml``: append each docker_user to the docker group.

    The role's ``getent`` + ``set_fact`` gate is reproduced around this
    deploy, not inside it: ``main`` gathers ``Groups`` after the package
    install and fails when the docker group is absent (the getent task
    fails the same way), and ``server.user(append=True)`` is itself
    idempotent on membership -- it adds only what the user lacks, which
    subsumes the role's ``at_least_one_user_to_modify`` pre-check.

    Deviation: ``ensure_home=False`` keeps the request a pure membership
    append; reemote's default would also converge home-directory ownership,
    which the role's task does not ask for. The trailing
    ``meta: reset_connection`` needs no request: every ``run()`` builds
    fresh hosts, so the next deploy (and the next program run) already
    connects with the new group memberships.
    """
    settings = role_settings(host)
    for user in settings["users"]:
        yield op(
            server.user,
            name=f"Add {user} to the docker group",
            user=user,
            groups=("docker",),
            append=True,
            ensure_home=False,
            sudo=True,
            when=is_ubuntu,
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

        # The role's variable resolution and its scoped-out gate, before any
        # host is contacted.
        settings = {host.name: role_settings(host) for host in targets}

        # The release codename for the repository's Suites, gathered once up
        # front (it is static OS identity, not a deploy outcome). gather_fact
        # does not close its hosts, so the finally does; run() builds and
        # closes its own.
        try:
            distributions = await gather_fact(targets, LinuxDistribution)
        finally:
            for host in hosts:
                await host.aclose()
        codenames = {
            host.name: distribution_codename(distributions[host.name])
            for host in targets
        }
        print("\n== Ubuntu release codenames (LinuxDistribution) ==")
        for name, codename in codenames.items():
            print(f"{name}: {codename}")

        reports = []
        setup = build_setup(codenames)
        reports.append(
            await execute(
                inventory, setup, "Deploy 1: Docker apt repository (setup-Debian.yml)"
            )
        )
        packages_report = await execute(
            inventory, deploy_packages, "Deploy 2: Docker packages"
        )
        reports.append(packages_report)
        configure_report = await execute(
            inventory, deploy_configure, "Deploy 3: daemon configuration and service"
        )
        reports.append(configure_report)

        # The restart docker handler -- the role's second flush_handlers
        # barrier ("avoid firewall conflicts"): it fires BEFORE the user
        # tasks, so the order here matches the play. Every notifying request
        # name is enumerated from the same frozen settings the deploys were
        # built from; the notification set is the union over the two
        # notifying deploys' apply reports, filtered by each host's
        # docker_service_manage (the handler's own `when:`) and partitioned
        # by docker_restart_handler_state.
        names = tuple(
            sorted({name for s in settings.values() for name in notifying_names(s)})
        )
        notified = changed_hosts(packages_report, names) | changed_hosts(
            configure_report, names
        )
        managed = frozenset(
            name for name in notified if settings[name]["service_manage"]
        )
        by_action: dict[str, list[str]] = {}
        for name in sorted(managed):
            action = settings[name]["restart_handler_state"]
            action = "running" if action == "started" else action
            by_action.setdefault(action, []).append(name)
        print("\n== Handler notification set (restart docker) ==")
        print(sorted(managed) or "(none)")
        handlers = compose(
            *(
                service_handler(
                    frozenset(action_names), ("docker",), action=action, when=is_ubuntu
                )
                for action, action_names in sorted(by_action.items())
            )
        )
        handler_report = await execute(
            inventory, handlers, "Deploy 4: restart docker handler (flush_handlers)"
        )

        # The getent gate: the role's `getent: database=group key=docker`
        # fails the play when the group does not exist (e.g. the packages
        # were removed). Reproduce that fail-fast between runs, then append
        # memberships. The gather sits AFTER the install apply, when the
        # docker-ce postinst has created the group on a real machine.
        user_hosts = [host for host in targets if settings[host.name]["users"]]
        if user_hosts:
            try:
                groups = await gather_fact(user_hosts, Groups)
            finally:
                for host in hosts:
                    await host.aclose()
            for host in user_hosts:
                if "docker" not in groups.get(host.name, []):
                    raise ValueError(
                        f"{host.name}: the docker group does not exist; install "
                        "the Docker packages before listing docker_users (the "
                        "role's getent task fails the same way)"
                    )
            reports.append(
                await execute(inventory, deploy_users, "Deploy 5: docker group users")
            )

        changed = sum(
            1
            for report in [*reports, handler_report]
            for entry in report.log
            if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across the deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, a role variable is
        # invalid, a scoped-out override was requested, or the key URL could
        # not be fetched (urllib's URLError is an OSError). No host mutation
        # is implied by the variable gates, so the message is the whole report.
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
