# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The node_exporter role (migrated from ``geerlingguy.node_exporter``).
#
# Installs `node_exporter <https://github.com/prometheus/node_exporter>`_,
# Prometheus' hardware and OS metric exporter, on Ubuntu hosts: the release
# tarball unpacked in ``/tmp``, the binary moved to ``/usr/local/bin``, the
# ``node_exporter`` group/user (with the optional fixed uid/gid), the rendered
# ``node_exporter.service`` unit, the running/enabled unit and the metrics
# endpoint check. :func:`node_exporter` is the whole task list and
# :class:`NodeExporterVars` is ``defaults/main.yml``, bound from the inventory's
# ``node_exporter`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. A host outside the target family
# returns before its first ``yield`` and emits no requests, no barriers and no
# log lines at all.
#
# The stream is one deploy body carrying every barrier type. The ``act`` probe
# (the role's ``--version`` ``command`` task) closes the first wave and its
# ``Result`` decides whether the download half runs at all; a ``control`` fetch
# resolves the literal ``latest`` version from the GitHub releases API; the
# download requests, the account requests, the unit file and the service request
# then share the next wave, closed by ``checkpoint("configure done")`` -- the
# role's ``notify``. ``changed_hosts`` over that report selects the hosts for the
# ``Restart node_exporter (handler)`` request, and the wave it closes is where
# the ``fact(server.HttpEndpoint)`` metrics check runs. Wave counts are
# therefore host-dependent: ``latest`` costs one extra wave, a host that must be
# downloaded for costs the same wave but more requests, and a host with no
# notification loses the handler request entirely.
#
# Deviations from the Ansible role, all deliberate:
#
# * **D1** the ``meta/main.yml`` platform list (Ubuntu, Debian, Fedora, SLES,
#   FreeBSD, ...) freezes to the ``"Ubuntu"`` family guard. The role ships no OS
#   dispatch at all -- ``defaults/main.yml`` is its only variable file -- so the
#   defaults are simply the frozen module constants.
# * **D2** ``run_once: true`` + ``delegate_to: localhost`` becomes a per-host
#   ``control`` barrier: ``files.fetch_url`` runs on the controller for each host
#   still asking for ``latest``. The role's ``until: status == 200``/``retries: 5``
#   loop is dropped -- an unreachable API raises ``OSError`` out of the deploy
#   instead of retrying five times.
# * **D3** the ``include_tasks: config-version.yaml`` + ``set_fact`` pair
#   disappears: the resolved version and download URL are ordinary local names in
#   the body, so no inventory host variable is ever rewritten.
# * **D4** the ``unarchive`` task's ``mode: 0755`` is **not** carried over.
#   ``files.unarchive``'s ``mode`` is a recursive ``chmod -R`` of ``dest``, and
#   the role's ``dest`` is the shared world-writable ``/tmp`` -- applying it there
#   would re-mode every other user's files. The executable bit the task wanted is
#   instead set on the binary itself, by the ``files.copy`` that follows, which is
#   the only path the unit file ever executes.
# * **D5** ``remote_src: true`` with a URL ``src`` downloads on the target in
#   Ansible; here ``files.unarchive`` fetches the tarball **control-side** and
#   spools it to a URL-derived ``/tmp`` path (the ``files.download``/kubectl
#   stance). The controller needs egress to ``github.com``; the host downloads
#   nothing.
# * **D6** the ``copy`` task becomes ``files.copy`` in its rename form with
#   ``overwrite=True`` -- Ansible's ``force: yes``, which the ``copy`` module
#   defaults to -- so a drifted binary converges once and a repeat reports no
#   change (the sha1 comparison), with ``mode="0755"`` carried explicitly.
# * **D7** ``systemd: daemon_reload=true`` ``when: unit file is changed`` and the
#   ``service:`` task fold into **one** ``systemd.service`` request carrying
#   ``daemon_reload=True``: the reload is state-gated on
#   ``SystemdNeedDaemonReload``, so a converged host issues no ``daemon-reload``
#   exactly as the role's ``when`` intended.
# * **D8** the ``uri`` verification becomes ``fact(server.HttpEndpoint)``, which
#   runs ``curl`` **on the target** so ``localhost`` keeps the role's meaning (the
#   host's own loopback). A fact never raises on a non-2xx answer, so the body
#   reads the record and raises its own ``ValueError`` when the status is not 200
#   or the body lacks the ``Metrics`` link.
# * **D9** the handler runs *before* the metrics check. Ansible flushes handlers
#   at the end of the play, so its ``uri`` task probed whatever was running before
#   the restart; here the check follows the restart and reads the service the
#   deploy just brought up.
# * **D10** ``create_home: false`` maps to ``ensure_home=False`` (reemote's
#   ``create_home`` already defaults to ``False``, so no ``useradd -m`` is
#   emitted); ``home`` is left unset, exactly as the task leaves it.
# * **D11** ``default(omit)`` becomes ``None``: with no ``gid`` there is no group
#   request and the user request carries no ``group=`` (so ``useradd`` creates its
#   own group of the same name, as on the role's path), and with no ``uid`` no
#   ``--uid`` flag is rendered.
# * **D12** the unit template is pure substitution, so it renders as an f-string:
#   no Jinja2, no ``reemote[templates]`` extra. Where the template left a trailing
#   space behind an empty ``node_exporter_options``, the rendered ``ExecStart``
#   line does not.
# """

