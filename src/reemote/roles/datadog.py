# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The DataDog Agent role (migrated from ``datadog.datadog`` 5.6.0).
# 
# Installs and configures the Datadog Agent on Ubuntu hosts: the apt signing keys
# and repository, the agent package, ``/etc/datadog-agent`` configuration, the
# check configs under ``conf.d``, custom Python checks under ``checks.d``, the
# ``install_info`` stamp, the systemd units, and the role's two restart handlers.
# :func:`datadog` is the whole task list and :class:`DatadogVars` is
# ``defaults/main.yml``, bound from the inventory entry's ``datadog`` role
# mapping by :func:`~reemote.rolevars.resolve_vars`.
# 
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RHEL/CentOS/SUSE paths
# are out of scope; a host outside the target family returns before its first
# ``yield`` and emits no requests at all.
# 
# This is the corpus's first **async factory** (§16.3): the five signing-key URLs
# are inventory-wide constants, so the factory ``await``\\ s
# :func:`~reemote.modules.files.fetch_url` for each one before returning the
# deploy. That control-side IO runs even under ``dry`` (§6.3) -- this is the one
# role where ``--dry`` touches the network. The runner must ``await datadog(v)``.
# 
# The stream carries up to ten waves for a fresh Ubuntu host with all three
# disable-flags set, fewer once it converges:
# 
# 0. ``fact(AptPackages)`` -- the installed-package list the pre-cleanup gate reads.
# 1. ``fact(SystemdUnitFiles)`` -- the unit list the legacy-installer cleanup needs.
# 2. :func:`pre_cleanup` then :func:`bootstrap` then :func:`repository` then
#    :func:`agent` -- four stages, no barrier between them (the driver applies each
#    request before pulling the next).
# 3. ``fact(SystemdUnitFiles)`` -- post-install unit list for the configure plan.
# 4. ``fact(SystemdServiceState, "datadog-agent-exp.service")`` -- the experimental
#    agent's state.
# 5. :func:`configure` -- closed by ``checkpoint("configure done")``.
# 6. ``fact(FindFiles, CONF_D, ...)`` -- conditional; only when ``manage_config``
#    and at least one disable flag is set. Asymmetric barriers are legal (§10.3).
# 7. :func:`cleanup_conf_d` -- closed by ``checkpoint("conf.d cleanup done")``.
# 8. The two restart handlers, gated per-host on the checkpoint union.
# 
# Two checkpoints, unioned in the body -- the same shape as php (§18.6). The
# notification split reproduces the role's two handler channels: requests whose
# name is in :data:`PROBE_NOTIFY_NAMES` on a ``before_7180`` host notify
# ``datadog-agent-sysprobe``; everything else notifies ``datadog-agent``.
# 
# Deviations from the Ansible role, all deliberate:
# 
# * the five signing keys are fetched **control-side** (``files.fetch_url``) and
#   imported additively into the shared keyring with ``gpg.import_key(dest=...)``;
#   the role fetches host-side into a tempdir. The control machine needs egress to
#   ``s3.amazonaws.com``; the host downloads nothing.
# * ``CURRENT`` is fingerprint-pinned (the role leaves it unpinned), so a key
#   rotation is a one-line constant update instead of a manual keyring removal.
# * ``install_info`` stamps ``tool: reemote`` rather than ``tool: ansible``.
# * ``datadog_apt_repo`` (a custom repository) is **rejected** rather than
#   implemented: its stat/slurp/compare block needs a per-host remote content read
#   this module does not carry.
# * ``datadog_yum_gpgkey`` and ``datadog_zypper_gpgkey`` are rejected: removed from
#   the role upstream.
# * the ``__pycache__`` cleanup, the legacy-installer removal and the six legacy
#   ``apt_repository`` deprecations reproduce the role's ``failed_when: false``
#   tolerance as preconditions on the gathered facts rather than swallowing errors.
# * the ``conf.d`` sweep runs after the whole configure stage instead of between
#   ``datadog.yaml`` and the check installs; the two never touch the same file.
# * the six unprefixed probe-config variables (``system_probe_config``,
#   ``network_config``, ``service_monitoring_config``, ``runtime_security_config``,
#   ``system_probe_other_config``, ``compliance_config``) are read raw from
#   ``host.extra``, preserving the existing inventory contract -- they carry no
#   ``datadog_`` prefix upstream and renaming them would break every inventory.
# * every request carried ``when=is_ubuntu`` in the example; here the family guard
#   returns before the first barrier, so a host outside the target family is never
#   contacted and no request needs a guard.
# """

