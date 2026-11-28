# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The mailhog role (migrated from ``geerlingguy.mailhog``).
#
# Installs `MailHog <https://github.com/mailhog/MailHog>`_, a Go-based SMTP
# server and web UI for capturing emails, plus its ``mhsendmail`` sendmail
# shim, on Ubuntu hosts: the release binaries downloaded into ``/opt/mailhog``
# and the ``mailhog.service`` systemd unit that runs and enables the daemon.
# :func:`mailhog` is the whole task list and :class:`MailhogVars` is
# ``defaults/main.yml``, bound from the inventory's ``mailhog`` role mapping
# by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family, systemd only. A host outside
# the target family returns before its first ``yield`` and emits no requests,
# no barriers and no log lines at all.
#
# The stream is one deploy body carrying a single ``fact`` barrier and five
# requests -- no checkpoint, no handler, no ``act``/``control`` read, because
# the role ships no ``handlers/main.yml`` and nothing between its tasks reads
# remote state. The first wave closes the ``fact(server.Arch)`` gate (the
# role's ``ansible_architecture`` lookup inside the two URL defaults), the
# second carries the five requests in the role's task order:
# ``files.directory`` for the install directory, two ``files.download``s for
# the binaries (the ``get_url`` ``with_items`` loop, one request per item),
# ``files.install`` for the rendered unit, ``systemd.service`` for the
# started-and-enabled unit. A converged host re-runs the same two waves --
# the fact read and the requests are idempotent, but the ``Arch`` gate never
# disappears.
#
# Deviations from the Ansible role, all deliberate:
#
# * **D1** the ``ansible_facts.service_mgr`` dispatch freezes to systemd: the
#   ``mailhog.init.j2`` branch (and with it the ``mailhog_daemonize_bin_path``
#   default and the ``geerlingguy.daemonize`` role dependency it exists to
#   serve) is scoped out, because the target family's servers run systemd.
#   An Ubuntu host without systemd would need the init script, which the
#   migration does not ship; the family guard drops non-Ubuntu hosts.
# * **D2** the binaries are fetched **control-side** by
#   :func:`~reemote.modules.files.download` (spool-installed); Ansible's
#   ``get_url`` downloads on the target. The control machine needs egress to
#   ``github.com``; the host downloads nothing.
# * **D3** idempotence is content-only and **unchecksummed**, exactly as the
#   role's ``get_url`` (no ``checksum`` argument): a converged pass re-fetches
#   each URL control-side to compare, and a pure permission drift on an
#   installed binary is not re-applied. The ``0755`` mode the role installs
#   at is what keeps the binary sha1 read-back (unprivileged) cheap.
# * **D4** the ``mailhog_arch`` default freezes to :func:`arch_for`, the
#   role's own literal ternary: ``aarch64`` becomes ``arm``, every other
#   machine becomes ``amd64`` (MailHog publishes only those two Linux
#   architectures, so unlike ``kubectl`` there is no fail-fast map -- an
#   unmapped machine gets the role's answer, ``amd64``).
# * **D5** the unit template is pure substitution -- one interpolation of
#   ``mailhog_install_dir`` -- so it renders as an f-string: no Jinja2, no
#   ``reemote[templates]`` extra. :func:`render_unit` reproduces
#   ``mailhog.unit.j2`` byte-for-byte, including the single trailing newline.
# * **D6** the unit file keeps the role's unusual ``mode: 0755`` (systemd
#   reads ``/etc/systemd/system`` itself; the executable bit is meaningless
#   there) rather than normalizing to ``0644``, for byte-level fidelity to
#   what the role leaves on disk.
# * **D7** ``service: name=mailhog state=started enabled=yes`` becomes one
#   ``systemd.service`` request carrying ``daemon_reload=True``: Ansible's
#   service module re-reads unit files behind the scenes after the template
#   task wrote one, and reemote's reload is state-gated on
#   ``SystemdNeedDaemonReload``, so a converged host issues no ``daemon-reload``
#   -- the same net behaviour, in one request.
# * **D8** the role has **no handlers**: changing the unit file or replacing
#   a running binary does not restart a service that is already up, upstream
#   included. The migration preserves that exactly -- no checkpoint, no
#   restart handler is added.
# * **D9** privilege: the role's tasks carry no per-task ``become`` (plays
#   are expected to run the role with escalation); here every request is
#   flagged ``sudo=True`` and the run-as identity comes from the inventory
#   ``privilege`` section, never from an operation argument.
# """