import json
import re
from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import files, server, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint, control, fact

# --- role constants (defaults/main.yml, Ubuntu only) --------------------------

#: The version keyword that triggers the GitHub release lookup.
LATEST = "latest"

#: The releases API the ``config-version.yaml`` ``uri`` task queries.
RELEASE_API_URL = (
    "https://api.github.com/repos/prometheus/node_exporter/releases/latest"
)

#: The download base of ``node_exporter_download_url``'s formula.
DOWNLOAD_BASE = "https://github.com/prometheus/node_exporter/releases/download"

#: The default architecture (``node_exporter_arch``) and binary path.
DEFAULT_ARCH = "amd64"
DEFAULT_BIN_PATH = "/usr/local/bin/node_exporter"

#: Where the tarball is unpacked (the ``unarchive`` task's ``dest: /tmp``).
EXTRACT_DIR = "/tmp"

#: The unit file, the service and its account.
UNIT_PATH = "/etc/systemd/system/node_exporter.service"
SERVICE = "node_exporter"
SERVICE_USER = "node_exporter"
NOLOGIN_SHELL = "/sbin/nologin"

#: The text the ``failed_when: "'Metrics' not in ...`` check looks for.
METRICS_MARKER = "Metrics"

#: The modes ``node_exporter_state`` accepts, as ``systemd.service`` keywords.
STATE_KEYWORDS: dict[str, dict[str, bool]] = {
    "started": {"running": True},
    "stopped": {"running": False},
    "restarted": {"running": True, "restarted": True},
    "reloaded": {"running": True, "reloaded": True},
}

#: The policies ``node_exporter_restart`` accepts (systemd's ``Restart=``).
RESTART_POLICIES: tuple[str, ...] = (
    "no",
    "on-success",
    "on-failure",
    "on-abnormal",
    "on-abort",
    "on-watchdog",
    "always",
)

#: The two ``notify: restart node_exporter`` tasks, by request name (D9's
#: handler is queued by these and nothing else).
MOVE_NAME = "Move node_exporter binary into place."
UNIT_NAME = "Copy the node_exporter systemd unit file."
NOTIFYING = (MOVE_NAME, UNIT_NAME)


# --- variables ---------------------------------------------------------------


class NodeExporterVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``node_exporter`` role mapping, so ``bin_path`` there sets
    :attr:`bin_path`. The model is frozen, so it stays hashable through the
    deploy body (a :class:`~reemote.rolevars.VarsSet` is closed over by the
    factory), and ``extra="forbid"`` turns a typo in that mapping into a
    ``ValidationError`` raised before any host is contacted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The release to install (``node_exporter_version``); ``"latest"`` resolves
    #: through the GitHub API in the body (deviations D2, D3).
    version: str = LATEST
    #: The release architecture in GitHub's spelling (``node_exporter_arch``).
    arch: str = DEFAULT_ARCH
    #: The full tarball URL (``node_exporter_download_url``). ``None`` -- the
    #: default -- derives it from :attr:`version` and :attr:`arch`; set it to
    #: mirror the archive elsewhere (deviation D5).
    download_url: str | None = None
    #: Where the binary lands (``node_exporter_bin_path``); its parent must exist.
    bin_path: str = DEFAULT_BIN_PATH
    #: The address the exporter listens on (``node_exporter_host``): ``""`` --
    #: the default -- binds every interface, and is what the metrics check
    #: replaces with ``localhost``.
    host: str = ""
    #: The port (``node_exporter_port``).
    port: int = 9100
    #: Extra command-line flags for ``ExecStart`` (``node_exporter_options``).
    options: str = ""
    #: The unit's wanted state (``node_exporter_state``): one of
    #: :data:`STATE_KEYWORDS`' keys.
    state: str = "started"
    #: Whether the unit is enabled at boot (``node_exporter_enabled``).
    enabled: bool = True
    #: The unit's ``Restart=`` policy (``node_exporter_restart``).
    restart: str = "on-failure"
    #: A fixed uid for the service account (``node_exporter_uid``); ``None``
    #: lets ``useradd`` choose (deviation D11).
    uid: int | None = None
    #: A fixed gid for the service group (``node_exporter_gid``); ``None`` means
    #: no group is created at all and the account gets its own default group.
    gid: int | None = None

    @field_validator("version")
    @classmethod
    def _version_form(cls, value: str) -> str:
        """Non-empty and path-safe: the version feeds a URL and two paths."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"node_exporter_version must be a non-empty string, got {value!r}"
            )
        value = value.strip()
        if "/" in value or value.startswith("."):
            raise ValueError(
                f"node_exporter_version must be a plain release name, got {value!r}"
            )
        return value

    @field_validator("arch")
    @classmethod
    def _arch_form(cls, value: str) -> str:
        """Non-empty and path-safe: the architecture names the archive directory."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"node_exporter_arch must be non-empty, got {value!r}")
        value = value.strip()
        if "/" in value or " " in value:
            raise ValueError(
                f"node_exporter_arch must be a plain architecture name, got {value!r}"
            )
        return value

    @field_validator("download_url")
    @classmethod
    def _download_url_form(cls, value: str | None) -> str | None:
        """An override must be an http(s) URL (``files.unarchive``'s guard)."""
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                f"node_exporter_download_url must be an http(s) URL, got {value!r}"
            )
        return value

    @field_validator("bin_path")
    @classmethod
    def _bin_path_absolute(cls, value: str) -> str:
        """A relative binary path would be executed relative to nobody's cwd."""
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError(
                f"node_exporter_bin_path must be an absolute path, got {value!r}"
            )
        return value

    @field_validator("host")
    @classmethod
    def _host_form(cls, value: str) -> str:
        """``""`` (every interface) or a plain address -- no scheme, no port."""
        if ":" in value or "/" in value or " " in value:
            raise ValueError(
                f"node_exporter_host must be a bare address (no port), got {value!r}"
            )
        return value

    @field_validator("port")
    @classmethod
    def _port_range(cls, value: int) -> int:
        """The port reaches ``--web.listen-address`` and the check URL."""
        if not isinstance(value, int) or not 1 <= value <= 65535:
            raise ValueError(
                f"node_exporter_port must be between 1 and 65535, got {value!r}"
            )
        return value

    @field_validator("state")
    @classmethod
    def _state_known(cls, value: str) -> str:
        """Only the four states :data:`STATE_KEYWORDS` can translate."""
        if value not in STATE_KEYWORDS:
            raise ValueError(
                f"node_exporter_state must be one of "
                f"{', '.join(STATE_KEYWORDS)}, got {value!r}"
            )
        return value

    @field_validator("restart")
    @classmethod
    def _restart_known(cls, value: str) -> str:
        """Only systemd's own ``Restart=`` vocabulary."""
        if value not in RESTART_POLICIES:
            raise ValueError(
                f"node_exporter_restart must be one of "
                f"{', '.join(RESTART_POLICIES)}, got {value!r}"
            )
        return value

    @field_validator("uid", "gid")
    @classmethod
    def _id_positive(cls, value: int | None) -> int | None:
        """``None`` omits the flag (deviation D11); 0 would be skipped anyway."""
        if value is None:
            return None
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"node_exporter uid/gid must be a positive int, got {value!r}")
        return value


