# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The Docker role (migrated from ``geerlingguy.docker``).
#
# Installs and configures Docker CE from the official vendor repository: on
# Ubuntu the legacy key/source/package cleanup, the repository dependencies and
# the signing key with its deb822 ``.sources`` file; on RedHat the obsolete-
# package purge, the GPG key and ``.repo`` file fetched control-side, the
# nightly/test section management, and the RHEL 8 containerd block. Both
# families then share the rest of the task list: the Docker packages (with the
# compose and buildx plugins), the optional ``/etc/docker/daemon.json`` and
# ``docker.service`` ExecStart override, the service's running/enabled state,
# the ``restart docker`` handler, and the ``docker_users`` group memberships.
# :func:`docker` is the whole task list and :class:`DockerVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``docker`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` and ``"RedHat"`` OS families -- the two
# setup branches the upstream role ships (the certbot precedent). A host
# outside both families returns before its first ``yield`` and emits no
# requests, no barriers and no log lines at all -- so the Alpine host in a
# shared inventory is never contacted. The role's Suse branch stays out of
# scope, so its ``include_vars`` OS dispatch collapses to the per-family
# branches of :func:`docker_plan`,
# and the legacy standalone docker-compose binary install is scoped out: it is
# an opt-in path the upstream defaults turn off and the compose plugin package
# supersedes, so a truthy :attr:`DockerVars.install_compose` raises instead of
# silently dropping the feature.
#
# The stream is up to six waves on Ubuntu, seven on a fresh RedHat host -- two
# checkpoints, the conditional barriers, and the request waves they close
# (design doc section 18.11):
#
# 0. ``fact(LinuxDistribution)`` -- on Ubuntu ``release_meta.VERSION_CODENAME``
#    feeds the repository's ``Suites``; on RedHat ``name`` picks the
#    ``fedora``/``rhel``/``centos`` URL segment and ``major`` gates the RHEL 8
#    containerd block (the ``include_vars`` dispatch, frozen). Either way the
#    setup wave is a pure function of this one read.
# 1. RedHat only, and only with ``add_repo``: ``fact(FileExists)`` on the
#    managed ``/etc/yum.repos.d/docker-<edition>.repo`` -- the ``stat``
#    register that guards the key and repo install (the repo_epel shape) --
#    then, when the file is absent, ``control(files.fetch_url)`` for the
#    ASCII-armored GPG key. An Ubuntu stream skips this wave and numbers the
#    rest one lower.
# 2. :func:`setup` (the family's branch: :func:`redhat_setup` or the Debian
#    cleanup, dependencies and deb822 repository) then :func:`packages` --
#    closed by ``checkpoint("packages done")``. The role ran these as two
#    separate plays' tasks; no read sits between them, so the driver simply
#    applies each request before pulling the next and they share one wave.
# 3. :func:`configure` -- daemon.json, the ExecStart drop-in, the service state
#    (all family-agnostic) -- closed by ``checkpoint("configure done")``.
# 4. :func:`handler`-gated restart -- ``restart docker``, only where this host
#    sits in the union of the *packages* and *configure* reports and
#    ``service_manage`` is on (the role's second ``flush_handlers``, which runs
#    *before* the user tasks).
# 5. ``fact(Groups)`` (conditional, only with users) -- the role's ``getent``
#    gate: the docker group must exist after the install, and a missing group
#    raises, because the getent task fails the play the same way. A barrier
#    result is data; the raise is the role's, not the driver's.
# 6. :func:`users` -- one ``server.user(append=True)`` membership per listed
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
#   ``allow_downgrades`` on apt -- same ``--allow-downgrades`` semantics --
#   while ``dnf.packages`` keeps the singular spelling; the role's two
#   version-split install tasks collapse to the modern (>= 2.12) one on both
#   families.
# * D9: RedHat's ``rpm_key`` and ``get_url`` read **control-side** --
#   ``files.fetch_url`` feeds the armored key to ``dnf.key`` as a stream (with
#   its :data:`DOCKER_KEY_ID` gate on ``RpmGpgKeys``) and ``files.download``
#   installs the ``.repo`` file, the repo_epel shape; the pair is guarded by
#   ``fact(FileExists, repofile)``, so a converged host fetches nothing where
#   upstream re-downloads on every pass. ``add_repo`` false installs no repo on
#   RedHat -- and removes nothing, because upstream's RedHat branch has no
#   repo-removal task at all, unlike the Debian one.
# * D10: the ``ini_file`` nightly-section removal (``state: absent`` without an
#   ``option``) becomes a ``files.transform`` carrying the pure
#   :func:`drop_ini_section` callable -- ``files.ini_option`` deliberately
#   ships no section removal; the test-section toggle is the faithful
#   ``files.ini_option(no_extra_spaces=True)``.
# * the host-family guard is the body's opening ``if``, not a ``when=`` on every
#   request: an off-target host emits no requests and no ``[skipped]`` lines.
# * variable validation becomes pydantic validation: the three enums are
#   :data:`~typing.Literal` fields, the bool gates are ``bool`` fields, a bad
#   value raises ``ValidationError`` -- a ``ValueError``, so the runner maps it
#   -- before any host is contacted (the old example's ``TypeError`` gates
#   included).
# * the repository and package deploys share one wave (see wave 2 above): the
#   split the example needed was a two-phase artifact, not a dependency.
# """