from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import files, server, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml, Ubuntu/systemd only) ------------------

#: The default install directory (``mailhog_install_dir``).
DEFAULT_INSTALL_DIR = "/opt/mailhog"

#: The default MailHog release (``mailhog_version``).
DEFAULT_MAILHOG_VERSION = "1.0.0"

#: The default mhsendmail release (``mhsendmail_version``).
DEFAULT_MHSENDMAIL_VERSION = "0.2.0"

#: The GitHub releases download bases the two URL defaults are built from.
MAILHOG_RELEASE_BASE = "https://github.com/mailhog/MailHog/releases/download"
MHSENDMAIL_RELEASE_BASE = (
    "https://github.com/mailhog/mhsendmail/releases/download"
)

#: The unit file and the service it defines (``mailhog.unit.j2`` target).
UNIT_PATH = "/etc/systemd/system/mailhog.service"
SERVICE = "mailhog"

#: The machine :func:`arch_for` maps to ``arm`` -- the role's whole ternary.
AARCH64 = "aarch64"


# --- variables ---------------------------------------------------------------


class MailhogVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``mailhog`` role mapping, so ``install_dir`` there sets
    :attr:`install_dir`. The model is frozen, so it stays hashable through the
    deploy body (a :class:`~reemote.rolevars.VarsSet` is closed over by the
    factory), and ``extra="forbid"`` turns a typo in that mapping into a
    ``ValidationError`` raised before any host is contacted.

    ``mailhog_daemonize_bin_path`` is deliberately absent: it only feeds the
    init script, which is scoped out (deviation D1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Where the binaries land (``mailhog_install_dir``); also interpolated
    #: into the unit's ``ExecStart``.
    install_dir: str = DEFAULT_INSTALL_DIR
    #: The MailHog release (``mailhog_version``), unless :attr:`binary_url`
    #: overrides the whole formula.
    version: str = DEFAULT_MAILHOG_VERSION
    #: The full MailHog binary URL (``mailhog_binary_url``). ``None`` -- the
    #: default -- derives it from :attr:`version` and the host's architecture.
    binary_url: str | None = None
    #: The mhsendmail release (``mhsendmail_version``), unless
    #: :attr:`mhsendmail_binary_url` overrides the formula.
    mhsendmail_version: str = DEFAULT_MHSENDMAIL_VERSION
    #: The full mhsendmail binary URL (``mhsendmail_binary_url``); ``None``
    #: derives it from :attr:`mhsendmail_version` and the architecture.
    mhsendmail_binary_url: str | None = None

    @field_validator("install_dir")
    @classmethod
    def _install_dir_absolute(cls, value: str) -> str:
        """A relative install dir would depend on the daemon's cwd."""
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"mailhog_install_dir must be a non-empty string, got {value!r}"
            )
        if not value.startswith("/"):
            raise ValueError(
                f"mailhog_install_dir must be absolute, got {value!r}"
            )
        return value.rstrip("/")

    @field_validator("version", "mhsendmail_version")
    @classmethod
    def _version_form(cls, value: str) -> str:
        """Non-empty and path-safe: each version feeds a URL."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"mailhog version must be a non-empty string, got {value!r}"
            )
        value = value.strip().lstrip("v")
        if "/" in value or value.startswith("."):
            raise ValueError(
                f"mailhog version must be a plain release name, got {value!r}"
            )
        return value

    @field_validator("binary_url", "mhsendmail_binary_url")
    @classmethod
    def _url_form(cls, value: str | None) -> str | None:
        """An override must be an http(s) URL (``files.download``'s guard)."""
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                f"mailhog binary URL must be an http(s) URL, got {value!r}"
            )
        return value


# --- pure helpers ------------------------------------------------------------


def arch_for(machine: str) -> str:
    """``mailhog_arch`` -- the role's literal ternary, frozen (deviation D4).

    ``{{ 'arm' if ansible_architecture == 'aarch64' else 'amd64' }}``: the
    architecture suffix MailHog and mhsendmail append to their Linux release
    names. Everything that is not ``aarch64`` gets ``amd64``, because those
    are the only two archives the projects publish.
    """
    return "arm" if machine == AARCH64 else "amd64"