# --- pure helpers ------------------------------------------------------------


def parse_release_tag(text: str) -> str:
    """The ``config-version.yaml`` ``set_fact``: ``tag_name`` without its ``v``.

    Reproduces ``regex_replace('^v?([0-9\\.]+)$', '\\1')`` on the API response's
    ``tag_name``: a plain ``v1.8.2`` becomes ``1.8.2``, and a tag the pattern does
    not match (``2.0.0-rc.1``) is used verbatim, exactly as ``regex_replace``
    leaves it. Anything else -- no JSON body, no ``tag_name`` -- raises, because
    the version reaches a URL, a path and the ``--version`` comparison.
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"{RELEASE_API_URL} did not return a JSON object: {text[:80]!r}"
        ) from exc
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag:
        raise ValueError(f"{RELEASE_API_URL} returned no 'tag_name': {text[:80]!r}")
    match = re.fullmatch(r"v?([0-9.]+)", tag)
    return match.group(1) if match else tag


def download_url_for(version: str, arch: str) -> str:
    """``node_exporter_download_url``' formula from ``defaults/main.yml``."""
    return (
        f"{DOWNLOAD_BASE}/v{version}/node_exporter-{version}.linux-{arch}.tar.gz"
    )


def resolve_download_url(hv: NodeExporterVars, version: str) -> str:
    """The override or the formula (deviation D5's single decision point)."""
    return hv.download_url or download_url_for(version, hv.arch)


def archive_dir(version: str, arch: str) -> str:
    """The tarball's top-level directory under :data:`EXTRACT_DIR`."""
    return f"{EXTRACT_DIR}/node_exporter-{version}.linux-{arch}"


def extracted_binary(version: str, arch: str) -> str:
    """The ``copy`` task's ``src``: the unpacked binary inside that directory."""
    return f"{archive_dir(version, arch)}/node_exporter"


def probe_command(hv: NodeExporterVars) -> str:
    """The role's version-probe ``command`` (no escalation, as upstream)."""
    return f"{hv.bin_path} --version"


def metrics_url(hv: NodeExporterVars) -> str:
    """The ``uri`` task's URL: the configured address, else ``localhost``."""
    return f"http://{hv.host or 'localhost'}:{hv.port}/"


def render_unit(hv: NodeExporterVars) -> str:
    """``templates/node_exporter.service.j2``, rendered with an f-string (D12).

    The template is pure substitution, so no Jinja2 is needed; the only text
    difference is that an empty ``node_exporter_options`` leaves no trailing
    space on the ``ExecStart`` line.
    """
    exec_start = f"{hv.bin_path} --web.listen-address={hv.host}:{hv.port}"
    if hv.options:
        exec_start = f"{exec_start} {hv.options}"
    return (
        "[Unit]\n"
        "Description=NodeExporter\n"
        "\n"
        "[Service]\n"
        "TimeoutStartSec=0\n"
        f"User={SERVICE_USER}\n"
        f"ExecStart={exec_start}\n"
        f"Restart={hv.restart}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


# --- the deploy stages (each returns a per-host generator) --------------------


def install(hv: NodeExporterVars, version: str) -> Deploy:
    """The download half: ``tasks/main.yml``'s unarchive and move tasks.

    Two requests in the role's order. ``files.unarchive``'s ``src`` is the
    release URL, so the tarball is fetched control-side and spooled
    (deviation D5); its ``creates`` gate names the directory the tarball
    unpacks to, which is the path the next request copies from. No ``mode`` is
    passed -- the recursive chmod it would mean has no business on ``/tmp``
    (deviation D4), and the binary's ``0755`` is set by the copy.
    """
    url = resolve_download_url(hv, version)
    extracted = extracted_binary(version, hv.arch)

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.unarchive,
            name="Download and unarchive node_exporter into temporary location.",
            src=url,
            dest=EXTRACT_DIR,
            creates=archive_dir(version, hv.arch),
            sudo=True,
        )
        yield op(
            files.copy,
            name=MOVE_NAME,
            src=extracted,
            dest=hv.bin_path,
            mode="0755",
            overwrite=True,
            sudo=True,
        )

    return deploy