import io
import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import apt, dnf, files, server, systemd
from ..modules.files import FileExists
from ..modules.server import Groups, LinuxDistribution
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, control, fact

# --- role constants (defaults/main.yml + vars/*.yml, both target families) ---

#: The families the role runs on; anything else returns before its first
#: ``yield`` and emits no requests, no barriers and no log lines. Upstream
#: ships a dedicated setup file for exactly these two (Suse is out of scope).
TARGET_FAMILIES = ("Ubuntu", "RedHat")

#: The OS dispatch's Ubuntu arm: the role derives the repo URL segment from
#: ``ansible_facts.distribution`` (lower-cased), with a Pop!_OS / Linux Mint
#: workaround that also resolves to ``ubuntu``.
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

#: ``vars/RedHat.yml``'s ``docker_obsolete_packages``: the distro- and
#: project-packaged Docker bits the official yum repo replaces (the list
#: covers Fedora/Rocky as well as CentOS, per the file's own comments).
REDHAT_OBSOLETE_PACKAGES = (
    "docker",
    "docker-client",
    "docker-client-latest",
    "docker-common",
    "docker-latest",
    "docker-latest-logrotate",
    "docker-logrotate",
    "docker-engine",
)

#: The RedHat URL segment table: upstream's ``docker_yum_repo_url`` conditional
#: chain over ``ansible_facts.distribution`` -- ``fedora`` for Fedora, ``rhel``
#: for RedHat, and ``centos`` for everything else (CentOS, Rocky and AlmaLinux
#: all read the ``centos`` repo tree).
YUM_URL_SEGMENTS = {"Fedora": "fedora", "RedHat": "rhel"}
YUM_DEFAULT_SEGMENT = "centos"