import io
import re
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core import op
from ..handlers import changed_hosts, restart_handler
from ..hosts import Host
from ..modules import apt, files, gpg, server, systemd
from ..modules.apt import AptPackages
from ..modules.files import FindFiles
from ..modules.systemd import SystemdServiceState, SystemdUnitFiles
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, checkpoint, fact

# --- role constants (defaults/main.yml, Ubuntu path only) --------------------

#: ``role_version`` -- stamped into ``install_info``.
ROLE_VERSION = "5.6.0"

CONFIG_DIR = "/etc/datadog-agent"
CONF_D = f"{CONFIG_DIR}/conf.d"
CHECKS_D = f"{CONFIG_DIR}/checks.d"
AGENT_USER = "dd-agent"
AGENT_GROUP = "dd-agent"

#: The shared, non-default keyring the role imports every signing key into.
USR_SHARE_KEYRING = "/usr/share/keyrings/datadog-archive-keyring.gpg"
SOURCES_LIST_D = "/etc/apt/sources.list.d"
CUSTOM_REPO_FILE = f"{SOURCES_LIST_D}/ansible_datadog_custom.list"
PYCACHE_DIR = "/opt/datadog-agent/python-scripts/__pycache__"

#: ``datadog_apt_cache_valid_time``.
CACHE_VALID_TIME = 3600

MANAGED_HEADER = (
    "# Managed by reemote -- reemote.roles.datadog "
    f"(datadog.datadog {ROLE_VERSION})"
)

#: The five signing-key URLs, in the role's own order.
APT_KEY_URLS = (
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_CURRENT.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_382E94DE.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_F14F620E.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_C0962C7D.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_06462314.public",
)
#: One fingerprint per URL, in the same order.
APT_KEY_FINGERPRINTS = (
    "5F1E256061D813B125E156E8E6266D4AC0962C7D",  # CURRENT
    "A2923DFF56EDA6E76E55E492D3A80E30382E94DE",
    "D75CEA17048B9ACBF186794B32637D44F14F620E",
    "5F1E256061D813B125E156E8E6266D4AC0962C7D",
    "D18886567EABAD8B2D2526900D826EB906462314",
)

AGENT_REPO_MAJORS = ("6", "7")
AGENT_REPOS = {
    major: f"deb [signed-by={USR_SHARE_KEYRING}] https://apt.datadoghq.com/ stable {major}"
    for major in AGENT_REPO_MAJORS
}

#: The six legacy lines ``pkg-debian.yml`` deprecates.
LEGACY_REPO_LINES = (
    "deb http://apt.datadoghq.com/ stable main",
    "deb http://apt.datadoghq.com/ stable 6",
    "deb http://apt.datadoghq.com/ stable 7",
    "deb https://apt.datadoghq.com/ stable main",
    "deb https://apt.datadoghq.com/ stable 6",
    "deb https://apt.datadoghq.com/ stable 7",
)

LEGACY_INSTALLER_UNITS = (
    "datadog-installer-exp.service",
    "datadog-installer.service",
)
LEGACY_INSTALLER_PATHS = tuple(
    f"{directory}/{unit}"
    for directory in (
        "/etc/systemd/system",
        "/lib/systemd/system",
        "/usr/lib/systemd/system",
    )
    for unit in LEGACY_INSTALLER_UNITS
)

DISABLED_AGENT_UNITS = (
    "datadog-agent",
    "datadog-agent-process",
    "datadog-agent-trace",
)

BOOTSTRAP_PACKAGES = ("apt-transport-https", "gnupg")
SIGNING_KEYS_PACKAGE = "datadog-signing-keys"

#: The five ``agent-linux.yml`` tests for "is a system-probe config defined".
PROBE_CONFIG_KEYS = (
    "system_probe_config",
    "network_config",
    "service_monitoring_config",
    "runtime_security_config",
    "system_probe_other_config",
)

#: Request names whose notify target switches to sysprobe on before_7180.
PROBE_NOTIFY_NAMES = (
    "Create system-probe configuration file",
    "Remove system-probe configuration file",
    "Create security-agent configuration file",
    "Remove security-agent configuration file",
)

#: The role's ``agent_regexp`` (``parse-version.yml``), verbatim.
AGENT_VERSION_RE = re.compile(
    r"(?:(?P<epoch>[0-9]+):)?(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<bugfix>[0-9]+)"
    r"(?P<suffix>(?:~|-)[^0-9\s-]+[^-\s]*)?(?:-(?P<release>[0-9]+))?"
)

#: Static request names that notify a restart handler.
NOTIFY_STATIC = (
    "Create main Datadog agent configuration file",
    "Create system-probe configuration file",
    "Remove system-probe configuration file",
    "Create security-agent configuration file",
    "Remove security-agent configuration file",
    "Add user to additional groups",
)