def configure(hv: NodeExporterVars) -> Deploy:
    """The account, unit-file and service tasks (``tasks/main.yml`` 5-9).

    The group request only exists when a fixed ``gid`` was configured -- without
    one the role creates no group and ``useradd`` makes its own (deviation D11).
    The user request never builds a home directory (deviation D10), the unit file
    is installed as rendered content at ``0644`` root:root, and the service
    request carries ``daemon_reload=True`` so the role's separate reload task
    needs no second request (deviation D7).
    """
    unit = render_unit(hv)

    def deploy(host: Host) -> Generator[StageItem, Any]:
        if hv.gid is not None:
            yield op(
                server.group,
                name="Create node_exporter group (optional fixed GID)",
                group=SERVICE_USER,
                gid=hv.gid,
                sudo=True,
            )
        yield op(
            server.user,
            name="Create node_exporter user (optional fixed UID)",
            user=SERVICE_USER,
            shell=NOLOGIN_SHELL,
            ensure_home=False,
            group=SERVICE_USER if hv.gid is not None else None,
            uid=hv.uid,
            sudo=True,
        )
        yield op(
            files.install,
            name=UNIT_NAME,
            content=unit,
            dest=UNIT_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        yield op(
            systemd.service,
            name="Ensure node_exporter is running and enabled at boot.",
            service=SERVICE,
            enabled=hv.enabled,
            daemon_reload=True,
            sudo=True,
            **STATE_KEYWORDS[hv.state],
        )

    return deploy


# --- the factory --------------------------------------------------------------


def node_exporter(v: VarsSet[NodeExporterVars]) -> Deploy:
    """Install and run Prometheus' node_exporter on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`NodeExporterVars` per host
            (``resolve_vars(NodeExporterVars, "node_exporter", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("node_exporter", node_exporter(v))(host)``.

    Raises:
        ValueError: A role variable is invalid (raised by pydantic while the
            variables are resolved), the releases API answers with no usable
            ``tag_name``, or the metrics endpoint does not answer 200 with the
            ``Metrics`` link in its body -- the role's ``failed_when``.
        OSError: The control-side release lookup cannot reach
            :data:`RELEASE_API_URL`; the role's five retries are not reproduced
            (deviation D2).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # The role's ``command: --version`` with ``failed_when: false``: the
        # Result is data, so a missing binary is an empty stdout, not an error.
        probe = yield act(
            Shell, command=probe_command(hv), name="Check current node_exporter version."
        )

        version = hv.version
        if version == LATEST:
            # ``delegate_to: localhost`` + ``run_once`` as a per-host control
            # read (deviation D2); the resolved version is a local name, never a
            # rewritten host fact (deviation D3).
            api_text = yield control(files.fetch_url, RELEASE_API_URL)
            version = parse_release_tag(api_text)

        # Tasks 3 and 4's shared ``when``: the probe's stdout either lacks the
        # wanted version or is empty because nothing is installed yet.
        if version not in probe["stdout"]:
            yield from install(hv, version)(host)

        yield from configure(hv)(host)

        # The checkpoint *is* the notify: it closes the wave holding the copy and
        # the unit write, and nothing may be yielded between them (rule 4).
        wave_report = yield checkpoint("configure done")
        notified = changed_hosts(wave_report, NOTIFYING)
        yield from service_handler(
            notified, (SERVICE,), action="restarted"
        )(host)

        # The ``uri`` + ``failed_when`` verification, on the target (deviation
        # D8), after the restart so it reads the service this deploy brought up
        # (deviation D9).
        info = yield fact(
            server.HttpEndpoint,
            url=metrics_url(hv),
            name="Verify node_exporter is responding to requests.",
        )
        if info["status"] != 200 or METRICS_MARKER not in info["body"]:
            raise ValueError(
                f"node_exporter on {host.name} is not serving metrics at "
                f"{metrics_url(hv)}: status {info['status']!r}, "
                f"{METRICS_MARKER!r} in body: {METRICS_MARKER in info['body']}"
            )

    return deploy


__all__ = [
    "NodeExporterVars",
    "node_exporter",
]