#: The rpm short id of the Docker Release (CE rpm) signing key published at
#: ``<repo_url>/<segment>/gpg`` -- the last 8 hex digits of fingerprint
#: ``060A61C51B558A7F742B77AAC52FEB6B621E9F35``, verified from the live key in
#: ``examples/modules/dnf/dnf_key.py``. Pinned so :func:`redhat_setup`'s
#: ``dnf.key`` gates the import on the ``RpmGpgKeys`` fact (D9), the
#: ``EPEL_KEY_IDS`` precedent from repo_epel.
DOCKER_KEY_ID = "621E9F35"

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

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``docker`` role mapping, so ``users`` there sets
    :attr:`users`. Every default from both setup paths is carried over:
    :attr:`packages` and the ``apt_*``/``yum_*`` URL pair default to ``None``
    -- the role derives them from :attr:`edition` / :attr:`repo_url` (and, on
    RedHat, from the gathered distribution) when they are not overridden
    (:func:`docker_plan` reproduces the derivations) -- and
    :attr:`obsolete_packages` is ``None`` so each family falls back to its own
    ``vars/<OS>.yml`` list.

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
    #: ``docker_obsolete_packages``: ``None`` picks the family's own list --
    #: :data:`DEBIAN_OBSOLETE_PACKAGES` or :data:`REDHAT_OBSOLETE_PACKAGES`.
    obsolete_packages: tuple[str, ...] | None = None

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
    #: The legacy standalone docker-compose *binary* install: scoped out -- an
    #: opt-in path the upstream defaults turn off and the compose plugin
    #: package supersedes. A truthy value raises rather than silently dropping
    #: the feature.
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
    #: ``docker_yum_repo_url``: ``None`` derives
    #: ``<repo_url>/<segment>/docker-<edition>.repo``, the segment
    #: ``fedora``/``rhel``/``centos`` picked from the gathered distribution.
    yum_repo_url: str | None = None
    #: ``docker_yum_gpg_key``: ``None`` derives ``<repo_url>/<segment>/gpg`` --
    #: the key URL follows the *upstream* repo, not a mirror override, exactly
    #: as the role's defaults template it.
    yum_gpg_key: str | None = None
    #: ``docker_yum_repo_enable_test``: the ``enabled`` value written into the
    #: ``docker-<edition>-test`` section of the repo file.
    yum_repo_enable_test: str = "0"

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
                "docker_install_compose (the legacy standalone docker-compose "
                "binary install) is scoped out of this migration -- an opt-in "
                "path the upstream defaults turn off and the compose plugin "
                "package supersedes. Use docker_install_compose_plugin (the "
                "default) instead."
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

    @field_validator(
        "edition", "compose_package", "apt_filename", "apt_release_channel",
        "yum_repo_enable_test",
    )
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

    Everything is decided from the resolved variables plus the single
    ``LinuxDistribution`` read -- the Ubuntu release codename, the RedHat URL
    segment and major version -- and the plan is frozen right after that
    barrier, so every deploy below is a pure function of this one object. It
    replaces the example's ``settings`` dict keyed by host name and its
    whole-inventory ``names`` union: under a per-host body, the plan is a
    local.

    ``notify_names`` lists this host's notifying request names (the channel the
    two checkpoints are read against), and ``handler_action`` is the already
    mapped ``service_handler`` verb for :attr:`DockerVars.restart_handler_state`.
    The ``repofile``/``test_section``/``test_enabled``/``nightly_transform``/
    ``rhel8`` entries and the RedHat meanings of ``repo_url``/``gpg_key_url``
    (the ``.repo`` *file* URL and the RPM key) serve the RedHat arm only; the
    ``codename`` is the Ubuntu arm's (empty on RedHat, which needs no Suites).
    """

    family: str
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
    repofile: str
    test_section: str
    test_enabled: str
    nightly_transform: Callable[[str | None], str]
    rhel8: bool
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
    their names are deliberately absent. Neither setup arm notifies on either
    family, so the names are family-agnostic.
    """
    names = [NOTIFY_PACKAGE]
    if plan_fields["install_compose_plugin"]:
        names.append(NOTIFY_COMPOSE_PLUGIN)
    if plan_fields["daemon_json"] is not None:
        names.append(NOTIFY_DAEMON_JSON)
    if plan_fields["start_command"]:
        names.append(NOTIFY_EXEC_START)
    return tuple(names)