# --- variables ---------------------------------------------------------------


class DatadogVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``datadog`` role mapping, so ``api_key`` there sets
    :attr:`api_key`. The six probe-config variables
    (``system_probe_config``, ``network_config``, etc.) are **not** fields here:
    they are unprefixed top-level inventory keys upstream, not ``datadog``
    role variables, and are read raw from ``host.extra`` in the deploy body.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str = ""
    site: str = ""
    url: str = ""
    infrastructure_mode: str = ""
    remote_updates: bool = True
    config: dict[str, Any] = {}
    checks: dict[str, Any] = {}
    custom_checks: dict[str, str] = {}
    additional_checks: tuple[str, ...] = ()
    disable_untracked_checks: bool = False
    disable_default_checks: bool = False
    disable_example_checks: bool = False
    manage_config: bool = True
    enabled: bool = True
    skip_running_check: bool = False
    user: str = AGENT_USER
    group: str = AGENT_GROUP
    additional_groups: dict[str, Any] = {}
    agent_major_version: str = ""
    agent_version: str = ""
    agent_flavor: str = "datadog-agent"
    agent_allow_downgrade: bool = False
    apt_repo: str = ""
    apt_cache_valid_time: int = CACHE_VALID_TIME


# --- resolved settings -------------------------------------------------------


@dataclass(frozen=True)
class DatadogSettings:
    """One host's fully resolved role settings.

    Built by :func:`resolve_settings` from the typed variables and the raw
    ``host.extra`` probe configs. Frozen so no deploy body can mutate it.
    """

    api_key: str
    site: str
    url: str
    infrastructure_mode: str
    remote_updates: bool
    config: dict[str, Any]
    checks: dict[str, Any]
    custom_checks: dict[str, str]
    tracked_checks: frozenset[str]
    disable_untracked: bool
    disable_default: bool
    disable_example: bool
    manage_config: bool
    enabled: bool
    skip_running_check: bool
    agent_user: str
    agent_group: str
    additional_groups: tuple[str, ...]
    major: str
    flavor: str
    allow_downgrade: bool
    apt_repo: str
    cache_valid_time: int
    parsed: dict[str, str] | None
    pin: str
    before_7180: bool
    system_probe_config: dict[str, Any]
    network_config: dict[str, Any]
    service_monitoring_config: dict[str, Any]
    system_probe_other_config: dict[str, Any]
    runtime_security_config: dict[str, Any]
    compliance_config: dict[str, Any]


def resolve_settings(hv: DatadogVars, host: Host) -> DatadogSettings:
    """Merge the typed variables with the raw probe configs from ``host.extra``.

    Pure: no IO, no host read beyond ``host.extra`` (static inventory data).
    Reproduces ``set-parse-version.yml`` and ``parse-version.yml``.
    """
    version = hv.agent_version or ""
    parsed = parse_agent_version(version)
    major = hv.agent_major_version or ""
    if parsed is not None:
        if major and major != parsed["major"]:
            raise ValueError(
                f"{host.name}: the provided major version {major} is not compatible "
                f"with the version {parsed['major']} deduced from datadog_agent_version "
                f"({version}). Aborting."
            )
        major = parsed["major"]
    checks = hv.checks or {}
    if not isinstance(checks, dict):
        raise TypeError(f"{host.name}: datadog_checks must be a mapping")
    additional = hv.additional_checks or ()
    extra = host.extra
    return DatadogSettings(
        api_key=hv.api_key or "",
        site=hv.site or "",
        url=hv.url or "",
        infrastructure_mode=hv.infrastructure_mode or "",
        remote_updates=hv.remote_updates,
        config=hv.config or {},
        checks=checks,
        custom_checks=hv.custom_checks or {},
        tracked_checks=frozenset(checks) | frozenset(additional),
        disable_untracked=hv.disable_untracked_checks,
        disable_default=hv.disable_default_checks,
        disable_example=hv.disable_example_checks,
        manage_config=hv.manage_config,
        enabled=hv.enabled,
        skip_running_check=hv.skip_running_check,
        agent_user=hv.user,
        agent_group=hv.group,
        additional_groups=tuple(hv.additional_groups or ()),
        major=major or "7",
        flavor=hv.agent_flavor,
        allow_downgrade=hv.agent_allow_downgrade,
        apt_repo=hv.apt_repo or "",
        cache_valid_time=hv.apt_cache_valid_time,
        parsed=parsed,
        pin=debian_pin(parsed) if parsed is not None else "",
        before_7180=before_version(parsed, 18),
        system_probe_config=extra.get("system_probe_config") or {},
        network_config=extra.get("network_config") or {},
        service_monitoring_config=extra.get("service_monitoring_config") or {},
        system_probe_other_config=extra.get("system_probe_other_config") or {},
        runtime_security_config=extra.get("runtime_security_config") or {},
        compliance_config=extra.get("compliance_config") or {},
    )


