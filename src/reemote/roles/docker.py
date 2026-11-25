# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Docker role (migrated from ``geerlingguy.docker``).
#
# Installs and configures Docker CE from the official Docker apt repository: the
# legacy key/source/package cleanup, the repository dependencies, the signing key
# and deb822 ``.sources`` file, the Docker packages (with the compose and buildx
# plugins), the optional ``/etc/docker/daemon.json`` and ``docker.service``
# ExecStart override, the service's running/enabled state, the ``restart docker``
# handler, and the ``docker_users`` group memberships. :func:`docker` is the whole
# task list and :class:`DockerVars` is ``defaults/main.yml``, bound from the
# inventory's ``docker_``-prefixed keys by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the family returns
# before its first ``yield`` and emits no requests, no barriers and no log lines
# at all -- so the Alpine and CentOS hosts in a shared inventory are never
# contacted. The role's RedHat and Suse branches are out of scope, so its
# ``include_vars`` OS dispatch collapses to the frozen Debian constants below,
# and the opt-in standalone docker-compose binary install is scoped out: a
# truthy :attr:`DockerVars.install_compose` raises instead of silently dropping
# the feature.
#
# The stream is up to five waves -- two checkpoints, one conditional ``fact``
# barrier, and the request waves they close (design doc section 18.11):
#
# 0. ``fact(LinuxDistribution)`` -- ``release_meta.VERSION_CODENAME`` feeds the
#    repository's ``Suites``, so the setup wave is a pure function of it.
# 1. :func:`setup` then :func:`packages` -- the legacy cleanup, dependencies,
#    key and deb822 repository, then the Docker packages -- closed by
#    ``checkpoint("packages done")``. The role ran these as two separate plays'
#    tasks; no read sits between them, so the driver simply applies each request
#    before pulling the next and they share one wave.
# 2. :func:`configure` -- daemon.json, the ExecStart drop-in, the service state
#    -- closed by ``checkpoint("configure done")``.
# 3. :func:`handler`-gated restart -- ``restart docker``, only where this host
#    sits in the union of the *packages* and *configure* reports and
#    ``service_manage`` is on (the role's second ``flush_handlers``, which runs
#    *before* the user tasks).
# 4. ``fact(Groups)`` (conditional, only with users) -- the role's ``getent``
#    gate: the docker group must exist after the install, and a missing group
#    raises, because the getent task fails the play the same way. A barrier
#    result is data; the raise is the role's, not the driver's.
# 5. :func:`users` -- one ``server.user(append=True)`` membership per listed
#    user (only when the group gate passed).
#
# **Two checkpoints, unioned.** The handler's notification set spans the
# packages and configure deploys, so both reports are read and unioned in the
# body -- the php shape (design doc section 18.6), not the nginx one where both
# channels share a single report.
#
# The restart verb reads *this host's* :attr:`DockerVars.restart_handler_state`
# (mapped through :data:`HANDLER_ACTIONS`), so the old example's ``by_action``
# partition and its whole-inventory ``names`` union both disappear: they existed
# only because one ``compose(*service_handler(...))`` deploy served every host,
# and a per-host body needs neither.
#
# **``meta: reset_connection`` changes meaning here.** The old script could call
# it a structural no-op because every ``run()`` built fresh hosts; under
# :func:`~reemote.run_waves` the connections stay open for the whole run (§13),
# so a new group membership takes effect on the *next* pass only. This is the
# one place the migration makes an Ansible behaviour unreachable rather than
# merely different: a program that needs the membership mid-run must drive two
# :func:`~reemote.run_waves` passes (design doc section 18.11).
#
# No template engine is needed: the role's only filter is ``to_nice_json``,
# which maps byte-for-byte to the standard library's ``json.dumps(indent=4,
# sort_keys=True)`` (no trailing newline), so this role never imports
# :mod:`reemote.roles._render` and needs no ``reemote[templates]`` extra.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the ``apt update`` handler (notify + immediate flush) is folded into
#   ``apt.sources_file(update_cache=True)`` -- the index refresh chains onto a
#   real sources change (write *or* removal), same fire-on-change semantics, one
#   fewer barrier.
# * D2: the ExecStart override is a ``systemd.dropin``
#   (``docker.service.d/reemote-exec-start.conf``), not an in-place patch of the
#   packaged unit's FragmentPath: the empty ``ExecStart=`` line clears the
#   packaged value per systemd override semantics, the unit file survives
#   package upgrades untouched, and ``daemon_reload=True`` reproduces the
#   conditional reload (chained on a real content change only). The drop-in
#   write does not need the unit to exist.
# * D3: the role's ``ignore_errors: ansible_check_mode`` is dropped -- the
#   ``dry`` preview never applies, so there is no check-mode error to tolerate.
# * D4: the ``set_fact: at_least_one_user_to_modify`` pre-check is subsumed:
#   ``server.user(append=True)`` adds only missing memberships and reports no
#   change for a user already in the group. The getent *failure* on a missing
#   docker group is reproduced as the fail-fast gate above.
# * D5: ``ensure_home=False`` on the user requests keeps each one a pure
#   membership append; reemote's default would also converge home-directory
#   ownership, which the role's task does not ask for.
# * D6: Ansible's ``allow_downgrade`` (singular) is reemote's
#   ``allow_downgrades`` -- same ``--allow-downgrades`` semantics -- and the
#   role's two version-split install tasks collapse to the modern (>= 2.12) one.
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: an off-target host emits no requests and no ``[skipped]`` lines.
# * variable validation becomes pydantic validation: the three enums are
#   :data:`~typing.Literal` fields, the bool gates are ``bool`` fields, a bad
#   value raises ``ValidationError`` -- a ``ValueError``, so the runner maps it
#   -- before any host is contacted (the old example's ``TypeError`` gates
#   included).
# * the repository and package deploys share one wave (see wave 1 above): the
#   split the example needed was a two-phase artifact, not a dependency.
# """