def drop_ini_section(section: str) -> Callable[[str | None], str]:
    """Ansible's ``ini_file {section, state: absent}`` (no option) as a pure transform.

    ``files.ini_option`` deliberately ships no section removal (D10), so the
    nightly-section drop is this factory's closure: it drops the ``[section]``
    header and every following line up to the next section header, returning
    the rest verbatim -- no other section, option, comment or blank line is
    touched, and no line endings are rewritten. An absent file (``None``)
    yields empty text, the same file an ``ini_file`` with the role's default
    ``create: yes`` would leave behind; when the section is not present the
    content passes through byte-identically, so the transform is a fixpoint.
    Built once per host in :func:`docker_plan`, as the request callables in
    security's :func:`resolve_plan` are, so it stays stable for the whole run.
    """
    header = f"[{section}]"

    def transform(current: str | None) -> str:
        if current is None:
            return ""
        kept: list[str] = []
        skipping = False
        for line in current.splitlines(keepends=True):
            stripped = line.strip()
            if stripped == header:
                skipping = True
                continue
            if skipping:
                if stripped.startswith("[") and stripped.endswith("]"):
                    skipping = False
                else:
                    continue
            kept.append(line)
        return "".join(kept)

    return transform


def docker_plan(hv: DockerVars, info: dict[str, Any], family: str) -> DockerPlan:
    """Freeze the per-host targets from the resolved vars and the gathered distribution.

    Reproduces the role's variable derivations, per family: ``packages`` from
    ``edition``, the obsolete list from ``vars/<OS>.yml`` when not overridden,
    and the repo/key URLs from ``repo_url`` through the ``include_vars``
    dispatch frozen as Python -- the apt pair under ``/ubuntu``, the yum pair
    under :data:`YUM_URL_SEGMENTS`' segment for the gathered distribution (the
    key URL still follows the *upstream* repo, not a mirror override) -- plus
    the ``to_nice_json`` render of ``daemon_options``. The state enums split
    into the operation's ``present``/``latest`` booleans.
    """
    is_redhat = family == "RedHat"
    daemon_json = render_daemon_json(hv.daemon_options)
    packages = hv.packages if hv.packages is not None else derive_packages(hv.edition)
    if is_redhat:
        # No Suites to feed: the codename is unused, while the distribution
        # picks the URL segment and the major version gates the RHEL 8
        # containerd block (upstream's ``| int == 8`` -- the fact parses
        # ``major`` to an int or int-string, so compare on its text).
        codename = ""
        segment = YUM_URL_SEGMENTS.get(str(info.get("name") or ""), YUM_DEFAULT_SEGMENT)
        repo_url = hv.yum_repo_url if hv.yum_repo_url is not None else (
            f"{hv.repo_url}/{segment}/docker-{hv.edition}.repo"
        )
        gpg_key_url = hv.yum_gpg_key if hv.yum_gpg_key is not None else (
            f"{hv.repo_url}/{segment}/gpg"
        )
        obsolete = (
            hv.obsolete_packages
            if hv.obsolete_packages is not None
            else REDHAT_OBSOLETE_PACKAGES
        )
        rhel8 = str(info.get("major", "")) == "8"
    else:
        codename = distribution_codename(info)
        repo_url = hv.apt_repo_url if hv.apt_repo_url is not None else (
            f"{hv.repo_url}/{UBUNTU_DISTRIBUTION}"
        )
        gpg_key_url = hv.apt_gpg_key if hv.apt_gpg_key is not None else (
            f"{hv.repo_url}/{UBUNTU_DISTRIBUTION}/gpg"
        )
        obsolete = (
            hv.obsolete_packages
            if hv.obsolete_packages is not None
            else DEBIAN_OBSOLETE_PACKAGES
        )
        rhel8 = False
    state = hv.packages_state
    compose_state = hv.compose_package_state
    service_state = hv.service_state
    fields = {
        "install_compose_plugin": hv.install_compose_plugin,
        "daemon_json": daemon_json,
        "start_command": hv.service_start_command,
    }
    return DockerPlan(
        family=family,
        codename=codename,
        packages=tuple(packages),
        packages_present=state != "absent",
        packages_latest=state == "latest",
        obsolete_packages=obsolete,
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
        repofile=f"/etc/yum.repos.d/docker-{hv.edition}.repo",
        test_section=f"docker-{hv.edition}-test",
        test_enabled=hv.yum_repo_enable_test,
        nightly_transform=drop_ini_section(f"docker-{hv.edition}-nightly"),
        rhel8=rhel8,
        users=tuple(hv.users),
        daemon_json=daemon_json,
        start_command=hv.service_start_command,
        notify_names=notifying_names(fields),
    )