# --- version helpers ---------------------------------------------------------


def parse_agent_version(version: str) -> dict[str, str] | None:
    """The role's ``parse-version.yml`` regex, as a dict of strings."""
    match = AGENT_VERSION_RE.search(version)
    if match is None:
        return None
    parsed = {key: value or "" for key, value in match.groupdict().items()}
    return {
        **parsed,
        "epoch": parsed["epoch"] or "1",
        "release": parsed["release"] or "1",
    }


def debian_pin(parsed: dict[str, str]) -> str:
    """``agent_datadog_agent_debian_version``: the apt ``name=version`` pin."""
    return (
        f"{parsed['epoch']}:{parsed['major']}.{parsed['minor']}.{parsed['bugfix']}"
        f"{parsed['suffix']}-{parsed['release']}"
    )


def before_version(parsed: dict[str, str] | None, minor: int, bugfix: int = 0) -> bool:
    """The role's ``agent_datadog_before_7NNN`` flags."""
    if parsed is None:
        return False
    if int(parsed["major"]) >= 8:
        return False
    return int(parsed["minor"]) < minor or (
        int(parsed["minor"]) == minor and int(parsed["bugfix"]) < bugfix
    )


def _reemote_version() -> str:
    """The installed reemote version, for the ``install_info`` stamp."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("reemote")
    except PackageNotFoundError:
        return "unknown"


# --- renderers ---------------------------------------------------------------


def to_nice_yaml(
    data: Any, *, indent: int = 4, sort_keys: bool = True, width: int = 80
) -> str:
    """Ansible's ``to_nice_yaml`` filter, with its defaults."""
    from ._render import yaml

    return yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        indent=indent,
        sort_keys=sort_keys,
        width=width,
    )


def indent_block(text: str, width: int = 2) -> str:
    """Jinja's ``indent(width, True)``: indent every non-blank line."""
    return "\n".join(
        (" " * width + line) if line.strip() else line for line in text.split("\n")
    )


def render_datadog_yaml(settings: DatadogSettings) -> str:
    """``templates/datadog.yaml.j2``, pre-rendered control-side."""
    config = settings.config
    parts = [MANAGED_HEADER, ""]
    if settings.site and "site" not in config:
        parts += [f"site: {settings.site}", ""]
    if settings.url and "dd_url" not in config:
        parts += [f"dd_url: {settings.url}", ""]
    if "api_key" not in config:
        parts += [f"api_key: {settings.api_key}", ""]
    if settings.infrastructure_mode and "infrastructure_mode" not in config:
        parts += [f"infrastructure_mode: {settings.infrastructure_mode}", ""]
    parts += [f"remote_updates: {settings.remote_updates}", ""]
    if config:
        parts += [to_nice_yaml(config).rstrip("\n"), ""]
    return "\n".join(parts)


def render_check_yaml(config: dict[str, Any]) -> str:
    """``templates/checks.yaml.j2``: ``indent=2, width=160, sort_keys=False``."""
    return (
        f"{MANAGED_HEADER}\n\n"
        + to_nice_yaml(config, indent=2, width=160, sort_keys=False)
        + "\n"
    )


def render_install_info(tool_version: str) -> str:
    """``templates/install_info.j2``."""
    return (
        "---\n"
        "install_method:\n"
        "  tool: reemote\n"
        f"  tool_version: reemote-{tool_version}\n"
        f"  installer_version: datadog_role-{ROLE_VERSION}\n"
    )


def render_system_probe_yaml(settings: DatadogSettings) -> str:
    """``templates/system-probe.yaml.j2``: four indented blocks plus a bare one."""
    parts = [MANAGED_HEADER, ""]
    for key in (
        "system_probe_config",
        "network_config",
        "service_monitoring_config",
        "runtime_security_config",
    ):
        block = getattr(settings, key)
        if block:
            parts += [f"{key}:", indent_block(to_nice_yaml(block)).rstrip("\n"), ""]
    if settings.system_probe_other_config:
        parts += [
            to_nice_yaml(settings.system_probe_other_config).rstrip("\n"),
            "",
        ]
    return "\n".join(parts)


def render_security_agent_yaml(settings: DatadogSettings) -> str:
    """``templates/security-agent.yaml.j2``: two indented blocks."""
    parts = [MANAGED_HEADER, ""]
    for key in ("runtime_security_config", "compliance_config"):
        block = getattr(settings, key)
        if block:
            parts += [f"{key}:", indent_block(to_nice_yaml(block)).rstrip("\n"), ""]
    return "\n".join(parts)