def mailhog_binary_url(version: str, arch: str) -> str:
    """``mailhog_binary_url``' formula from ``defaults/main.yml``."""
    return f"{MAILHOG_RELEASE_BASE}/v{version}/MailHog_linux_{arch}"


def mhsendmail_binary_url(version: str, arch: str) -> str:
    """``mhsendmail_binary_url``' formula from ``defaults/main.yml``."""
    return f"{MHSENDMAIL_RELEASE_BASE}/v{version}/mhsendmail_linux_{arch}"


def render_unit(install_dir: str) -> str:
    """``templates/mailhog.unit.j2``, rendered with an f-string (deviation D5).

    The template interpolates exactly one variable -- ``mailhog_install_dir``
    in the ``ExecStart`` line -- so the render is plain Python, byte-identical
    to the Jinja2 output including the single trailing newline.
    """
    return (
        "[Unit]\n"
        "Description=MailHog Email Catcher\n"
        "After=syslog.target network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={install_dir}/mailhog\n"
        "StandardOutput=journal\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


# --- the deploy stages (each returns a per-host generator) --------------------


def install(hv: MailhogVars, arch: str) -> Deploy:
    """The role's first two tasks: the directory and the ``get_url`` loop.

    The ``with_items`` pair becomes one ``files.download`` request per item
    (control-side fetch, deviation D2), each installed ``0755`` root:root
    exactly as the task's ``mode``/``owner``/``group`` spell it (deviation
    D3). ``sudo=True`` on every request (deviation D9).
    """
    mailhog_url = hv.binary_url or mailhog_binary_url(hv.version, arch)
    mhsendmail_url = hv.mhsendmail_binary_url or mhsendmail_binary_url(
        hv.mhsendmail_version, arch
    )

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.directory,
            name="Ensure mailhog install directory exists.",
            path=hv.install_dir,
            user="root",
            group="root",
            mode="0755",
            sudo=True,
        )
        yield op(
            files.download,
            name="Download the MailHog binary.",
            url=mailhog_url,
            dest=f"{hv.install_dir}/mailhog",
            user="root",
            group="root",
            mode="0755",
            sudo=True,
        )
        yield op(
            files.download,
            name="Download the mhsendmail binary.",
            url=mhsendmail_url,
            dest=f"{hv.install_dir}/mhsendmail",
            user="root",
            group="root",
            mode="0755",
            sudo=True,
        )

    return deploy


def configure(hv: MailhogVars) -> Deploy:
    """The role's unit-file and service tasks (the systemd branch, D1).

    The rendered unit is installed with :func:`~reemote.modules.files.install`
    at the role's ``0755`` (deviation D6), and the ``service:`` task becomes
    one ``systemd.service`` request: started, enabled, with the state-gated
    ``daemon_reload=True`` that reads the unit file this same wave may have
    just written (deviation D7).
    """
    unit = render_unit(hv.install_dir)

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.install,
            name="Copy mailhog systemd unit file into place.",
            content=unit,
            dest=UNIT_PATH,
            user="root",
            group="root",
            mode="0755",
            sudo=True,
        )
        yield op(
            systemd.service,
            name="Ensure mailhog is enabled and will start on boot.",
            service=SERVICE,
            running=True,
            enabled=True,
            daemon_reload=True,
            sudo=True,
        )

    return deploy


# --- the factory --------------------------------------------------------------


def mailhog(v: VarsSet[MailhogVars]) -> Deploy:
    """Install the MailHog binaries and run its systemd unit on Ubuntu hosts.

    Args:
        v: The role's resolved variables, one :class:`MailhogVars` per host
            (``resolve_vars(MailhogVars, "mailhog", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("mailhog", mailhog(v))(host)``.

    Raises:
        ValueError: A role variable is invalid (raised by pydantic while the
            variables are resolved, before any host is contacted). The
            architecture lookup never raises -- the role's own ternary maps
            every non-``aarch64`` machine to ``amd64`` (deviation D4).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # The role's ``ansible_architecture`` fact-gathering read, in-stream:
        # the one barrier, closing the first wave. It decides both download
        # URL suffixes (deviation D4) before a byte is fetched.
        machine = yield fact(server.Arch)
        arch = arch_for(machine)

        yield from install(hv, arch)(host)
        yield from configure(hv)(host)

    return deploy


__all__ = [
    "MailhogVars",
    "mailhog",
]