# --- deploys -----------------------------------------------------------------


def setup(plan: DockerPlan) -> Deploy:
    """``tasks/setup-Debian.yml``: cleanup, dependencies, key and repository.

    The Ubuntu arm; the body dispatches a RedHat host to :func:`redhat_setup`
    instead. The role's ``notify: apt update`` + ``meta: flush_handlers`` pair is
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


def redhat_setup(plan: DockerPlan, key_stream: io.BytesIO | None) -> Deploy:
    """``tasks/setup-RedHat.yml``: purge, key, repo file, ini sections, RHEL 8 block.

    Task-for-task, in the file's own order. The obsolete purge is unconditional
    (upstream ``package`` task, no ``when``). The ``rpm_key``/``get_url`` pair
    runs only when the body's ``FileExists`` guard found no repo file
    (``key_stream`` is then the stream the ``control`` barrier built): the key
    is imported with the :data:`DOCKER_KEY_ID` gate and the ``.repo`` file is
    laid down by ``files.download``, both fetched control-side (D9). With
    ``add_repo`` the ini pair then runs on every pass, idempotent by content:
    the nightly-section drop is ``files.transform`` over
    :func:`drop_ini_section` (D10), the test toggle a
    ``files.ini_option(no_extra_spaces=True)`` -- upstream's ``create`` default
    is ``yes``, so the operation's is left in place. The RHEL 8 containerd
    block is the plan's ``rhel8`` gate, three plain ``dnf.packages`` requests.
    No request notifies ``restart docker``: none of these tasks carries a
    ``notify`` upstream either.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            dnf.packages,
            name="Ensure old versions of Docker are not installed.",
            packages=plan.obsolete_packages,
            present=False,
            sudo=True,
        )
        if key_stream is not None:
            yield op(
                dnf.key,
                name="Add Docker GPG key.",
                src=key_stream,
                keyid=DOCKER_KEY_ID,
                sudo=True,
            )
            yield op(
                files.download,
                name="Add Docker repository.",
                url=plan.repo_url,
                dest=plan.repofile,
                mode="0644",
                sudo=True,
            )
        if plan.add_repo:
            yield op(
                files.transform,
                name="Remove Docker Nightly repo.",
                path=plan.repofile,
                transform=plan.nightly_transform,
                mode="0644",
                sudo=True,
            )
            yield op(
                files.ini_option,
                name="Configure Docker Test repo.",
                path=plan.repofile,
                section=plan.test_section,
                option="enabled",
                value=plan.test_enabled,
                no_extra_spaces=True,
                mode="0644",
                sudo=True,
            )
        if plan.rhel8:
            yield op(
                dnf.packages,
                name="Ensure runc is not installed.",
                packages=("runc",),
                present=False,
                sudo=True,
            )
            yield op(
                dnf.packages,
                name="Ensure container-selinux is installed.",
                packages=("container-selinux",),
                sudo=True,
            )
            yield op(
                dnf.packages,
                name="Ensure containerd.io is installed.",
                packages=("containerd.io",),
                sudo=True,
            )

    return deploy