# --- plan builders -----------------------------------------------------------


def sysprobe_is_enabled(settings: DatadogSettings, units: frozenset[str]) -> bool:
    """``agent_datadog_sysprobe_enabled``, and the installed-unit precondition."""
    if "datadog-agent-sysprobe.service" not in units:
        return False
    for key in (
        "system_probe_config",
        "network_config",
        "service_monitoring_config",
    ):
        if getattr(settings, key).get("enabled"):
            return True
    return bool(settings.system_probe_other_config)


@dataclass(frozen=True)
class DatadogConfigurePlan:
    """Every rendered template and frozen read the configure deploy needs."""

    manage_config: bool
    check_running: bool
    enabled: bool
    exp_running: bool
    sysprobe_enabled: bool
    before_7180: bool
    units: frozenset[str]
    agent_user: str
    agent_group: str
    additional_groups: tuple[str, ...]
    datadog_yaml: str
    install_info: str
    system_probe_yaml: str | None
    security_agent_yaml: str | None
    checks: tuple[tuple[str, str], ...]
    custom_checks: tuple[tuple[str, str], ...]


def configure_plan(
    settings: DatadogSettings,
    units: frozenset[str],
    exp_state: str | None,
    tool_version: str,
    custom_check_texts: tuple[tuple[str, str], ...],
) -> DatadogConfigurePlan:
    """Render every template and freeze every read the configure deploy needs."""
    check_running = not settings.skip_running_check
    probe_defined = any(
        getattr(settings, key) for key in PROBE_CONFIG_KEYS
    )
    return DatadogConfigurePlan(
        manage_config=settings.manage_config,
        check_running=check_running,
        enabled=settings.enabled,
        exp_running=exp_state == "active",
        sysprobe_enabled=check_running and sysprobe_is_enabled(settings, units),
        before_7180=settings.before_7180,
        units=units,
        agent_user=settings.agent_user,
        agent_group=settings.agent_group,
        additional_groups=settings.additional_groups,
        datadog_yaml=render_datadog_yaml(settings),
        install_info=render_install_info(tool_version),
        system_probe_yaml=(
            render_system_probe_yaml(settings)
            if settings.manage_config and check_running and probe_defined
            else None
        ),
        security_agent_yaml=(
            render_security_agent_yaml(settings)
            if settings.manage_config and settings.runtime_security_config
            else None
        ),
        checks=tuple(
            (check, render_check_yaml(settings.checks[check]))
            for check in sorted(settings.checks)
        ),
        custom_checks=custom_check_texts,
    )


# --- deploys -----------------------------------------------------------------


def pre_cleanup(skip_install: bool, legacy_units: tuple[str, ...]) -> Deploy:
    """Legacy ``__pycache__`` removal + ``remove-legacy-installer.yml``."""

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if not skip_install:
            yield op(
                files.directory,
                name="Remove legacy Python script cache",
                path=PYCACHE_DIR,
                present=False,
                sudo=True,
            )
        yield op(
            apt.packages,
            name="Remove legacy installer package",
            packages=("datadog-installer",),
            present=False,
            sudo=True,
        )
        for unit in legacy_units:
            yield op(
                systemd.service,
                name=f"Stop legacy installer unit {unit}",
                service=unit,
                running=False,
                enabled=False,
                sudo=True,
            )
        for legacy_path in LEGACY_INSTALLER_PATHS:
            yield op(
                files.file,
                name=f"Remove legacy installer unit file {legacy_path}",
                path=legacy_path,
                present=False,
                sudo=True,
            )
        if legacy_units:
            yield op(
                systemd.daemon_reload,
                name="Reload systemd units",
                sudo=True,
            )

    return deploy


def bootstrap(settings: DatadogSettings) -> Deploy:
    """``pkg-debian.yml``'s first two tasks: apt-transport-https and gnupg."""

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for package in BOOTSTRAP_PACKAGES:
            yield op(
                apt.packages,
                name=f"Install {package}",
                packages=(package,),
                update=True,
                cache_valid_time=settings.cache_valid_time,
                sudo=True,
            )

    return deploy


