# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The hosts-file role (migrated from ``ajsalminen/ansible-role-hosts``).
#
# Manages ``/etc/hostname`` and ``/etc/hosts`` on Ubuntu/Debian hosts with the
# upstream role's two templates: the short hostname in ``/etc/hostname``, and in
# ``/etc/hosts`` a managed header, one line per entry (the IPv4 loopback entry,
# the host's default-route IPv4 entry, then the operator's additional entries)
# and the fixed IPv6 localhost block. The Ansible role's whole task list is one
# deploy here -- :func:`hosts` -- and its ``defaults/main.yml`` is
# :class:`HostsVars`, bound from the inventory entry's ``hosts`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's ``include_vars`` of
# ``vars/Darwin.yml`` / ``vars/FreeBSD.yml`` (only to switch the file group to
# ``wheel``) collapses away under that target, exactly as the other migrated
# roles' OS dispatch does.
#
# The stream carries two waves (design doc section 5.7):
#
# 0. ``fact(DefaultIpv4Address)`` -- the role's
#    ``{{ ansible_default_ipv4.address }}`` lookup. Skipped when
#    :attr:`HostsVars.default_ipv4_address` or :attr:`HostsVars.hosts_content`
#    supplies the value the entry line would need, so an overridden host parks
#    on nothing.
# 1. the requests: the two installs and their metadata companions, in the
#    role's task order (``hostname.yml`` before ``hosts.yml``).
#
# There is **no checkpoint**: the role has no handlers and nothing reads an
# outcome mid-play. Like every migrated role, :func:`hosts` publishes no
# outputs -- a composing role sequences it with
# ``yield from scoped("hosts", hosts(v))(host)``.
#
# The live kernel hostname is deliberately not set: the upstream role only
# manages the two files (the new name applies on reboot), so the migration
# changes nothing a config-management run should.
#
# Deviations from the Ansible role, all deliberate:
#
# * D1: the host-family guard is the body's opening ``if``, not a ``when=`` on
#   every request: a host outside the ``"Ubuntu"`` family returns before it
#   yields anything, so it emits no requests and no log lines at all.
# * D2: the two ``.j2`` templates are rendered as plain Python, not Jinja2:
#   each is pure substitution and a for-loop, and :func:`render_hosts` emits
#   byte-identical output to an Ansible-defaults render of
#   ``templates/hosts.j2`` (verified against the upstream template; only the
#   header line differs, see D3). No ``reemote[templates]`` extra.
# * D3: ``# {{ ansible_managed }}`` becomes the constant ``# Reemote managed``
#   -- reemote has no ``ansible_managed`` equivalent, and claiming Ansible
#   authorship would be wrong.
# * D4: the ``with_first_found`` per-host template lookup
#   (``hosts_<inventory_hostname>.j2`` / ``hostname_<inventory_hostname>.j2``)
#   has no analogue -- a library role ships no on-disk template directory. The
#   equivalent is :attr:`HostsVars.hosts_content` /
#   :attr:`HostsVars.hostname_content`: a verbatim content override, bindable
#   per host through the inventory's ``hosts`` role mapping.
# * D5: the opt-in ``hosts_override_hosts_template`` / ``server_override_hosts``
#   branch (a controller-side template path read at render time) is scoped out;
#   D4's content override covers the same need without a file search.
# * D6: ``vars/Darwin.yml`` / ``vars/FreeBSD.yml`` (group ``wheel``) are out of
#   target; :attr:`HostsVars.file_group` carries the knob and defaults to
#   ``"root"``, the Ubuntu path's value.
# * D7: ``hosts_default_hosts | union(hosts_additional_hosts)`` becomes plain
#   concatenation -- the defaults are structurally unique, and a duplicate an
#   operator adds is rendered as asked (Jinja's ``union`` over dict entries has
#   no reemote-side dedup).
# * D8: the template task's ``owner``/``group``/``mode`` are re-applied by a
#   companion ``files.file`` request: :func:`~reemote.modules.files.install`
#   idempotence is content-only, so without the pair an owner/mode drift would
#   never converge, where Ansible's ``template`` fixes it on the next run.
# """

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import files
from ..modules.hardware import DefaultIpv4Address, DefaultIpv4AddressInfo
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

# --- role constants (defaults/main.yml + templates/, Ubuntu path only) ---

#: What ``tasks/hostname.yml`` manages: ``dest: /etc/hostname``.
HOSTNAME_PATH = "/etc/hostname"

#: What ``tasks/hosts.yml`` manages: ``dest: /etc/hosts``.
HOSTS_PATH = "/etc/hosts"

#: The ``hosts.j2`` header line; D3 replaces ``{{ ansible_managed }}``.
MANAGED_HEADER = "# Reemote managed"