def packages(plan: DockerPlan) -> Deploy:
    """The Docker packages and the compose plugin (both notify ``restart docker``).

    The role carries two mutually exclusive install tasks per package set,
    split on the Ansible version; on modern Ansible (>= 2.12) both the Debian
    and the RedHat path are the ``allow_downgrade: true`` one, so each set is a
    single request through the family's own manager (D6).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if plan.family == "RedHat":
            yield op(
                dnf.packages,
                name=NOTIFY_PACKAGE,
                packages=plan.packages,
                present=plan.packages_present,
                latest=plan.packages_latest,
                allow_downgrade=True,
                sudo=True,
            )
            if plan.install_compose_plugin:
                yield op(
                    dnf.packages,
                    name=NOTIFY_COMPOSE_PLUGIN,
                    packages=plan.compose_package,
                    present=plan.compose_present,
                    latest=plan.compose_latest,
                    allow_downgrade=True,
                    sudo=True,
                )
            return
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
    """Install and configure Docker CE on Ubuntu and RedHat hosts, and run the role's handler.

    Args:
        v: The role's resolved variables, one :class:`DockerVars` per host
            (``resolve_vars(DockerVars, "docker", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        ValueError: The distribution read carries no ``VERSION_CODENAME`` (an
            Ubuntu host), or a host declared into the RedHat family gathers a
            distribution outside it (the certbot/repo_epel re-check), or
            ``docker_users`` is set while the ``docker`` group does not exist
            after the install (the role's getent task fails the same way). Each
            is raised inside the body, after the relevant barrier and before the
            requests it would gate, so nothing is half-applied. An
            ``install_compose`` override raises during variable resolution,
            before any host is contacted.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family not in TARGET_FAMILIES:
            return

        # Wave 0: the Ubuntu release codename for the repository's Suites, and
        # on RedHat the URL segment and RHEL-8 gate (the frozen include_vars
        # dispatch). Its barrier closes before any setup wave is built, so
        # every request below is a pure function of the frozen plan. A
        # declared-RedHat host is re-checked against the gathered distribution
        # here -- the only proof the family matches the machine, and the raise
        # runs before a single request.
        info = yield fact(LinuxDistribution)
        if host.family == "RedHat" and info.get("family") != "RedHat":
            raise ValueError(
                f"{host.name}: declared RedHat family but the distribution is "
                f"{info.get('name')!r} (family {info.get('family')!r}) -- "
                "check the inventory family"
            )
        plan = docker_plan(hv, info, host.family)

        key_stream = None
        if plan.family == "RedHat" and plan.add_repo:
            # The ``stat`` register guarding the key/repo pair (the repo_epel
            # shape): a host that already has the repo file fetches nothing.
            repo_exists = bool((yield fact(FileExists, path=plan.repofile)))
            if not repo_exists:
                # Control-side read whose URL came from a per-host fact, so it
                # cannot move into an ``async def`` factory. The barrier returns
                # the armored key's text, never a stream, so a journal could
                # serialise it; the stream is built here, once, in this single
                # pass.
                key_text = yield control(files.fetch_url, plan.gpg_key_url)
                key_stream = io.BytesIO(key_text.encode("utf-8"))

        # The setup wave, family's own branch, then the packages -- no barrier
        # between them; the driver applies each request before pulling the
        # next (the example's two-deploy split was a two-phase artifact).
        if plan.family == "RedHat":
            yield from redhat_setup(plan, key_stream)(host)
        else:
            yield from setup(plan)(host)
        yield from packages(plan)(host)
        packages_report = yield checkpoint("packages done")

        # The configure wave: the daemon configuration, closed by its own
        # checkpoint.
        yield from configure(plan)(host)
        configure_report = yield checkpoint("configure done")

        # The handler wave: the restart docker handler -- the role's second
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

        # The gate wave: the getent gate, then the memberships -- only for a
        # host that lists users. The role fails the play when the group is
        # absent (the docker packages were removed, say), and a barrier result
        # is data, so the raise is the role's, not the driver's.
        if plan.users:
            groups = yield fact(Groups)
            if "docker" not in groups:
                raise ValueError(
                    f"{host.name}: the docker group does not exist; install the "
                    "Docker packages before listing docker_users (the role's "
                    "getent task fails the same way)"
                )
            # The memberships wave.
            yield from users(plan)(host)

    return deploy


__all__ = [
    "DockerVars",
    "docker",
]
