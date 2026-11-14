# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Migration of the DataDog Ansible role (``datadog.datadog`` 5.6.0) to reemote.

The role at ``https://github.com/DataDog/ansible-datadog`` installs and
configures the Datadog Agent. This program is its **Ubuntu/Debian default
path** expressed as a reemote deploy: the apt repository and signing keys,
the agent package, ``/etc/datadog-agent`` configuration, the check configs
under ``conf.d``, the custom Python checks under ``checks.d``, the
``install_info`` stamp, the systemd units, and the role's two restart
handlers. Every role variable is read from the inventory entry's extra
keys (reemote's ``host_vars`` mechanism), so ``datadog_api_key`` and friends
live beside ``host``/``connection`` in ``inventory.json``.

Target: hosts in the inventory group ``"ubuntu"``. Every request carries
``when=is_ubuntu``, and a skipped request is never materialized -- the
other hosts in the shared inventory are not even contacted.

Why the program is a *sequence* of deploys rather than one
-------------------------------------------------------

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
is full of ``register:`` + ``when:`` reads, ``service_facts`` probes,
``find`` enumerations and fact-fed templates -- all of which are mutable
reads a single deploy may not branch on. Each one becomes a
**gather -> snapshot -> factory** step: ``gather_fact`` between two complete
runs, freeze the answer into a per-host dict, close a deploy factory over
it. The barriers below are therefore load-bearing, not stylistic:

1. ``build_pre_cleanup``   -- legacy installer removal (needs the unit list)
2. ``deploy_bootstrap``    -- apt-transport-https + gnupg (branch-free)
3. ``build_repository``    -- keyring, legacy repo sweep, agent repo
4. ``build_agent``         -- the agent + signing-keys packages
5. ``deploy_identity``     -- group memberships (see GAP-2, #12)
6. ``build_configure``     -- every config file and the systemd units
7. ``build_cleanup_conf_d``-- the untracked/default/example check sweep
8. ``compose(restart_handler(...), restart_handler(...))`` -- the handlers

Implementation gaps
-------------------

Five genuine library gaps were proven while mapping the role. GAP-1 (#11,
additive multi-key import into a non-default keyring) is now **closed**:
``gpg.import_key`` gained ``dest=``, so the five signing keys import
additively into the shared keyring, each fingerprint-pinned. The four
remaining gaps are worked around here, marked ``GAP-n`` at the point of
use, and tracked as GitHub issues; the workarounds carry a documented
deviation. They are all bridgeable, so the migration completes -- a second
pass will replace them once the library extensions land:

* GAP-2 (#12) -- unprivileged read-back of ``0640`` service-owned files
* GAP-3 (#13) -- ``apt.packages(latest=True)`` is non-idempotent
* GAP-4 (#14) -- ``apt.repo`` has no ``update_cache``
* GAP-5 (#15) -- ``apt.repo`` removal is file-scoped, no source provenance

See ``ansible-datadog.md`` for the full gap register, the coverage table
and every deliberate deviation.

Prerequisites: PyYAML (``--with pyyaml``) renders the role's
``to_nice_yaml`` templates; it is deliberately *not* a reemote dependency.

Run it with::

    uv run --with pyyaml python examples/roles/ansible-datadog/ansible-datadog.py
"""

import asyncio
import io
import json
import re
import sys
import urllib.request
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    AptPackages,
    AptSources,
    AptUpgradable,
    CommandError,
    FindFiles,
    FindInFiles,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    SystemdServiceState,
    SystemdUnitFiles,
    apt,
    changed_hosts,
    compose,
    files,
    format_log,
    format_plan,
    gather_fact,
    gpg,
    op,
    restart_handler,
    run,
    server,
    systemd,
    validate_inventory,
)

# --- role constants (defaults/main.yml, Ubuntu path only) --------------------

#: ``role_version`` -- stamped into ``install_info``.
ROLE_VERSION = "5.6.0"

CONFIG_DIR = "/etc/datadog-agent"
CONF_D = f"{CONFIG_DIR}/conf.d"
CHECKS_D = f"{CONFIG_DIR}/checks.d"
AGENT_USER = "dd-agent"
AGENT_GROUP = "dd-agent"

#: ``datadog_apt_usr_share_keyring`` -- the shared, non-default keyring the
#: role imports every signing key into and references from ``signed-by``.
USR_SHARE_KEYRING = "/usr/share/keyrings/datadog-archive-keyring.gpg"
SOURCES_LIST_D = "/etc/apt/sources.list.d"
CUSTOM_REPO_FILE = f"{SOURCES_LIST_D}/ansible_datadog_custom.list"
PYCACHE_DIR = "/opt/datadog-agent/python-scripts/__pycache__"

#: ``datadog_apt_cache_valid_time``.
CACHE_VALID_TIME = 3600

#: Ansible's ``ansible_managed`` is a *timestamped* string, which a
#: deterministic deploy may not render. A static header replaces it: the
#: file still says who manages it, without a per-run mtime in the content.
MANAGED_HEADER = (
    "# Managed by reemote -- examples/roles/ansible-datadog "
    f"(datadog.datadog {ROLE_VERSION})"
)

#: ``datadog_apt_default_keys``: CURRENT plus four named keys, in the role's
#: own URL order.
APT_KEY_URLS = (
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_CURRENT.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_382E94DE.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_F14F620E.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_C0962C7D.public",
    "https://s3.amazonaws.com/public-signing-keys/DATADOG_APT_KEY_06462314.public",
)
#: One fingerprint per URL, in the same order. The role leaves CURRENT
#: unpinned (it rotates); this migration pins it to the fingerprint it serves
#: today so every import is idempotent and a CURRENT rotation becomes a
#: one-line constant update instead of a manual keyring removal. CURRENT
#: currently serves the same key as C0962C7D (the 2023-04-20 APT key), so
#: those two imports deduplicate against each other.
APT_KEY_FINGERPRINTS = (
    "5F1E256061D813B125E156E8E6266D4AC0962C7D",  # CURRENT
    "A2923DFF56EDA6E76E55E492D3A80E30382E94DE",
    "D75CEA17048B9ACBF186794B32637D44F14F620E",
    "5F1E256061D813B125E156E8E6266D4AC0962C7D",
    "D18886567EABAD8B2D2526900D826EB906462314",
)

#: ``datadog_agent{6,7}_apt_repo`` -- the trailing slash on the URL is the
#: role's own and is preserved: ``AptSources`` keeps it verbatim, so dropping
#: it would make every presence comparison miss.
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
LEGACY_REPO_URLS = frozenset(
    {"http://apt.datadoghq.com/", "https://apt.datadoghq.com/"}
)
LEGACY_REPO_COMPONENTS = frozenset({"main", "6", "7"})

#: GAP-5 (#15) workaround: ``AptSources`` reports *what* is configured
#: but not *which file* holds it, and ``apt.repo(present=False)`` only edits
#: the one file ``filename`` names. One recursive content grep recovers the
#: provenance the fact drops, so the sweep can be fanned out per file.
#: The ERE matches exactly the six legacy lines: no ``[options]`` block (so
#: a modern ``signed-by`` line is never touched), ``stable`` suite, and one
#: of the three deprecated components.
LEGACY_REPO_PATTERN = r"^deb +https?://apt\.datadoghq\.com/ +stable +(main|6|7) *$"

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

#: ``agent-linux.yml``'s stopped/disabled loop when ``datadog_enabled`` is false.
DISABLED_AGENT_UNITS = (
    "datadog-agent",
    "datadog-agent-process",
    "datadog-agent-trace",
)

BOOTSTRAP_PACKAGES = ("apt-transport-https", "gnupg")
SIGNING_KEYS_PACKAGE = "datadog-signing-keys"

#: The five ``agent-linux.yml`` tests for "is a system-probe config defined".
#: Four of them are role defaults, so the test is always true and the
#: template's ``file absent`` counterpart is unreachable by default -- the
#: same dead branch the role has.
PROBE_CONFIG_KEYS = (
    "system_probe_config",
    "network_config",
    "service_monitoring_config",
    "runtime_security_config",
    "system_probe_other_config",
)

#: The request names whose notify target switches to
#: ``restart datadog-agent-sysprobe`` on an agent older than 6/7.18.
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

#: Request names that notify ``restart datadog-agent`` and carry no
#: per-item suffix. The per-check names are added by ``notifying_names``.
NOTIFY_STATIC = (
    "Create main Datadog agent configuration file",
    "Create system-probe configuration file",
    "Remove system-probe configuration file",
    "Create security-agent configuration file",
    "Remove security-agent configuration file",
    "Add user to additional groups",
)

#: examples/roles/ansible-datadog/ansible-datadog.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

#: The role defaults this migration honours. ``runtime_security_config`` and
#: ``compliance_config`` are deliberately absent: the role does not define
#: them either, and their absence is what makes the security-agent template
#: conditional rather than always-on.
ROLE_DEFAULTS: dict[str, Any] = {
    "datadog_api_key": "",
    "datadog_site": "",
    "datadog_url": "",
    "datadog_infrastructure_mode": "",
    "datadog_remote_updates": True,
    "datadog_config": {},
    "datadog_checks": {},
    "datadog_custom_checks": {},
    "datadog_additional_checks": [],
    "datadog_disable_untracked_checks": False,
    "datadog_disable_default_checks": False,
    "datadog_disable_example_checks": False,
    "datadog_manage_config": True,
    "datadog_enabled": True,
    "datadog_skip_running_check": False,
    "datadog_user": AGENT_USER,
    "datadog_group": AGENT_GROUP,
    "datadog_additional_groups": {},
    "datadog_agent_major_version": "",
    "datadog_agent_version": "",
    "datadog_agent_flavor": "datadog-agent",
    "datadog_agent_allow_downgrade": False,
    "datadog_apt_repo": "",
    "datadog_apt_cache_valid_time": CACHE_VALID_TIME,
    "system_probe_config": {},
    "network_config": {},
    "service_monitoring_config": {},
    "system_probe_other_config": {},
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
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data and stays a stable module-level
    function, so both deploy phases reach the same eligibility decision.
    A skipped request is never materialized, so the Alpine and CentOS
    hosts in the shared inventory are not contacted at all.
    """
    return host.group == "ubuntu"


def parse_agent_version(version: str) -> dict[str, str] | None:
    """The role's ``parse-version.yml`` regex, as a dict of strings.

    Returns None when ``version`` does not carry a ``major.minor.bugfix``
    core -- the role's ``regex_search`` yields an empty list then, and
    every ``agent_datadog_*`` version fact stays undefined.
    """
    match = AGENT_VERSION_RE.search(version)
    if match is None:
        return None
    parsed = {key: value or "" for key, value in match.groupdict().items()}
    # The role's two "fill empty with default" tasks.
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
    """The role's ``agent_datadog_before_7NNN`` flags.

    Every one of them is gated on ``agent_datadog_major is defined and
    agent_datadog_minor is defined`` -- facts only ``parse-version.yml``
    sets -- so an unpinned deploy reports False for all of them.
    """
    if parsed is None:
        return False
    if int(parsed["major"]) >= 8:
        return False
    return int(parsed["minor"]) < minor or (
        int(parsed["minor"]) == minor and int(parsed["bugfix"]) < bugfix
    )


def role_settings(host: Host) -> dict[str, Any]:
    """Freeze one host's role variables: defaults overlaid with ``host.extra``.

    Reads only static inventory data, so a deploy body may call it and
    branch on the result -- both phases see the same dict. The version
    parsing and the major-version compatibility check happen here too,
    reproducing ``set-parse-version.yml`` and ``parse-version.yml``.
    """
    merged = {**ROLE_DEFAULTS, **host.extra}
    version = str(merged["datadog_agent_version"] or "")
    parsed = parse_agent_version(version)
    major = str(merged["datadog_agent_major_version"] or "")
    if parsed is not None:
        if major and major != parsed["major"]:
            raise ValueError(
                f"{host.name}: the provided major version {major} is not compatible "
                f"with the version {parsed['major']} deduced from datadog_agent_version "
                f"({version}). Aborting."
            )
        major = parsed["major"]
    checks = merged["datadog_checks"] or {}
    # sanitize-checks.yml's `assert: agent_datadog_checks is mapping`.
    if not isinstance(checks, dict):
        raise TypeError(f"{host.name}: datadog_checks must be a mapping")
    additional = tuple(merged["datadog_additional_checks"] or ())
    return {
        "api_key": str(merged["datadog_api_key"] or ""),
        "site": str(merged["datadog_site"] or ""),
        "url": str(merged["datadog_url"] or ""),
        "infrastructure_mode": str(merged["datadog_infrastructure_mode"] or ""),
        "remote_updates": merged["datadog_remote_updates"],
        "config": merged["datadog_config"] or {},
        "checks": checks,
        "custom_checks": merged["datadog_custom_checks"] or {},
        "tracked_checks": frozenset(checks) | frozenset(additional),
        "disable_untracked": bool(merged["datadog_disable_untracked_checks"]),
        "disable_default": bool(merged["datadog_disable_default_checks"]),
        "disable_example": bool(merged["datadog_disable_example_checks"]),
        "manage_config": bool(merged["datadog_manage_config"]),
        "enabled": bool(merged["datadog_enabled"]),
        "skip_running_check": bool(merged["datadog_skip_running_check"]),
        "agent_user": str(merged["datadog_user"]),
        "agent_group": str(merged["datadog_group"]),
        "additional_groups": tuple(merged["datadog_additional_groups"] or ()),
        "major": major or "7",
        "flavor": str(merged["datadog_agent_flavor"]),
        "allow_downgrade": bool(merged["datadog_agent_allow_downgrade"]),
        "apt_repo": str(merged["datadog_apt_repo"] or ""),
        "cache_valid_time": int(merged["datadog_apt_cache_valid_time"]),
        "parsed": parsed,
        "pin": debian_pin(parsed) if parsed is not None else "",
        "before_7180": before_version(parsed, 18),
        "system_probe_config": merged["system_probe_config"] or {},
        "network_config": merged["network_config"] or {},
        "service_monitoring_config": merged["service_monitoring_config"] or {},
        "system_probe_other_config": merged["system_probe_other_config"] or {},
        "runtime_security_config": merged.get("runtime_security_config") or {},
        "compliance_config": merged.get("compliance_config") or {},
    }


def to_nice_yaml(
    data: Any, *, indent: int = 4, sort_keys: bool = True, width: int = 80
) -> str:
    """Ansible's ``to_nice_yaml`` filter, with its defaults.

    PyYAML is imported *here*, not at module scope: it is not a reemote
    runtime dependency, so importing this module never requires it. It is
    in the project's ``dev`` group (the offline example tests render these
    templates) and ``--with pyyaml`` supplies it for a standalone run.
    """
    import yaml

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


def render_datadog_yaml(settings: dict[str, Any]) -> str:
    """``templates/datadog.yaml.j2``, pre-rendered control-side."""
    config = settings["config"]
    parts = [MANAGED_HEADER, ""]
    if settings["site"] and "site" not in config:
        parts += [f"site: {settings['site']}", ""]
    if settings["url"] and "dd_url" not in config:
        parts += [f"dd_url: {settings['url']}", ""]
    if "api_key" not in config:
        parts += [f"api_key: {settings['api_key']}", ""]
    if settings["infrastructure_mode"] and "infrastructure_mode" not in config:
        parts += [f"infrastructure_mode: {settings['infrastructure_mode']}", ""]
    parts += [f"remote_updates: {settings['remote_updates']}", ""]
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
    """``templates/install_info.j2``.

    Deviation: the role stamps ``tool: ansible`` and Ansible's own version.
    reemote performed the install, so the stamp names reemote -- the file
    exists to tell Datadog how the agent got there, and lying about it
    would defeat its purpose. ``installer_version`` keeps the role version.
    """
    return (
        "---\n"
        "install_method:\n"
        "  tool: reemote\n"
        f"  tool_version: reemote-{tool_version}\n"
        f"  installer_version: datadog_role-{ROLE_VERSION}\n"
    )


def render_system_probe_yaml(settings: dict[str, Any]) -> str:
    """``templates/system-probe.yaml.j2``: four indented blocks plus a bare one."""
    parts = [MANAGED_HEADER, ""]
    for key in (
        "system_probe_config",
        "network_config",
        "service_monitoring_config",
        "runtime_security_config",
    ):
        block = settings[key]
        if block:
            parts += [f"{key}:", indent_block(to_nice_yaml(block)).rstrip("\n"), ""]
    if settings["system_probe_other_config"]:
        parts += [
            to_nice_yaml(settings["system_probe_other_config"]).rstrip("\n"),
            "",
        ]
    return "\n".join(parts)


def render_security_agent_yaml(settings: dict[str, Any]) -> str:
    """``templates/security-agent.yaml.j2``: two indented blocks."""
    parts = [MANAGED_HEADER, ""]
    for key in ("runtime_security_config", "compliance_config"):
        block = settings[key]
        if block:
            parts += [f"{key}:", indent_block(to_nice_yaml(block)).rstrip("\n"), ""]
    return "\n".join(parts)


def is_legacy_datadog_source(source: dict[str, Any]) -> bool:
    """Whether an ``AptSources`` entry is one of the six deprecated lines."""
    components = tuple(source.get("components") or ())
    return (
        source.get("type") == "deb"
        and source.get("url") in LEGACY_REPO_URLS
        and source.get("distribution") == "stable"
        and len(components) == 1
        and components[0] in LEGACY_REPO_COMPONENTS
        and not source.get("options")
    )


def repo_entry(major: str) -> dict[str, Any]:
    """The ``AptSource`` dict ``AGENT_REPOS[major]`` parses to.

    Built by hand rather than by calling the private ``_parse_list_line``:
    the shape is public (``AptSources``' documented return type) and this
    keeps the comparison independent of the parser's internals.
    """
    return {
        "type": "deb",
        "url": "https://apt.datadoghq.com/",
        "distribution": "stable",
        "components": [major],
        "options": {"signed-by": [USR_SHARE_KEYRING]},
    }


def legacy_repo_targets(hits: tuple[str, ...]) -> tuple[str | None, ...]:
    """Map ``FindInFiles`` hits to the ``apt.repo`` ``filename`` arguments.

    ``/etc/apt/sources.list`` is the ``filename=None`` case; a ``.list``
    file under ``sources.list.d`` contributes its stem. Anything else (a
    deb822 ``.sources`` file, a stray ``.list.save`` backup) cannot be
    edited by ``apt.repo`` and is dropped -- the caller reports it. The
    result is sorted for a stable request order, with the ``None`` entry
    first because ``None`` and ``str`` do not compare.
    """
    stems = set()
    main_file = False
    for hit in hits:
        if hit == "/etc/apt/sources.list":
            main_file = True
            continue
        candidate = Path(hit)
        if candidate.parent == Path(SOURCES_LIST_D) and candidate.suffix == ".list":
            stems.add(candidate.stem)
    return (None,) * main_file + tuple(sorted(stems))


def fetch_keys(urls: tuple[str, ...]) -> tuple[bytes, ...]:
    """Download every signing key control-side, one body per URL.

    The role fetches each key into a host-side tempdir and imports it on the
    host; reemote takes IO streams, so the fetch moves control-side (D7) and
    each body becomes its own ``gpg.import_key(dest=...)`` request -- an
    additive import into the shared keyring, fingerprint-pinned.
    """
    bodies = []
    for url in urls:
        with urllib.request.urlopen(url, timeout=60) as response:
            bodies.append(response.read())
    return tuple(bodies)


def reemote_version() -> str:
    """The installed reemote version, for the ``install_info`` stamp."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("reemote")
    except PackageNotFoundError:
        return "unknown"


def notifying_names(plans: dict[str, Any], found: dict[str, Any]) -> tuple[str, ...]:
    """Every request name that notifies a restart handler.

    The notification channel is the request ``name``, so the set has to be
    enumerated from the same frozen inputs the deploys were built from --
    never from a live read. Static names plus one per managed check, per
    custom check and per stale ``conf.d`` entry the sweep will remove.
    """
    names = list(NOTIFY_STATIC)
    for plan in plans.values():
        for check in plan["checks"]:
            names.append(f"Create a configuration file for {check[0]}")
            names.append(f"Remove old configuration file for {check[0]}")
        for check in plan["custom_checks"]:
            names.append(f"Create custom check file for {check[0]}")
    for paths in found.values():
        for stale in paths:
            check = Path(stale).parent.name.removesuffix(".d")
            names += [
                f"Delete untracked check {check}",
                f"Delete default check {check}",
                f"Delete example check {check}",
            ]
    return tuple(names)


# --- deploys -----------------------------------------------------------------


def build_pre_cleanup(
    snapshot: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``tasks/main.yml``'s ``__pycache__`` removal + ``remove-legacy-installer.yml``.

    The snapshot carries the two reads those tasks branch on: whether the
    pinned agent version is already installed (``agent_datadog_skip_install``)
    and which legacy installer units systemd still knows about. Both are
    frozen before this deploy's prepare phase, so the branches resolve
    identically in prepare and apply.

    Deviation: the role marks every one of these tasks ``failed_when:
    false`` (best-effort cleanup on platforms where the package manager
    misbehaves). reemote is fail-fast -- a non-zero exit raises
    ``CommandError`` -- and there is no per-request ``failed_when``. On
    Ubuntu the removals are all no-ops when the legacy installer is absent,
    so the tolerance is not needed; a genuine failure now stops the deploy
    instead of being swallowed.
    """

    def pre_cleanup(host: Host) -> Generator[OperationRequest]:
        plan = snapshot.get(host.name)
        if plan is None:
            return
        if not plan["skip_install"]:
            # files.directory, not files.file: the target is a directory and
            # files.file(present=False) raises on a non-file unless forced.
            yield op(
                files.directory,
                name="Remove legacy Python script cache",
                path=PYCACHE_DIR,
                present=False,
                sudo=True,
                when=is_ubuntu,
            )
        yield op(
            apt.packages,
            name="Remove legacy installer package",
            packages=("datadog-installer",),
            present=False,
            sudo=True,
            when=is_ubuntu,
        )
        # systemd.service on a unit that does not exist renders `systemctl
        # start` and fails, so the stop/disable loop is gated on the unit
        # list the snapshot read -- the role's own `failed_when: false`
        # tolerance expressed as a precondition instead.
        for unit in plan["legacy_units"]:
            yield op(
                systemd.service,
                name=f"Stop legacy installer unit {unit}",
                service=unit,
                running=False,
                enabled=False,
                sudo=True,
                when=is_ubuntu,
            )
        for legacy_path in LEGACY_INSTALLER_PATHS:
            yield op(
                files.file,
                name=f"Remove legacy installer unit file {legacy_path}",
                path=legacy_path,
                present=False,
                sudo=True,
                when=is_ubuntu,
            )
        # daemon_reload is stateless (always changed), so it is gated on
        # there having been a unit to forget: a converged host skips it.
        if plan["legacy_units"]:
            yield op(
                systemd.daemon_reload,
                name="Reload systemd units",
                sudo=True,
                when=is_ubuntu,
            )

    return pre_cleanup


def deploy_bootstrap(host: Host) -> Generator[OperationRequest]:
    """``pkg-debian.yml``'s first two tasks: apt-transport-https and gnupg.

    Branch-free, so it needs no snapshot. Deviation: the role passes
    ``update_cache: true`` with no ``cache_valid_time``, which refreshes the
    index on every run. Both requests honour the role's own
    ``datadog_apt_cache_valid_time`` window instead, so a second pass inside
    the hour reports no change -- reemote's ``apt.update`` prefix is
    stateless and would otherwise make every run "changed".
    """
    for package in BOOTSTRAP_PACKAGES:
        yield op(
            apt.packages,
            name=f"Install {package}",
            packages=(package,),
            update=True,
            cache_valid_time=CACHE_VALID_TIME,
            sudo=True,
            when=is_ubuntu,
        )


def build_repository(
    snapshot: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``pkg-debian.yml``: the keyring, the legacy sweep and the agent repo.

    GAP-4/GAP-5 (#14/#15: ``apt.repo`` has no ``update_cache`` and no source
    provenance) are worked around here; see the comments at each site. The
    signing keys import additively into the shared keyring with
    ``gpg.import_key(dest=...)`` (GAP-1/#11, now closed).

    The per-host ``io.BytesIO`` objects are built ONCE, in the factory body
    outside the generator: a stream is hashable but compared by *identity*,
    so a fresh stream per phase would trip ``DeterminismError``.
    ``Host.upload_file`` rewinds before every transfer, so one stream serves
    the dry run and the real run alike.
    """
    streams = {
        name: tuple(io.BytesIO(body) for body in plan["keys"])
        for name, plan in snapshot.items()
    }

    def repository(host: Host) -> Generator[OperationRequest]:
        plan = snapshot.get(host.name)
        if plan is None:
            return
        settings = role_settings(host)

        # The role's `stat` + `file state=touch mode=0644 root:root`.
        # Deviation: `touch` is dropped. Ansible's own state=touch is
        # non-idempotent (it bumps mtime every run) and reemote's
        # files.file(touch=True) faithfully reproduces that -- which would
        # make every deploy report a change. present=True with the mode and
        # owner converges the same end state without the mtime churn.
        yield op(
            files.file,
            name="Ensure Datadog apt keyring exists",
            path=USR_SHARE_KEYRING,
            present=True,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
            when=is_ubuntu,
        )

        # The role's five `_apt-key-import.yml` iterations: each key imports
        # additively into the shared keyring (gpg --no-default-keyring
        # --keyring ... --import), fingerprint-pinned so a repeat import is a
        # no-op. gpg.import_key(dest=...) reproduces this directly (GAP-1/#11,
        # closed): the keys already in the file stay, so an operator-added key
        # survives. CURRENT is pinned too -- a documented deviation from the
        # role, which leaves it unpinned -- so a rotation is a one-line
        # constant update. The streams zip with the URLs and fingerprints in
        # the role's own order.
        for url, stream, fingerprint in zip(
            APT_KEY_URLS, streams[host.name], APT_KEY_FINGERPRINTS
        ):
            yield op(
                gpg.import_key,
                name=f"Import Datadog apt signing key {Path(url).stem}",
                src=stream,
                dest=USR_SHARE_KEYRING,
                keyid=fingerprint,
                sudo=True,
                when=is_ubuntu,
            )

        # GAP-5 (#15): AptSources entries carry no filename, and apt.repo's
        # removal only edits the file `filename` names -- so "remove this
        # repo line wherever it lives" needs the provenance the fact drops.
        # The snapshot recovered it with one FindInFiles content grep; here
        # the six legacy lines are fanned out across every file that holds
        # one (plus /etc/apt/sources.list as filename=None). apt.repo
        # no-ops when the line is not in the target file's own text, so the
        # cross product is safe -- just verbose, exactly as Ansible's six
        # global apt_repository tasks are.
        for stem in plan["legacy_files"]:
            for line in LEGACY_REPO_LINES:
                yield op(
                    apt.repo,
                    name=f"Deprecate legacy Datadog repository {line!r} in {stem}",
                    src=line,
                    present=False,
                    filename=stem,
                    sudo=True,
                    when=is_ubuntu,
                )

        # The role's with_dict over {6, 7}: present for the chosen major,
        # absent for the other. Both requests are always yielded -- the
        # `present` flag is the branch, not the request sequence.
        for major in AGENT_REPO_MAJORS:
            yield op(
                apt.repo,
                name=f"Ensure Datadog repository for Agent {major}",
                src=AGENT_REPOS[major],
                present=major == settings["major"],
                filename=f"ansible_datadog_{major}",
                sudo=True,
                when=is_ubuntu,
            )

        # The custom-repo block is scoped out (datadog_apt_repo is rejected
        # in main), so only its cleanup half runs: with no custom repo
        # configured the role removes the leftover file unconditionally.
        yield op(
            files.file,
            name="Remove Datadog custom repository file",
            path=CUSTOM_REPO_FILE,
            present=False,
            sudo=True,
            when=is_ubuntu,
        )

        # GAP-4 (#14): apt.repo has no `update_cache` parameter, so the role's
        # `update_cache: true` on every apt_repository task cannot ride the
        # request. A *stateless* apt.update() is gated on a snapshot boolean
        # instead: it fires only when this deploy will actually change a
        # sources file. Gating it on cache_valid_time would be wrong -- the
        # index may be fresh and still not know about the repo just added.
        if plan["needs_update"]:
            yield op(
                apt.update,
                name="Refresh the apt cache after repository changes",
                sudo=True,
                when=is_ubuntu,
            )

    return repository


def build_agent(
    snapshot: dict[str, frozenset[str]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``pkg-debian/install-pinned.yml`` / ``install-latest.yml`` + signing keys.

    GAP-3 (#13): ``apt.packages(latest=True)`` is non-idempotent -- it upgrades
    every installed, unpinned package it is given *without checking whether
    an upgrade exists* (apt.py's own comment says so), so a converged host
    reports a change on every run. The snapshot froze ``AptUpgradable``
    after the repository deploy refreshed the index; ``latest`` is set only
    for a package that actually has a pending upgrade. Once the upgrade
    lands, the next pass reads an empty upgradable set and the request
    converges to a plain presence check.

    ``skip_install`` is not reproduced as a request gate: reemote's
    ``apt.packages`` is version-aware for a ``name=version`` pin, so an
    already-installed pin is a no-op by construction. The flag still gates
    the ``__pycache__`` cleanup, which is what the role uses it for first.
    """

    def agent(host: Host) -> Generator[OperationRequest]:
        settings = role_settings(host)
        flavor = settings["flavor"]
        upgradable = snapshot.get(host.name, frozenset())
        pin = settings["pin"]
        if pin:
            yield op(
                apt.packages,
                name=f"Install pinned {flavor} package",
                packages=(f"{flavor}={pin}",),
                allow_downgrades=settings["allow_downgrade"],
                update=True,
                cache_valid_time=settings["cache_valid_time"],
                sudo=True,
                when=is_ubuntu,
            )
        else:
            yield op(
                apt.packages,
                name=f"Install latest {flavor} package",
                packages=(flavor,),
                latest=flavor in upgradable,
                update=True,
                cache_valid_time=settings["cache_valid_time"],
                sudo=True,
                when=is_ubuntu,
            )
        # The role installs datadog-signing-keys with state=latest and no
        # update_cache ("that was just done by the repository task above").
        yield op(
            apt.packages,
            name=f"Install latest {SIGNING_KEYS_PACKAGE} package",
            packages=(SIGNING_KEYS_PACKAGE,),
            latest=SIGNING_KEYS_PACKAGE in upgradable,
            sudo=True,
            when=is_ubuntu,
        )

    return agent


def deploy_identity(host: Host) -> Generator[OperationRequest]:
    """Group memberships: ``datadog_additional_groups``, plus GAP-2 (#12).

    GAP-2 (#12): ``files.install`` decides idempotence by reading the destination
    back with ``FileContents``, which cats *unprivileged*. The role's config
    files are ``0640`` owned by ``dd-agent`` (or root:dd-agent), so the login
    user cannot read them and every deploy would re-install all of them.
    The workaround is to make the login user a member of the agent group,
    which the ``0640`` group-read bit then covers.

    Ordering is load-bearing: this deploy runs AFTER the agent package
    install (the group must exist) and BEFORE any ``0640`` write, and each
    ``run`` opens fresh SSH sessions -- so the configure deploy's sessions
    pick up the new supplementary group. A membership change inside the
    *same* run would not take effect for that run's own sessions.

    Deviation: the role never touches the login user. This is an extra
    privileged change to the target, made so content idempotence works; the
    alternative (leaving the files unreadable) re-installs every config on
    every pass and restarts the agent each time.
    """
    settings = role_settings(host)
    login = host.connection.get("username")
    if login:
        yield op(
            server.user,
            name="Add the login user to the agent group",
            user=login,
            groups=(settings["agent_group"],),
            append=True,
            ensure_home=False,
            sudo=True,
            when=is_ubuntu,
        )
    additional = settings["additional_groups"]
    if additional:
        yield op(
            server.user,
            name="Add user to additional groups",
            user=settings["agent_user"],
            groups=additional,
            append=True,
            ensure_home=False,
            sudo=True,
            when=is_ubuntu,
        )


def build_configure(
    plans: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """``_agent-linux-macos-shared.yml`` + ``agent-linux.yml``'s config and units.

    Every template is pre-rendered control-side in ``main`` and frozen into
    the plan as a plain ``str``: hashable, equal across phases, and no
    identity-stable stream needed. The plan also carries the two systemd
    reads the role takes with ``service_facts`` -- which units exist and
    whether the experimental agent is running -- because
    ``systemd.service`` on a nonexistent unit renders ``systemctl start``
    and raises ``CommandError``.
    """

    def configure(host: Host) -> Generator[OperationRequest]:
        plan = plans.get(host.name)
        if plan is None:
            return
        agent_user = plan["agent_user"]
        agent_group = plan["agent_group"]

        if plan["manage_config"]:
            yield op(
                files.directory,
                name="Create Datadog agent config directory",
                path=CONFIG_DIR,
                mode="0755",
                user=agent_user,
                group=agent_group,
                sudo=True,
                when=is_ubuntu,
            )
            yield op(
                files.install,
                name="Create main Datadog agent configuration file",
                content=plan["datadog_yaml"],
                dest=f"{CONFIG_DIR}/datadog.yaml",
                mode="0640",
                user=agent_user,
                group=agent_group,
                sudo=True,
                when=is_ubuntu,
            )
            for check in plan["checks"]:
                yield op(
                    files.directory,
                    name=f"Ensure configuration directory for {check[0]}",
                    path=f"{CONF_D}/{check[0]}.d",
                    mode="0755",
                    user=agent_user,
                    group=agent_group,
                    sudo=True,
                    when=is_ubuntu,
                )
                yield op(
                    files.install,
                    name=f"Create a configuration file for {check[0]}",
                    content=check[1],
                    dest=f"{CONF_D}/{check[0]}.d/conf.yaml",
                    mode="0640",
                    user=agent_user,
                    group=agent_group,
                    sudo=True,
                    when=is_ubuntu,
                )
                yield op(
                    files.file,
                    name=f"Remove old configuration file for {check[0]}",
                    path=f"{CONF_D}/{check[0]}.yaml",
                    present=False,
                    sudo=True,
                    when=is_ubuntu,
                )

        # The role's `copy` of a controller-side custom check. reemote takes
        # content, not a controller path, so main() read the file and froze
        # its text. Deviation: a *binary* custom check would need files.put
        # into a login-writable path; the role's checks.d entries are Python
        # source, which is text.
        for check in plan["custom_checks"]:
            yield op(
                files.install,
                name=f"Create custom check file for {check[0]}",
                content=check[1],
                dest=f"{CHECKS_D}/{check[0]}.py",
                mode="0755",
                user=agent_user,
                group=agent_group,
                sudo=True,
                when=is_ubuntu,
            )

        # Unconditional in the role -- no `when: datadog_manage_config`.
        yield op(
            files.install,
            name="Create installation information file",
            content=plan["install_info"],
            dest=f"{CONFIG_DIR}/install_info",
            mode="0644",
            user=agent_user,
            group=agent_group,
            sudo=True,
            when=is_ubuntu,
        )

        if plan["manage_config"] and plan["check_running"]:
            # The role's four `is defined` tests are always true for the
            # four configs defaults/main.yml defines, so the template branch
            # always fires and the `file absent` branch never does. The plan
            # still carries both directions: a host that undefines all four
            # (reemote has no `is defined`, so an empty dict is the analogue)
            # gets the removal instead.
            if plan["system_probe_yaml"] is not None:
                yield op(
                    files.install,
                    name="Create system-probe configuration file",
                    content=plan["system_probe_yaml"],
                    dest=f"{CONFIG_DIR}/system-probe.yaml",
                    mode="0640",
                    user="root",
                    group=agent_group,
                    sudo=True,
                    when=is_ubuntu,
                )
            else:
                yield op(
                    files.file,
                    name="Remove system-probe configuration file",
                    path=f"{CONFIG_DIR}/system-probe.yaml",
                    present=False,
                    sudo=True,
                    when=is_ubuntu,
                )

        if plan["check_running"]:
            units = plan["units"]
            if (
                plan["enabled"]
                and not plan["exp_running"]
                and "datadog-agent.service" in units
            ):
                yield op(
                    systemd.service,
                    name="Ensure datadog-agent is running",
                    service="datadog-agent",
                    running=True,
                    enabled=True,
                    sudo=True,
                    when=is_ubuntu,
                )
            if (
                plan["enabled"]
                and plan["sysprobe_enabled"]
                and "datadog-agent-sysprobe.service" in units
            ):
                yield op(
                    systemd.service,
                    name="Ensure datadog-agent-sysprobe is running",
                    service="datadog-agent-sysprobe",
                    running=True,
                    enabled=True,
                    sudo=True,
                    when=is_ubuntu,
                )
            if not plan["enabled"]:
                for unit in DISABLED_AGENT_UNITS:
                    if f"{unit}.service" in units:
                        yield op(
                            systemd.service,
                            name=f"Ensure {unit} is not running",
                            service=unit,
                            running=False,
                            enabled=False,
                            sudo=True,
                            when=is_ubuntu,
                        )
                if "datadog-agent-security.service" in units:
                    yield op(
                        systemd.service,
                        name="Ensure datadog-agent-security is not running",
                        service="datadog-agent-security",
                        running=False,
                        enabled=False,
                        sudo=True,
                        when=is_ubuntu,
                    )
            if (
                plan["before_7180"]
                and (not plan["enabled"] or not plan["sysprobe_enabled"])
                and "datadog-agent-sysprobe.service" in units
            ):
                yield op(
                    systemd.service,
                    name="Ensure datadog-agent-sysprobe is stopped",
                    service="datadog-agent-sysprobe",
                    running=False,
                    enabled=False,
                    sudo=True,
                    when=is_ubuntu,
                )

        if plan["manage_config"]:
            if plan["security_agent_yaml"] is not None:
                yield op(
                    files.install,
                    name="Create security-agent configuration file",
                    content=plan["security_agent_yaml"],
                    dest=f"{CONFIG_DIR}/security-agent.yaml",
                    mode="0640",
                    user="root",
                    group=agent_group,
                    sudo=True,
                    when=is_ubuntu,
                )
            else:
                yield op(
                    files.file,
                    name="Remove security-agent configuration file",
                    path=f"{CONFIG_DIR}/security-agent.yaml",
                    present=False,
                    sudo=True,
                    when=is_ubuntu,
                )

    return configure


def build_cleanup_conf_d(
    found: dict[str, Any],
) -> Callable[[Host], Generator[OperationRequest]]:
    """The role's three ``conf.d`` cleanup loops, as a post-configure deploy.

    ``FindFiles(CONF_D, fname="conf.yaml*", maxdepth=2)`` is the role's
    ``find: patterns=["conf.yaml*"] recurse depth=2 file_type=file``. The
    enumeration is a mutable read, so it cannot live in the deploy that
    also installs the tracked checks -- the installs change what it
    returns. It moves to the gap between two complete runs, exactly as
    ``examples/demonstrations/composition/conf_d_cleanup.py`` does.

    The role derives the check name with ``dirname | basename |
    regex_replace('^(.*).d$', '\\1')`` and then *reconstructs* the path;
    this uses the found path directly, which is the same file for every
    ``conf.d/<check>.d/<suffix>`` hit. Entries that are not one of those
    (a stray ``conf.d/conf.yaml``) are skipped rather than turned into a
    bogus ``conf.d/conf.d/conf.yaml`` removal.

    Deviation: the sweep runs after the whole configure deploy instead of
    between ``datadog.yaml`` and the check installs. It only removes
    *untracked* checks while the installs only write *tracked* ones, so the
    two never touch the same file and the order is immaterial.
    """

    def cleanup_conf_d(host: Host) -> Generator[OperationRequest]:
        settings = role_settings(host)
        if not settings["manage_config"]:
            return
        tracked = settings["tracked_checks"]
        for stale in sorted(found.get(host.name, ())):
            check_dir = Path(stale).parent
            if check_dir.parent != Path(CONF_D) or not check_dir.name.endswith(".d"):
                continue
            check = check_dir.name.removesuffix(".d")
            if check in tracked:
                continue
            suffix = Path(stale).name
            if suffix == "conf.yaml" and settings["disable_untracked"]:
                yield op(
                    files.file,
                    name=f"Delete untracked check {check}",
                    path=stale,
                    present=False,
                    sudo=True,
                    when=is_ubuntu,
                )
            elif suffix == "conf.yaml.default" and settings["disable_default"]:
                yield op(
                    files.file,
                    name=f"Delete default check {check}",
                    path=stale,
                    present=False,
                    sudo=True,
                    when=is_ubuntu,
                )
            elif suffix == "conf.yaml.example" and settings["disable_example"]:
                yield op(
                    files.file,
                    name=f"Delete example check {check}",
                    path=stale,
                    present=False,
                    sudo=True,
                    when=is_ubuntu,
                )

    return cleanup_conf_d


# --- snapshot builders (pure; called once from main) -------------------------


def pre_cleanup_snapshot(
    installed: dict[str, Any],
    units: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the two reads ``main.yml``'s cleanup tasks branch on.

    ``skip_install`` is the role's ``agent_datadog_skip_install``: the
    pinned Debian version already being installed. ``legacy_units`` is the
    subset of the installer units systemd still lists -- the role tolerates
    their absence with ``failed_when: false``, reemote preconditions on it.
    """
    snapshot: dict[str, Any] = {}
    for name, config in settings.items():
        versions = {
            entry["name"]: tuple(entry["versions"])
            for entry in installed.get(name, ())
        }
        pin = config["pin"]
        snapshot[name] = {
            "skip_install": bool(pin) and pin in versions.get(config["flavor"], ()),
            "legacy_units": tuple(
                unit for unit in LEGACY_INSTALLER_UNITS if unit in units.get(name, {})
            ),
        }
    return snapshot


def repository_snapshot(
    keys: tuple[bytes, ...],
    sources: dict[str, Any],
    hits: dict[str, Any],
    repo_files: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the signing-key bodies and the three apt reads the repository deploy needs.

    ``needs_update`` is the GAP-4 (#14) workaround's gate: true when this
    deploy will change a sources file (a legacy line to drop, the desired repo
    missing, or a leftover custom repo file to remove). It is computed from
    pre-state, so both phases agree, and it is deliberately *not* a
    cache-freshness test -- a fresh index still does not know about a repo
    that was just added.
    """
    snapshot: dict[str, Any] = {}
    for name, config in settings.items():
        configured = sources.get(name, ())
        snapshot[name] = {
            "keys": keys,
            "legacy_files": legacy_repo_targets(tuple(hits.get(name, ()))),
            "needs_update": (
                any(is_legacy_datadog_source(source) for source in configured)
                or repo_entry(config["major"]) not in configured
                or CUSTOM_REPO_FILE in repo_files.get(name, ())
            ),
        }
    return snapshot


def sysprobe_is_enabled(settings: dict[str, Any], units: frozenset[str]) -> bool:
    """``agent_datadog_sysprobe_enabled``, and the installed-unit precondition.

    Two role bugs are corrected here rather than reproduced:

    * ``agent_datadog_before_7610`` tests ``datadog_major``/``datadog_minor``/
      ``datadog_bugfix``, which the role never defines (``parse-version.yml``
      sets ``agent_datadog_major`` and friends). The flag is therefore always
      false, the "since 6/7.61.0" block always runs, and it *overwrites* the
      three earlier version-gated blocks -- so they are dead code and the
      final expression is the 7610 one regardless of the agent version.
    * That expression's ``and``/``or`` precedence makes a non-empty
      ``system_probe_other_config`` bypass the ``sysprobe_installed`` test.
      reemote's ``systemd.service`` raises ``CommandError`` on a unit that
      does not exist, so honouring the bug would break the deploy. The
      intended reading -- ``(any enabled flag or other config) and
      installed`` -- is what this implements.
    """
    if "datadog-agent-sysprobe.service" not in units:
        return False
    for key in (
        "system_probe_config",
        "network_config",
        "service_monitoring_config",
    ):
        if settings[key].get("enabled"):
            return True
    return bool(settings["system_probe_other_config"])


def configure_plan(
    settings: dict[str, Any],
    units: frozenset[str],
    exp_state: str | None,
    tool_version: str,
) -> dict[str, Any]:
    """Render every template and freeze every read the configure deploy needs.

    Control-side work, performed once in ``main``: the Jinja2 templates
    become plain strings (reemote has no template engine and no controller
    filesystem paths inside a deploy), the custom checks are read from the
    paths ``datadog_custom_checks`` names, and ``service_facts`` becomes the
    two systemd reads passed in.

    ``exp_state`` is a raw systemd ``ActiveState`` (``active``, ``inactive``,
    ...), where Ansible's ``service_facts`` reports ``running``/``stopped``;
    ``active`` is the equivalent test.
    """
    check_running = not settings["skip_running_check"]
    probe_defined = any(key in settings for key in PROBE_CONFIG_KEYS)
    return {
        "manage_config": settings["manage_config"],
        "check_running": check_running,
        "enabled": settings["enabled"],
        "exp_running": exp_state == "active",
        "sysprobe_enabled": check_running and sysprobe_is_enabled(settings, units),
        "before_7180": settings["before_7180"],
        "units": units,
        "agent_user": settings["agent_user"],
        "agent_group": settings["agent_group"],
        "datadog_yaml": render_datadog_yaml(settings),
        "install_info": render_install_info(tool_version),
        "system_probe_yaml": (
            render_system_probe_yaml(settings)
            if settings["manage_config"] and check_running and probe_defined
            else None
        ),
        "security_agent_yaml": (
            render_security_agent_yaml(settings)
            if settings["manage_config"] and settings["runtime_security_config"]
            else None
        ),
        "checks": tuple(
            (check, render_check_yaml(settings["checks"][check]))
            for check in sorted(settings["checks"])
        ),
        "custom_checks": tuple(
            (check, Path(source).read_text(encoding="utf-8"))
            for check, source in sorted(settings["custom_checks"].items())
        ),
    }


def notified_hosts(reports: list[Any], names: tuple[str, ...]) -> frozenset[str]:
    """Union ``changed_hosts`` across every deploy's apply report.

    Ansible accumulates notifications across the whole play and flushes
    them once at the end; reemote's ``changed_hosts`` reads one report, so
    the union is the play-wide notification set.
    """
    notified: frozenset[str] = frozenset()
    for report in reports:
        notified = notified | changed_hosts(report, names)
    return notified


async def execute(inventory: list[dict[str, Any]], deploy: Any, title: str) -> Any:
    """Dry-run then really run one deploy, printing both renderings.

    The dry run is Ansible's ``--check`` as a return value: it stops after
    prepare, so the plan shows what *would* change without touching a host.
    Both runs use the very same deploy object -- for a factory-built deploy
    that means the same frozen snapshot and the same identity-stable
    streams, which is what makes the determinism guard hold.
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
            print("\nNo host in the inventory group 'ubuntu'; nothing to deploy.")
            return

        # The role's variable resolution and its two `fail:` gates, before
        # any host is contacted.
        settings = {host.name: role_settings(host) for host in targets}
        for host in targets:
            config = settings[host.name]
            if config["manage_config"] and not config["api_key"]:
                raise ValueError(
                    f"{host.name}: datadog_api_key is mandatory when using managed "
                    "config. Add it to the host's inventory entry."
                )
            if config["apt_repo"]:
                raise ValueError(
                    f"{host.name}: datadog_apt_repo (a custom apt repository) is "
                    "scoped out of this migration -- the role's stat/slurp/compare "
                    "block needs a per-host remote content read this program does "
                    "not implement. Unset it to use the official repository."
                )
            for removed in ("datadog_yum_gpgkey", "datadog_zypper_gpgkey"):
                if host.extra.get(removed):
                    raise ValueError(
                        f"{host.name}: {removed} has been removed from the role; "
                        "use the distribution's own key management instead."
                    )

        # One control-side fetch of all five keys, one body per URL; each
        # imports additively into the shared keyring (gpg.import_key dest=).
        print("\n== Fetching the Datadog apt signing keys (control-side) ==")
        keys = fetch_keys(APT_KEY_URLS)
        print(f"{len(keys)} keys, {sum(len(body) for body in keys)} armored bytes")

        # Pre-state snapshot. gather_fact does not close its hosts, so the
        # finally does; run() builds and closes its own.
        try:
            installed = await gather_fact(targets, AptPackages)
            sources = await gather_fact(targets, AptSources)
            hits = await gather_fact(
                targets, FindInFiles, "/etc/apt", LEGACY_REPO_PATTERN
            )
            repo_files = await gather_fact(
                targets, FindFiles, SOURCES_LIST_D, fname="*.list", maxdepth=1
            )
            units_before = await gather_fact(targets, SystemdUnitFiles)
        finally:
            for host in hosts:
                await host.aclose()
        print("\n== Pre-state snapshot ==")
        for host in targets:
            print(
                f"{host.name}: {len(installed.get(host.name, ()))} installed packages, "
                f"{len(sources.get(host.name, ()))} apt sources, "
                f"{len(hits.get(host.name, ()))} legacy repo file(s), "
                f"{len(units_before.get(host.name, {}))} systemd units"
            )

        reports = []
        pre_cleanup = build_pre_cleanup(
            pre_cleanup_snapshot(installed, units_before, settings)
        )
        reports.append(
            await execute(inventory, pre_cleanup, "Deploy 1: legacy installer cleanup")
        )
        reports.append(
            await execute(inventory, deploy_bootstrap, "Deploy 2: apt bootstrap packages")
        )
        repository = build_repository(
            repository_snapshot(keys, sources, hits, repo_files, settings)
        )
        reports.append(
            await execute(inventory, repository, "Deploy 3: signing keys and apt repo")
        )

        # GAP-3 (#13): read the upgrade candidates *after* the repository
        # deploy refreshed the index, so `latest` is a real delta rather than
        # an unconditional upgrade.
        try:
            upgradable = await gather_fact(targets, AptUpgradable)
        finally:
            for host in hosts:
                await host.aclose()
        agent = build_agent(
            {
                name: frozenset(entry["name"] for entry in entries)
                for name, entries in upgradable.items()
            }
        )
        reports.append(
            await execute(inventory, agent, "Deploy 4: the Datadog agent package")
        )

        # GAP-2 (#12): group membership must land before any 0640 write, and
        # in an earlier run than the write -- each run opens fresh SSH
        # sessions, so only a later run sees the new supplementary group.
        reports.append(
            await execute(inventory, deploy_identity, "Deploy 5: group memberships")
        )

        # service_facts, after the install: which agent units exist now, and
        # whether the experimental agent owns the service manager.
        try:
            units_after = await gather_fact(targets, SystemdUnitFiles)
            exp_state = await gather_fact(
                targets, SystemdServiceState, "datadog-agent-exp.service"
            )
        finally:
            for host in hosts:
                await host.aclose()
        tool_version = reemote_version()
        plans = {
            host.name: configure_plan(
                settings[host.name],
                frozenset(units_after.get(host.name, {})),
                exp_state.get(host.name),
                tool_version,
            )
            for host in targets
        }
        configure = build_configure(plans)
        reports.append(
            await execute(inventory, configure, "Deploy 6: configuration and services")
        )

        # The conf.d enumeration, after the installs it must observe. The
        # role gates its `find` on disable_untracked or disable_default only,
        # which leaves the example-check loop with nothing to iterate when
        # disable_example is set alone; all three flags gate it here.
        sweep_hosts = [
            host
            for host in targets
            if settings[host.name]["manage_config"]
            and (
                settings[host.name]["disable_untracked"]
                or settings[host.name]["disable_default"]
                or settings[host.name]["disable_example"]
            )
        ]
        found: dict[str, Any] = {}
        if sweep_hosts:
            try:
                found = await gather_fact(
                    sweep_hosts, FindFiles, CONF_D, fname="conf.yaml*", maxdepth=2
                )
            finally:
                for host in hosts:
                    await host.aclose()
            print("\n== Post-configure snapshot (FindFiles conf.d) ==")
            for name, paths in found.items():
                print(f"{name}: {len(paths)} check config file(s)")
        cleanup = build_cleanup_conf_d(found)
        reports.append(
            await execute(inventory, cleanup, "Deploy 7: conf.d cleanup")
        )

        # The handlers. Every notifying request name is enumerated from the
        # same frozen inputs the deploys were built from, then the play-wide
        # notification set is split by the role's two notify targets and
        # filtered by each handler's own `when:`.
        names = notifying_names(plans, found)
        notified = notified_hosts(reports, names)
        probe_notified = notified_hosts(reports, PROBE_NOTIFY_NAMES) & frozenset(
            name for name in plans if plans[name]["before_7180"]
        )
        agent_notified = notified - probe_notified
        sysprobe_restart = frozenset(
            name
            for name in probe_notified
            if plans[name]["enabled"]
            and plans[name]["sysprobe_enabled"]
            and "datadog-agent-sysprobe.service" in plans[name]["units"]
        )
        agent_restart = frozenset(
            name
            for name in agent_notified
            if plans[name]["enabled"]
            and not plans[name]["exp_running"]
            and "datadog-agent.service" in plans[name]["units"]
        )
        print("\n== Handler notification set ==")
        print(f"restart datadog-agent-sysprobe: {sorted(sysprobe_restart) or '(none)'}")
        print(f"restart datadog-agent: {sorted(agent_restart) or '(none)'}")
        handlers = compose(
            restart_handler(
                sysprobe_restart, ("datadog-agent-sysprobe",), when=is_ubuntu
            ),
            restart_handler(agent_restart, ("datadog-agent",), when=is_ubuntu),
        )
        reports.append(await execute(inventory, handlers, "Deploy 8: restart handlers"))

        changed = sum(
            1 for report in reports for entry in report.log if entry.changed
        )
        print(f"\n== Done: {changed} changed request(s) across {len(reports)} deploys ==")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        # The inventory is missing/unreadable/not JSON, a role variable is
        # invalid, or a scoped-out override was requested. No host was
        # contacted for the variable gates, so the message is the whole report.
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