def repository(settings: DatadogSettings, streams: tuple[Any, ...]) -> Deploy:
    """The keyring, the legacy sweep and the agent repo."""

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.file,
            name="Ensure Datadog apt keyring exists",
            path=USR_SHARE_KEYRING,
            present=True,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        for url, stream, fingerprint in zip(
            APT_KEY_URLS, streams, APT_KEY_FINGERPRINTS
        ):
            yield op(
                gpg.import_key,
                name=f"Import Datadog apt signing key {Path(url).stem}",
                src=stream,
                dest=USR_SHARE_KEYRING,
                keyid=fingerprint,
                sudo=True,
            )
        for line in LEGACY_REPO_LINES:
            yield op(
                apt.repo,
                name=f"Deprecate legacy Datadog repository {line!r}",
                src=line,
                present=False,
                update_cache=True,
                sudo=True,
            )
        for major in AGENT_REPO_MAJORS:
            yield op(
                apt.repo,
                name=f"Ensure Datadog repository for Agent {major}",
                src=AGENT_REPOS[major],
                present=major == settings.major,
                filename=f"ansible_datadog_{major}",
                update_cache=True,
                sudo=True,
            )
        yield op(
            files.file,
            name="Remove Datadog custom repository file",
            path=CUSTOM_REPO_FILE,
            present=False,
            sudo=True,
        )

    return deploy


def agent(settings: DatadogSettings) -> Deploy:
    """The agent package (pinned or latest) plus signing-keys."""

    def deploy(host: Host) -> Generator[StageItem, Any]:
        flavor = settings.flavor
        pin = settings.pin
        if pin:
            yield op(
                apt.packages,
                name=f"Install pinned {flavor} package",
                packages=(f"{flavor}={pin}",),
                allow_downgrades=settings.allow_downgrade,
                update=True,
                cache_valid_time=settings.cache_valid_time,
                sudo=True,
            )
        else:
            yield op(
                apt.packages,
                name=f"Install latest {flavor} package",
                packages=(flavor,),
                latest=True,
                update=True,
                cache_valid_time=settings.cache_valid_time,
                sudo=True,
            )
        yield op(
            apt.packages,
            name=f"Install latest {SIGNING_KEYS_PACKAGE} package",
            packages=(SIGNING_KEYS_PACKAGE,),
            latest=True,
            sudo=True,
        )

    return deploy


def configure(plan: DatadogConfigurePlan) -> Deploy:
    """Configuration files, check configs, custom checks, and systemd units."""

    def deploy(host: Host) -> Generator[StageItem, Any]:
        agent_user = plan.agent_user
        agent_group = plan.agent_group

        if plan.additional_groups:
            yield op(
                server.user,
                name="Add user to additional groups",
                user=agent_user,
                groups=plan.additional_groups,
                append=True,
                ensure_home=False,
                sudo=True,
            )

        if plan.manage_config:
            yield op(
                files.directory,
                name="Create Datadog agent config directory",
                path=CONFIG_DIR,
                mode="0755",
                user=agent_user,
                group=agent_group,
                sudo=True,
            )
            yield op(
                files.install,
                name="Create main Datadog agent configuration file",
                content=plan.datadog_yaml,
                dest=f"{CONFIG_DIR}/datadog.yaml",
                mode="0640",
                user=agent_user,
                group=agent_group,
                sudo=True,
            )
            for check_name, check_content in plan.checks:
                yield op(
                    files.directory,
                    name=f"Ensure configuration directory for {check_name}",
                    path=f"{CONF_D}/{check_name}.d",
                    mode="0755",
                    user=agent_user,
                    group=agent_group,
                    sudo=True,
                )
                yield op(
                    files.install,
                    name=f"Create a configuration file for {check_name}",
                    content=check_content,
                    dest=f"{CONF_D}/{check_name}.d/conf.yaml",
                    mode="0640",
                    user=agent_user,
                    group=agent_group,
                    sudo=True,
                )
                yield op(
                    files.file,
                    name=f"Remove old configuration file for {check_name}",
                    path=f"{CONF_D}/{check_name}.yaml",
                    present=False,
                    sudo=True,
                )

        for check_name, check_content in plan.custom_checks:
            yield op(
                files.install,
                name=f"Create custom check file for {check_name}",
                content=check_content,
                dest=f"{CHECKS_D}/{check_name}.py",
                mode="0755",
                user=agent_user,
                group=agent_group,
                sudo=True,
            )

        yield op(
            files.install,
            name="Create installation information file",
            content=plan.install_info,
            dest=f"{CONFIG_DIR}/install_info",
            mode="0644",
            user=agent_user,
            group=agent_group,
            sudo=True,
        )

        if plan.manage_config and plan.check_running:
            if plan.system_probe_yaml is not None:
                yield op(
                    files.install,
                    name="Create system-probe configuration file",
                    content=plan.system_probe_yaml,
                    dest=f"{CONFIG_DIR}/system-probe.yaml",
                    mode="0640",
                    user="root",
                    group=agent_group,
                    sudo=True,
                )
            else:
                yield op(
                    files.file,
                    name="Remove system-probe configuration file",
                    path=f"{CONFIG_DIR}/system-probe.yaml",
                    present=False,
                    sudo=True,
                )

        if plan.check_running:
            units = plan.units
            if (
                plan.enabled
                and not plan.exp_running
                and "datadog-agent.service" in units
            ):
                yield op(
                    systemd.service,
                    name="Ensure datadog-agent is running",
                    service="datadog-agent",
                    running=True,
                    enabled=True,
                    sudo=True,
                )
            if (
                plan.enabled
                and plan.sysprobe_enabled
                and "datadog-agent-sysprobe.service" in units
            ):
                yield op(
                    systemd.service,
                    name="Ensure datadog-agent-sysprobe is running",
                    service="datadog-agent-sysprobe",
                    running=True,
                    enabled=True,
                    sudo=True,
                )
            if not plan.enabled:
                for unit in DISABLED_AGENT_UNITS:
                    if f"{unit}.service" in units:
                        yield op(
                            systemd.service,
                            name=f"Ensure {unit} is not running",
                            service=unit,
                            running=False,
                            enabled=False,
                            sudo=True,
                        )
                if "datadog-agent-security.service" in units:
                    yield op(
                        systemd.service,
                        name="Ensure datadog-agent-security is not running",
                        service="datadog-agent-security",
                        running=False,
                        enabled=False,
                        sudo=True,
                    )
            if (
                plan.before_7180
                and (not plan.enabled or not plan.sysprobe_enabled)
                and "datadog-agent-sysprobe.service" in units
            ):
                yield op(
                    systemd.service,
                    name="Ensure datadog-agent-sysprobe is stopped",
                    service="datadog-agent-sysprobe",
                    running=False,
                    enabled=False,
                    sudo=True,
                )

        if plan.manage_config:
            if plan.security_agent_yaml is not None:
                yield op(
                    files.install,
                    name="Create security-agent configuration file",
                    content=plan.security_agent_yaml,
                    dest=f"{CONFIG_DIR}/security-agent.yaml",
                    mode="0640",
                    user="root",
                    group=agent_group,
                    sudo=True,
                )
            else:
                yield op(
                    files.file,
                    name="Remove security-agent configuration file",
                    path=f"{CONFIG_DIR}/security-agent.yaml",
                    present=False,
                    sudo=True,
                )

    return deploy