#: ``templates/hosts.j2``'s fixed tail, verbatim (including the field-gap
#: spacing), rendered after the entry lines and two blank lines.
IPV6_LINES = (
    "# The following lines are desirable for IPv6 capable hosts",
    "::1     ip6-localhost ip6-loopback",
    "fe00::0 ip6-localnet",
    "ff00::0 ip6-mcastprefix",
    "ff02::1 ip6-allnodes",
    "ff02::2 ip6-allrouters",
)

#: Both template tasks: ``mode: 0644``, owner ``root``.
FILE_MODE = "0644"


# --- variables ---------------------------------------------------------------


class HostsEntry(BaseModel):
    """One hosts-file line: an item of ``hosts_additional_hosts``.

    The structure the README describes (``address`` plus the ordered
    ``hostnames``; the first name is the canonical one) and the shape
    ``hosts.j2`` renders. Frozen and hashable so the containing
    :class:`HostsVars` model stays hashable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str
    hostnames: tuple[str, ...]

    @field_validator("address")
    @classmethod
    def _address_non_empty(cls, value: str) -> str:
        """An empty address would render a hostname with no line to key it to."""
        if not value:
            raise ValueError("an entry needs a non-empty address")
        return value

    @field_validator("hostnames")
    @classmethod
    def _hostnames_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """An entry with no names is a bare address line -- a typo, not intent."""
        if not value or not all(name for name in value):
            raise ValueError("an entry needs at least one non-empty hostname")
        return value


class HostsVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``hosts`` role mapping, so ``additional_hosts`` there
    sets :attr:`additional_hosts`. The two templated defaults
    (``hosts_default_ipv4_hosts``, ``hosts_all_hosts``) are derived, not
    fields: only their inputs are carried (:attr:`default_ipv4_address`,
    :attr:`default_ipv4_hostnames`, :attr:`additional_hosts`).

    The model is frozen, so it stays hashable through the deploy body
    (:class:`~reemote.rolevars.VarsSet` is closed over by the factory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``hosts_ipv4_loopback_hosts``, the role's default loopback line.
    ipv4_loopback: HostsEntry = HostsEntry(
        address="127.0.0.1",
        hostnames=("localhost.localdomain", "localhost"),
    )
    #: ``null`` reads the address from the ``DefaultIpv4Address`` barrier --
    #: the ``ansible_default_ipv4.address`` equivalent; a set value is the
    #: per-host override the role's variable would be.
    default_ipv4_address: str | None = None
    #: ``null`` derives ``(inventory_hostname, inventory_hostname_short)``
    #: from the host's name, as the role's default does.
    default_ipv4_hostnames: tuple[str, ...] | None = None
    #: ``hosts_additional_hosts``: appended after the two defaults (D7).
    additional_hosts: tuple[HostsEntry, ...] = ()
    #: ``hosts_file_group``; ``wheel`` only exists off-target (D6).
    file_group: str = "root"
    #: D4: the per-host ``with_first_found`` equivalent -- a verbatim
    #: ``/etc/hostname`` body instead of the rendered short name.
    hostname_content: str | None = None
    #: D4/D5: a verbatim ``/etc/hosts`` body instead of the rendered entries.
    hosts_content: str | None = None

    @field_validator("default_ipv4_address")
    @classmethod
    def _none_or_non_empty(cls, value: str | None) -> str | None:
        """An empty override is a mistake, not "use the fact"."""
        if value is not None and not value:
            raise ValueError("default_ipv4_address must be non-empty or null")
        return value

    @field_validator("default_ipv4_hostnames")
    @classmethod
    def _hostnames_valid(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        """A set override must name at least one non-empty hostname."""
        if value is not None and (not value or not all(name for name in value)):
            raise ValueError("default_ipv4_hostnames must be non-empty names or null")
        return value

    @field_validator("file_group")
    @classmethod
    def _group_non_empty(cls, value: str) -> str:
        """The chown target group must be a real name."""
        if not value:
            raise ValueError("file_group must be a non-empty group name")
        return value


@dataclass(frozen=True)
class HostsPlan:
    """One host's resolved file bodies: the pure half of the plan freeze.

    Built by :func:`resolve_hosts` from the resolved variables, the host's
    name and the address the barrier (or the override) produced, so both
    contents are decided before a request is built. Frozen and hashable, as a
    deploy body's local must be.
    """

    hostname_content: str
    hosts_content: str
    file_group: str


# --- pure helpers ------------------------------------------------------------


def short_hostname(name: str) -> str:
    """The ``inventory_hostname_short`` equivalent: the name before the first dot."""
    return name.split(".", 1)[0]


def render_hosts(entries: tuple[HostsEntry, ...]) -> str:
    """``templates/hosts.j2``, reproduced in Python (D2).

    Byte-identical to an Ansible-defaults (``trim_blocks``) Jinja render of
    the upstream template: the header line, the entry lines
    (``address hostname1 hostname2 ...``) one after another, two blank lines,
    then the fixed IPv6 block; the file ends with one trailing newline. Only
    the header text differs from the role's render (D3).
    """
    lines = [MANAGED_HEADER]
    lines.extend(" ".join((entry.address, *entry.hostnames)) for entry in entries)
    lines.extend(["", ""])
    lines.extend(IPV6_LINES)
    return "\n".join(lines) + "\n"


def resolve_hosts(v: HostsVars, host_name: str, address: str | None) -> HostsPlan:
    """Freeze both file bodies for one host. Pure: no host read, no IO.

    ``address`` is the barrier's (or the override's) default-IPv4 address and
    is only required when the entry list is actually rendered -- a verbatim
    :attr:`HostsVars.hosts_content` needs none. A content override wins
    verbatim, the way the role's found-template wins over the bundled one.

    Raises:
        ValueError: The default ``/etc/hosts`` must be rendered but no address
            is available. Raised before the first request is built, so the
            host is not half-deployed.
    """
    short = short_hostname(host_name)
    if v.hosts_content is not None:
        hosts_content = v.hosts_content
    else:
        if address is None:
            raise ValueError(
                f"{host_name}: rendering the default /etc/hosts needs a "
                "default IPv4 address"
            )
        names = v.default_ipv4_hostnames or (host_name, short)
        entries = (
            v.ipv4_loopback,
            HostsEntry(address=address, hostnames=names),
            *v.additional_hosts,
        )
        hosts_content = render_hosts(entries)
    hostname_content = v.hostname_content if v.hostname_content is not None else f"{short}\n"
    return HostsPlan(
        hostname_content=hostname_content,
        hosts_content=hosts_content,
        file_group=v.file_group,
    )


# --- helper deploys ----------------------------------------------------------


def configure(plan: HostsPlan) -> Deploy:
    """``tasks/hostname.yml`` + ``tasks/hosts.yml``: the two template tasks.

    Each file is one :func:`~reemote.modules.files.install` (the spool-and-
    install family -- ``/etc`` is root-owned and transfers never elevate) with
    the role's ``owner``/``group``/``mode``, plus the companion
    :func:`~reemote.modules.files.file` that converges an owner/mode drift on
    a content-equal file (D8). The install is a no-op when the file already
    holds the rendered content, which is what makes a converged host's run
    report no change.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.install,
            name="Set up /etc/hostname.",
            content=plan.hostname_content,
            dest=HOSTNAME_PATH,
            mode=FILE_MODE,
            user="root",
            group=plan.file_group,
            sudo=True,
        )
        yield op(
            files.file,
            name="Ensure /etc/hostname ownership and mode.",
            path=HOSTNAME_PATH,
            user="root",
            group=plan.file_group,
            mode=FILE_MODE,
            sudo=True,
        )
        yield op(
            files.install,
            name="Set up /etc/hosts.",
            content=plan.hosts_content,
            dest=HOSTS_PATH,
            mode=FILE_MODE,
            user="root",
            group=plan.file_group,
            sudo=True,
        )
        yield op(
            files.file,
            name="Ensure /etc/hosts ownership and mode.",
            path=HOSTS_PATH,
            user="root",
            group=plan.file_group,
            mode=FILE_MODE,
            sudo=True,
        )

    return deploy