import json
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import apt, files, server, systemd
from ..modules.server import Groups, LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

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
#: systemd-native override that survives package upgrades (deviation D2).
EXEC_START_DROPIN = "reemote-exec-start"

#: The request names that notify ``restart docker`` (handlers/main.yml).
#: Enumerated per host by :func:`notifying_names` from the same frozen plan the
#: deploys read, so the notification set matches the requests exactly.
NOTIFY_PACKAGE = "Install Docker packages"
NOTIFY_COMPOSE_PLUGIN = "Install docker-compose-plugin"
NOTIFY_DAEMON_JSON = "Configure Docker daemon options"
NOTIFY_EXEC_START = "Override docker.service ExecStart"

#: ``docker_restart_handler_state`` -> the ``service_handler`` action. Ansible's
#: ``started`` state is the handler's ``running`` verb (the operation's default
#: ``running=True`` starts a stopped unit and leaves a running one alone).
HANDLER_ACTIONS = {"restarted": "restarted", "reloaded": "reloaded", "started": "running"}


# --- variables ---------------------------------------------------------------


class DockerVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names carry no ``docker_`` prefix -- the prefix passed to
    :func:`~reemote.rolevars.resolve_vars` binds ``docker_users`` in the
    inventory to :attr:`users`, so an inventory written for the role needs no
    change. Every Ubuntu-path default is carried over; the RedHat-only
    ``docker_yum_*`` variables are deliberately absent. :attr:`packages`,
    :attr:`apt_repo_url` and :attr:`apt_gpg_key`` default to ``None`` -- the
    role derives them from :attr:`edition` / :attr:`repo_url` when they are not
    overridden (:func:`docker_plan` reproduces the derivations).

    The model is frozen and every value that reaches
    :func:`~reemote.core.op` is hashable, so sequences are ``tuple`` (never a
    ``list``); :attr:`daemon_options` is the one mapping, and it only ever
    reaches an operation after :func:`render_daemon_json` turns it into a
    ``str`` (design doc section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``docker_edition``: the package-name prefix (``docker-ce``, ...).
    edition: str = "ce"
    #: ``docker_packages``: ``None`` derives the edition's five-package list.
    packages: tuple[str, ...] | None = None
    packages_state: Literal["present", "latest", "absent"] = "present"
    obsolete_packages: tuple[str, ...] = DEBIAN_OBSOLETE_PACKAGES

    # --- the service and its handler --------------------------------------
    #: Gates the service task *and* the ``restart docker`` handler, as the
    #: role's two ``when: docker_service_manage`` do.
    service_manage: bool = True
    service_state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    service_enabled: bool = True
    #: ``docker_service_start_command``: when set, the ExecStart drop-in (D2).
    service_start_command: str = ""
    #: The verb the handler uses when a notifying request changed something.
    restart_handler_state: Literal["restarted", "reloaded", "started"] = "restarted"

    # --- compose ------------------------------------------------------------
    install_compose_plugin: bool = True
    compose_package: str = "docker-compose-plugin"
    compose_package_state: Literal["present", "latest", "absent"] = "present"
    #: The standalone docker-compose *binary* install: scoped out (its get_url
    #: downloads a binary release artifact, outside the text-only transfer
    #: family). A truthy value raises rather than silently dropping the feature.
    install_compose: bool = False

    # --- repository ----------------------------------------------------------
    add_repo: bool = True
    #: ``docker_repo_url``: the base for the derived repo and key URLs.
    repo_url: str = "https://download.docker.com/linux"
    #: ``docker_apt_release_channel``: the repository component.
    apt_release_channel: str = "stable"
    #: ``docker_apt_repo_url``: ``None`` derives ``<repo_url>/ubuntu``; a
    #: mirror override changes the URI only.
    apt_repo_url: str | None = None
    #: ``docker_apt_gpg_key``: ``None`` derives ``<repo_url>/ubuntu/gpg`` --
    #: the key URL follows the *upstream* repo, not a mirror override, exactly
    #: as the role's defaults template it.
    apt_gpg_key: str | None = None
    #: Base name of both the ``.sources`` file and the managed keyring.
    apt_filename: str = "docker"

    # --- users and daemon options --------------------------------------------
    #: ``docker_users``: appended to the ``docker`` group (the getent gate
    #: runs first; D4/D5).
    users: tuple[str, ...] = ()
    #: ``docker_daemon_options``: rendered to ``/etc/docker/daemon.json`` with
    #: ``to_nice_json`` semantics when non-empty.
    daemon_options: dict[str, Any] = field(default_factory=dict)

    @field_validator("install_compose")
    @classmethod
    def _scoped_out(cls, value: bool) -> bool:
        """``docker_install_compose`` is scoped out; requesting it raises."""
        if value:
            raise ValueError(
                "docker_install_compose (the standalone binary install) is "
                "scoped out of this migration -- it downloads a binary release "
                "artifact, which the text-only transfer family does not cover. "
                "Use docker_install_compose_plugin (the default) instead."
            )
        return value

    @field_validator("packages", "obsolete_packages", "users", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> Any:
        """A list becomes a tuple; a non-sequence falls through for pydantic to reject."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("packages", "obsolete_packages", "users")
    @classmethod
    def _entries_non_empty(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Every entry must be a non-empty string."""
        if value is None:
            return value
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("must hold non-empty strings")
        return value

    @field_validator("edition", "compose_package", "apt_filename", "apt_release_channel")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty name is a typo, not "unset"."""
        if not value:
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("repo_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        """The derived URLs concatenate a path segment directly."""
        if value.endswith("/"):
            raise ValueError("repo_url must not end with a slash")
        return value


# --- frozen per-host plan ------------------------------------------------------


@dataclass(frozen=True)
class DockerPlan:
    """One host's resolved setup/packages/configure targets, built by :func:`docker_plan`.

    Everything except the release codename is decided from the resolved
    variables; the codename arrives from the ``LinuxDistribution`` barrier, and
    the plan is frozen right after it -- so every deploy below is a pure
    function of this one object. It replaces the example's ``settings`` dict
    keyed by host name and its whole-inventory ``names`` union: under a
    per-host body, the plan is a local.

    ``notify_names`` lists this host's notifying request names (the channel the
    two checkpoints are read against), and ``handler_action`` is the already
    mapped ``service_handler`` verb for :attr:`DockerVars.restart_handler_state`.
    """

    codename: str
    packages: tuple[str, ...]
    packages_present: bool
    packages_latest: bool
    obsolete_packages: tuple[str, ...]
    install_compose_plugin: bool
    compose_package: tuple[str, ...]
    compose_present: bool
    compose_latest: bool
    service_manage: bool
    service_running: bool
    service_restarted: bool
    service_reloaded: bool
    service_enabled: bool
    handler_action: str
    add_repo: bool
    repo_url: str
    gpg_key_url: str
    apt_filename: str
    release_channel: str
    users: tuple[str, ...]
    daemon_json: str | None
    start_command: str
    notify_names: tuple[str, ...] = field(default=())


# --- pure helpers ------------------------------------------------------------


def derive_packages(edition: str) -> tuple[str, ...]:
    """The role's templated ``docker_packages`` for one edition."""
    return (
        f"docker-{edition}",
        f"docker-{edition}-cli",
        f"docker-{edition}-rootless-extras",
        "containerd.io",
        "docker-buildx-plugin",
    )


def render_daemon_json(options: dict[str, Any]) -> str | None:
    """``{{ docker_daemon_options | to_nice_json }}``, control-side.

    Ansible's ``to_nice_json`` is ``json.dumps(indent=4, sort_keys=True)``
    (its explicit separators are the indent-mode defaults), with no trailing
    newline -- the copy task writes the string verbatim. An empty mapping means
    no daemon.json at all, exactly as the role's ``when: ... | length > 0``.
    """
    if not options:
        return None
    return json.dumps(options, indent=4, sort_keys=True)


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


def notifying_names(plan_fields: dict[str, Any]) -> tuple[str, ...]:
    """Every request name that queues ``restart docker`` for one host.

    Enumerated from the same frozen inputs the deploys were built from. The
    directory, service-state and user tasks carry no ``notify`` in the role, so
    their names are deliberately absent.
    """
    names = [NOTIFY_PACKAGE]
    if plan_fields["install_compose_plugin"]:
        names.append(NOTIFY_COMPOSE_PLUGIN)
    if plan_fields["daemon_json"] is not None:
        names.append(NOTIFY_DAEMON_JSON)
    if plan_fields["start_command"]:
        names.append(NOTIFY_EXEC_START)
    return tuple(names)


def docker_plan(hv: DockerVars, info: dict[str, Any]) -> DockerPlan:
    """Freeze the per-host targets from the resolved vars and the gathered distribution.

    Reproduces the role's variable derivations: ``packages`` from ``edition``,
    the repo/key URLs from ``repo_url`` (the key URL follows the *upstream*
    repo, not a mirror override), and the ``to_nice_json`` render of
    ``daemon_options``. The state enums split into the operation's
    ``present``/``latest`` booleans.
    """
    codename = distribution_codename(info)
    packages = hv.packages if hv.packages is not None else derive_packages(hv.edition)
    daemon_json = render_daemon_json(hv.daemon_options)
    repo_url = hv.apt_repo_url if hv.apt_repo_url is not None else (
        f"{hv.repo_url}/{UBUNTU_DISTRIBUTION}"
    )
    gpg_key_url = hv.apt_gpg_key if hv.apt_gpg_key is not None else (
        f"{hv.repo_url}/{UBUNTU_DISTRIBUTION}/gpg"
    )
    state = hv.packages_state
    compose_state = hv.compose_package_state
    service_state = hv.service_state
    fields = {
        "install_compose_plugin": hv.install_compose_plugin,
        "daemon_json": daemon_json,
        "start_command": hv.service_start_command,
    }
    return DockerPlan(
        codename=codename,
        packages=tuple(packages),
        packages_present=state != "absent",
        packages_latest=state == "latest",
        obsolete_packages=hv.obsolete_packages,
        install_compose_plugin=hv.install_compose_plugin,
        compose_package=(hv.compose_package,),
        compose_present=compose_state != "absent",
        compose_latest=compose_state == "latest",
        service_manage=hv.service_manage,
        service_running=service_state != "stopped",
        service_restarted=service_state == "restarted",
        service_reloaded=service_state == "reloaded",
        service_enabled=hv.service_enabled,
        handler_action=HANDLER_ACTIONS[hv.restart_handler_state],
        add_repo=hv.add_repo,
        repo_url=repo_url,
        gpg_key_url=gpg_key_url,
        apt_filename=hv.apt_filename,
        release_channel=hv.apt_release_channel,
        users=tuple(hv.users),
        daemon_json=daemon_json,
        start_command=hv.service_start_command,
        notify_names=notifying_names(fields),
    )


# --- deploys -----------------------------------------------------------------


def setup(plan: DockerPlan) -> Deploy:
    """``tasks/setup-Debian.yml``: cleanup, dependencies, key and repository.

    The role's ``notify: apt update`` + ``meta: flush_handlers`` pair is
    ``update_cache=True`` on the sources request: the ``AptUpdatable`` refresh
    is yielded only after a real sources change (write *or* removal, issue #14)
    -- D1. The URL ``signed_by`` form (issue #17) fetches the ASCII-armored key
    control-side and installs the repo-named ``/etc/apt/keyrings/<name>.asc``
    itself, and ``present=False`` removes the managed keyring too, so no
    separate download or key-removal request is needed.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.file,
            name="Remove Docker key from trusted.gpg.d",
            path=LEGACY_KEY_PATH,
            present=False,
            sudo=True,
        )
        yield op(
            files.file,
            name="Remove old Docker apt source list",
            path=LEGACY_REPO_LIST,
            present=False,
            sudo=True,
        )
        yield op(
            apt.packages,
            name="Remove obsolete Docker packages",
            packages=plan.obsolete_packages,
            present=False,
            sudo=True,
        )
        yield op(
            files.file,
            name="Remove legacy Docker repo file",
            path=LEGACY_DOCKER_LIST,
            present=False,
            sudo=True,
        )
        yield op(
            apt.packages,
            name="Install Docker repository dependencies",
            packages=DEPENDENCY_PACKAGES,
            sudo=True,
        )
        yield op(
            apt.sources_file,
            name="Add or remove Docker repository",
            filename=plan.apt_filename,
            types=("deb",),
            uris=(plan.repo_url,),
            suites=(plan.codename,),
            components=(plan.release_channel,),
            signed_by=plan.gpg_key_url,
            present=plan.add_repo,
            update_cache=True,
            sudo=True,
        )

    return deploy


def packages(plan: DockerPlan) -> Deploy:
    """The Docker packages and the compose plugin (both notify ``restart docker``).

    The role carries two mutually exclusive install tasks per package set,
    split on the Ansible version; on modern Ansible (>= 2.12) the Debian path
    is the ``allow_downgrade: true`` one, so each set is a single
    ``apt.packages`` request (D6).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name=NOTIFY_PACKAGE,
            packages=plan.packages,
            present=plan.packages_present,
            latest=plan.packages_latest,
            allow_downgrades=True,
            sudo=True,
        )
        if plan.install_compose_plugin:
            yield op(
                apt.packages,
                name=NOTIFY_COMPOSE_PLUGIN,
                packages=plan.compose_package,
                present=plan.compose_present,
                latest=plan.compose_latest,
                allow_downgrades=True,
                sudo=True,
            )

    return deploy


def configure(plan: DockerPlan) -> Deploy:
    """daemon.json, the ExecStart override, the service -- all vars-gated, fact-free.

    The daemon.json block runs only when ``docker_daemon_options`` was non-empty
    (the role's ``| length > 0``); the drop-in only when a start command is set
    (D2); the service task only when ``docker_service_manage``. restarted /
    reloaded are stateless (always changed), faithful to Ansible's service
    module -- the default started is fact-gated and converges.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if plan.daemon_json is not None:
            yield op(
                files.directory,
                name="Ensure /etc/docker directory exists",
                path=DOCKER_CONF_DIR,
                mode="0755",
                sudo=True,
            )
            yield op(
                files.install,
                name=NOTIFY_DAEMON_JSON,
                content=plan.daemon_json,
                dest=DAEMON_JSON_PATH,
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )

        if plan.start_command:
            yield op(
                systemd.dropin,
                name=NOTIFY_EXEC_START,
                unit="docker",
                filename=EXEC_START_DROPIN,
                content=(
                    f"[Service]\nExecStart=\nExecStart={plan.start_command}\n"
                ),
                daemon_reload=True,
                sudo=True,
            )

        if plan.service_manage:
            yield op(
                systemd.service,
                name="Ensure Docker is started and enabled at boot",
                service="docker",
                running=plan.service_running,
                restarted=plan.service_restarted,
                reloaded=plan.service_reloaded,
                enabled=plan.service_enabled,
                sudo=True,
            )

    return deploy


def users(plan: DockerPlan) -> Deploy:
    """``tasks/docker-users.yml``: append each docker_user to the docker group.

    The getent gate is the body's ``fact(Groups)`` barrier, taken after the
    packages applied (the docker-ce postinst creates the group on a real
    machine) and before these requests. ``ensure_home=False`` keeps each
    request a pure membership append (D5); ``append=True`` is idempotent on
    membership, which subsumes the role's ``at_least_one_user_to_modify``
    pre-check (D4).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for user in plan.users:
            yield op(
                server.user,
                name=f"Add {user} to the docker group",
                user=user,
                groups=("docker",),
                append=True,
                ensure_home=False,
                sudo=True,
            )

    return deploy


# --- the role ----------------------------------------------------------------


def docker(v: VarsSet[DockerVars]) -> Deploy:
    """Install and configure Docker CE on Ubuntu hosts, and run the role's handler.

    Args:
        v: The role's resolved variables, one :class:`DockerVars` per host
            (``resolve_vars(DockerVars, "docker_", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: The distribution read carries no ``VERSION_CODENAME``, or
            ``docker_users`` is set while the ``docker`` group does not exist
            after the install (the role's getent task fails the same way). Each
            is raised inside the body, after the relevant barrier and before the
            requests it would gate, so nothing is half-applied. An
            ``install_compose`` override raises during variable resolution,
            before any host is contacted.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the release codename for the repository's Suites. Its
        # barrier closes before the setup wave is built, so every request
        # below is a pure function of the frozen plan.
        info = yield fact(LinuxDistribution)
        plan = docker_plan(hv, info)

        # Wave 1: the repository setup, then the packages -- no barrier
        # between them; the driver applies each request before pulling the
        # next (the example's two-deploy split was a two-phase artifact).
        yield from setup(plan)(host)
        yield from packages(plan)(host)
        packages_report = yield checkpoint("packages done")

        # Wave 2: the daemon configuration, closed by its own checkpoint.
        yield from configure(plan)(host)
        configure_report = yield checkpoint("configure done")

        # Wave 3: the restart docker handler -- the role's second
        # flush_handlers, before the user tasks. The notification set spans
        # both reports, so the two reads are unioned (the php shape, §18.6);
        # the handler's own gate is service_manage, and the verb is this
        # host's restart_handler_state (no by_action partition survives).
        notified = changed_hosts(packages_report, plan.notify_names) | changed_hosts(
            configure_report, plan.notify_names
        )
        if host.name in notified and plan.service_manage:
            yield from service_handler(
                frozenset({host.name}), ("docker",), action=plan.handler_action
            )(host)

        # Wave 4: the getent gate, then the memberships -- only for a host
        # that lists users. The role fails the play when the group is absent
        # (the docker packages were removed, say), and a barrier result is
        # data, so the raise is the role's, not the driver's.
        if plan.users:
            groups = yield fact(Groups)
            if "docker" not in groups:
                raise ValueError(
                    f"{host.name}: the docker group does not exist; install the "
                    "Docker packages before listing docker_users (the role's "
                    "getent task fails the same way)"
                )
            # Wave 5: the group memberships.
            yield from users(plan)(host)

    return deploy


__all__ = [
    "DockerVars",
    "docker",
]