def cleanup_conf_d(settings: DatadogSettings, found: tuple[str, ...]) -> Deploy:
    """The role's three ``conf.d`` cleanup loops."""

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if not settings.manage_config:
            return
        tracked = settings.tracked_checks
        for stale in sorted(found):
            check_dir = Path(stale).parent
            if check_dir.parent != Path(CONF_D) or not check_dir.name.endswith(".d"):
                continue
            check = check_dir.name.removesuffix(".d")
            if check in tracked:
                continue
            suffix = Path(stale).name
            if suffix == "conf.yaml" and settings.disable_untracked:
                yield op(
                    files.file,
                    name=f"Delete untracked check {check}",
                    path=stale,
                    present=False,
                    sudo=True,
                )
            elif suffix == "conf.yaml.default" and settings.disable_default:
                yield op(
                    files.file,
                    name=f"Delete default check {check}",
                    path=stale,
                    present=False,
                    sudo=True,
                )
            elif suffix == "conf.yaml.example" and settings.disable_example:
                yield op(
                    files.file,
                    name=f"Delete example check {check}",
                    path=stale,
                    present=False,
                    sudo=True,
                )

    return deploy


# --- the role ----------------------------------------------------------------


async def datadog(v: VarsSet[DatadogVars]) -> Deploy:
    """Install and configure the Datadog Agent on Ubuntu hosts.

    This is an **async** factory: the five signing-key downloads are
    inventory-wide constants, so they run here -- even under ``dry`` (§6.3).
    The runner must ``await datadog(v)``.

    Args:
        v: The role's resolved variables, one :class:`DatadogVars` per host
            (``resolve_vars(DatadogVars, "datadog", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``.

    Raises:
        ValueError: ``datadog_api_key`` is empty while ``manage_config`` is true,
            or ``datadog_apt_repo`` is set (scoped out), or a removed yum/zypper
            gpgkey variable is present. Raised inside the body before any request.
    """
    # Control-side IO: fetch the five signing keys once for the whole inventory.
    key_texts = tuple([await files.fetch_url(url) for url in APT_KEY_URLS])

    # Control-side IO: pre-read custom check files (controller paths).
    custom_check_texts: dict[str, tuple[tuple[str, str], ...]] = {}
    for host_name, hv in v.values.items():
        if hv.custom_checks:
            custom_check_texts[host_name] = tuple(
                (check, Path(source).read_text(encoding="utf-8"))
                for check, source in sorted(hv.custom_checks.items())
            )

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        settings = resolve_settings(hv, host)

        # Validation gates -- before any host is contacted beyond the facts.
        if settings.manage_config and not settings.api_key:
            raise ValueError(
                f"{host.name}: datadog_api_key is mandatory when using managed "
                "config. Add it to the host's inventory entry."
            )
        if settings.apt_repo:
            raise ValueError(
                f"{host.name}: datadog_apt_repo (a custom apt repository) is "
                "scoped out of this migration. Unset it to use the official "
                "repository."
            )
        for removed in ("datadog_yum_gpgkey", "datadog_zypper_gpgkey"):
            if host.extra.get(removed):
                raise ValueError(
                    f"{host.name}: {removed} has been removed from the role; "
                    "use the distribution's own key management instead."
                )

        # Wave 0: the installed-package list.
        installed = yield fact(AptPackages)
        # Wave 1: the unit list for legacy-installer cleanup.
        units_before = yield fact(SystemdUnitFiles)

        # Derive pre-cleanup gates from the facts.
        versions = {
            entry["name"]: tuple(entry["versions"]) for entry in installed
        }
        skip_install = bool(settings.pin) and settings.pin in versions.get(
            settings.flavor, ()
        )
        legacy_units = tuple(
            unit for unit in LEGACY_INSTALLER_UNITS if unit in units_before
        )

        # Stages 2: pre-cleanup, bootstrap, repository, agent -- no barrier
        # between them; the driver applies each request before pulling the next.
        yield from pre_cleanup(skip_install, legacy_units)(host)
        yield from bootstrap(settings)(host)
        streams = tuple(io.BytesIO(text.encode("utf-8")) for text in key_texts)
        yield from repository(settings, streams)(host)
        yield from agent(settings)(host)

        # Wave 3: post-install unit list.
        units_after = yield fact(SystemdUnitFiles)
        # Wave 4: the experimental agent's state.
        exp_state = yield fact(SystemdServiceState, "datadog-agent-exp.service")

        # Build the configure plan.
        tool_version = _reemote_version()
        plan = configure_plan(
            settings,
            frozenset(units_after),
            exp_state,
            tool_version,
            custom_check_texts.get(host.name, ()),
        )

        # Wave 5: configure, closed by its checkpoint.
        yield from configure(plan)(host)
        configure_report = yield checkpoint("configure done")

        # Wave 6 (conditional): the conf.d enumeration, after configure applied.
        found: tuple[str, ...] = ()
        if settings.manage_config and (
            settings.disable_untracked
            or settings.disable_default
            or settings.disable_example
        ):
            found = yield fact(FindFiles, CONF_D, fname="conf.yaml*", maxdepth=2)

        # Wave 7: conf.d cleanup, closed by its checkpoint.
        yield from cleanup_conf_d(settings, found)(host)
        cleanup_report = yield checkpoint("conf.d cleanup done")

        # Handler notification split -- two checkpoints, unioned.
        configure_names = list(NOTIFY_STATIC)
        for check_name, _ in plan.checks:
            configure_names.append(f"Create a configuration file for {check_name}")
            configure_names.append(f"Remove old configuration file for {check_name}")
        for check_name, _ in plan.custom_checks:
            configure_names.append(f"Create custom check file for {check_name}")

        cleanup_names: list[str] = []
        for stale in found:
            check = Path(stale).parent.name.removesuffix(".d")
            cleanup_names += [
                f"Delete untracked check {check}",
                f"Delete default check {check}",
                f"Delete example check {check}",
            ]

        notified = bool(
            changed_hosts(configure_report, tuple(configure_names))
            & {host.name}
        ) or bool(
            changed_hosts(cleanup_report, tuple(cleanup_names)) & {host.name}
        )
        probe_notified = (
            settings.before_7180
            and host.name
            in changed_hosts(configure_report, PROBE_NOTIFY_NAMES)
        )
        agent_notified = notified and not probe_notified

        sysprobe_restart = (
            probe_notified
            and plan.enabled
            and plan.sysprobe_enabled
            and "datadog-agent-sysprobe.service" in plan.units
        )
        agent_restart = (
            agent_notified
            and plan.enabled
            and not plan.exp_running
            and "datadog-agent.service" in plan.units
        )

        # Wave 8: the two restart handlers.
        yield from restart_handler(
            frozenset({host.name}) if sysprobe_restart else frozenset(),
            ("datadog-agent-sysprobe",),
        )(host)
        yield from restart_handler(
            frozenset({host.name}) if agent_restart else frozenset(),
            ("datadog-agent",),
        )(host)

    return deploy


__all__ = ["DatadogVars", "datadog"]