# --- the role ----------------------------------------------------------------


def hosts(v: VarsSet[HostsVars]) -> Deploy:
    """Manage ``/etc/hosts`` and ``/etc/hostname`` on Ubuntu-family hosts.

    Args:
        v: The role's resolved variables, one :class:`HostsVars` per host
            (``resolve_vars(HostsVars, "hosts", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("hosts", hosts(v))(host)``.

    Raises:
        ValueError: The default ``/etc/hosts`` entry needs an address but the
            host has no default route (the fact read no address). Raised after
            the barrier and before any request is built, so nothing is
            half-applied.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        address = hv.default_ipv4_address
        if hv.hosts_content is None and address is None:
            # Wave 0: the role's `{{ ansible_default_ipv4.address }}` --
            # the interface carrying the default route, per host, so the read
            # is a barrier rather than factory-time work.
            info: DefaultIpv4AddressInfo | None = yield fact(DefaultIpv4Address)
            address = info["address"] if info is not None else None
            if address is None:
                raise ValueError(
                    f"{host.name}: no default IPv4 address found; set the "
                    "default_ipv4_address variable (or a full hosts_content) "
                    "for a host without a default route"
                )

        plan = resolve_hosts(hv, host.name, address)

        # One request wave, in the role's task order: hostname.yml first.
        yield from configure(plan)(host)

    return deploy


__all__ = [
    "HostsEntry",
    "HostsVars",
    "hosts",
]
