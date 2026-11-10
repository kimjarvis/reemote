# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``lxd`` module: comprehensive LXD management.
#
# Fact side: ``LxdContainers`` (the fast container list),
# ``LxdImageAliases`` (alias-to-fingerprint
# mappings), ``LxdImages`` (the local image store), ``LxdInstanceConfig``
# (one instance's expanded configuration), ``LxdInstanceSnapshots`` (one
# instance's snapshots), ``LxdInstanceState`` (one instance's runtime
# state), ``LxdInstances`` (all containers and VMs with full metadata),
# ``LxdNetworks`` (managed and unmanaged networks), ``LxdOperations``
# (in-flight background operations), ``LxdProfiles`` (configuration
# profiles), ``LxdProjectConfig`` (one project's configuration),
# ``LxdProjects`` (the server's projects), ``LxdServerInfo`` (daemon
# metadata and API extensions), ``LxdServerResources`` (host hardware),
# ``LxdStoragePools`` (storage pools and drivers) and ``LxdStorageVolumes``
# (a pool's volumes). Every fact but ``LxdContainers`` is reemote5-original
# and closes a visibility gap.
#
# Operation side: ``lxd.container`` creates/removes a container by
# image; ``lxd.instance`` extends it to
# containers AND VMs with profiles/config/devices and non-destructive
# reconciliation; ``lxd.instance_state`` drives the lifecycle
# (start/stop/freeze/restart/resume); ``lxd.instance_config`` updates an
# existing instance's config, devices and profiles;
# ``lxd.instance_snapshot`` creates/deletes/restores snapshots;
# ``lxd.instance_copy`` copies an instance; ``lxd.image`` caches or
# removes images; ``lxd.image_alias`` manages aliases; ``lxd.project``,
# ``lxd.network``, ``lxd.storage_pool``, ``lxd.storage_volume`` and
# ``lxd.profile`` manage the server's resources; and ``lxd.operation``
# waits on or cancels a background operation.
#
# Transport decision (developer-approved): everything runs through the
# ``lxc`` CLI on the target host with ``--format json``, never the REST
# API directly. The CLI blocks on LXD's asynchronous operations natively
# (no manual 202/operation-wait handling), and remotes/TLS certificates
# come from the host's configured ``lxc remote`` list through the
# ``remote`` parameter (an empty string means the local daemon).
# Project-scoped facts and operations take ``project`` (default
# ``"default"``) and render an explicit ``--project`` flag; the
# ``LxdContainers``/``container`` pair keeps its
# exact unscoped commands.
#
# Image-drift decision (developer-approved): ``lxd.instance`` reconciles
# profiles/config/devices non-destructively, but an existing instance
# whose base image differs from the requested one is only REPORTED in
# the reason -- never deleted and recreated. Destructive recreation
# stays an explicit user choice (``present=False`` then re-run).
#
# Hashability: ``op()`` rejects mutable request arguments, so config
# options are tuples of ``"key=value"`` strings and devices are tuples of
# ``(name, type, ("key=value", ...))`` triples -- never dicts.
# """

import asyncio
import json
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict, cast

from ...commands import Command, WaitCommand, WaitTimeoutError
from ...core import OpContext, OperationError
from ...hosts import Host


def _parse_json(output: Iterable[str]) -> Any:
    """Join ``lxc --format json`` stdout lines and decode the document.

    Empty output reads as None -- the guarded single-target facts append
    ``2>/dev/null || true``, so a missing instance/project/pool produces
    no stdout at all. The decode itself is total over well-formed lxc
    output; a non-JSON document would raise ``json.JSONDecodeError``,
    which no real lxc success path produces.
    """
    text = "\n".join(output).strip()
    if not text:
        return None
    return json.loads(text)


def _lxc_command(parts: list[str], project: str = "") -> str:
    """Render ``lxc <parts...> [--project <project>]``.

    The ``--project`` flag is appended only for a non-empty project, so
    the parity descriptors (which carry ``project=""``) render the
    exact unscoped shell. New operations pass their
    ``project`` option through verbatim, rendering the flag explicitly
    even for ``"default"`` -- the CLI's *current* project can be switched
    with ``lxc project switch``, so relying on it would be ambiguous.
    """
    command = " ".join(["lxc", *parts])
    if project:
        command += f" --project {shlex.quote(project)}"
    return command


def _remote_name(name: str, remote: str) -> str:
    """Prefix a resource name with ``<remote>:`` when a remote is given.

    The lxc CLI addresses remote resources as ``remote:name`` (and a
    bare ``remote:`` for collection listings); an empty remote means the
    local daemon. The composed string is quoted by the caller.
    """
    return f"{remote}:{name}" if remote else name


def _config_pairs(config: tuple[str, ...]) -> list[tuple[str, str]]:
    """Split ``"key=value"`` config strings into (key, value) pairs.

    Values may themselves contain ``=`` (only the first one splits);
    an entry without ``=`` or with an empty key raises ``ValueError`` in
    prepare, before any command is planned.
    """
    pairs = []
    for entry in config:
        key, sep, value = entry.partition("=")
        if not sep or not key:
            raise ValueError(f"invalid config entry {entry!r}: expected 'key=value'")
        pairs.append((key, value))
    return pairs


def _device_props(props: tuple[str, ...]) -> dict[str, str]:
    """Split a device's ``"key=value"`` property strings into a dict."""
    return dict(_config_pairs(props))


# --- Facts -------------------------------------------------------------------


class LxdContainer(TypedDict):
    """
    Attributes:
        name: The container's name.
        status: The container's status (``Running``, ``Stopped``,
            ``Frozen``); absent when the fast listing omits it.
        type: The instance type (``container``); absent when the fast
            listing omits it.
        ipv4: The container's IPv4 address, empty when unassigned.
        ipv6: The container's IPv6 address, empty when unassigned.
        snapshots: The number of snapshots the container holds.
    """

    name: str
    status: NotRequired[str]
    type: NotRequired[str]
    ipv4: NotRequired[str]
    ipv6: NotRequired[str]
    snapshots: NotRequired[int]


class LxdContainers:
    """The fast listing of LXD containers on the local daemon.

    **Commands:**

    ```bash
    lxc list --format json --fast
    ```

    **Returns:**

    ```python
    list[LxdContainer]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_containers.py``.
    """

    # The raw JSON array of
    # ``lxc list --format json --fast`` passed through verbatim (the
    # MysqlUser carry-whatever-the-server-reports precedent -- the fast
    # columns are name/status/ipv4/ipv6/type/snapshots, but any extra
    # keys survive). Two deliberate design choices: empty stdout parses
    # to [] rather than crashing on an ``assert len(output) == 1``, and
    # there is no command-existence gate (a missing lxc exits 127,
    # reported as CommandNotFoundError). The command is unscoped -- it
    # reads the CLI's current remote/project; LxdInstances is the
    # explicit remote/project-scoped superset covering containers AND
    # VMs with full metadata.

    #: Shell used to gather the value over SSH.
    COMMAND = "lxc list --format json --fast"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdContainer]:
        """Parse the fast JSON listing into container entries."""
        data = _parse_json(output)
        return cast(list[LxdContainer], data if data is not None else [])

    @classmethod
    async def process(cls, host: Host) -> list[LxdContainer]:
        containers = getattr(host, "lxd_containers", None)
        if containers is not None:
            # SimulatedHost seam: the container entries, copied per
            # entry so callers never mutate simulated state.
            return [cast(LxdContainer, dict(entry)) for entry in containers]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class LxdImage(TypedDict, total=False):
    """
    Attributes:
        fingerprint: The image's full SHA-256 fingerprint (64 hex chars).
        aliases: The aliases pointing at the image, each a dict with
            ``name`` and ``description``; empty when unaliased.
        filename: The original image file name, empty for copies.
        size: The image size in bytes.
        type: The instance type the image creates (``container`` or
            ``virtual-machine``).
        public: Whether the image is publicly readable.
        auto_update: Whether the image auto-updates from its source.
        properties: The image properties (``os``, ``release``,
            ``architecture``, ``description``, ...).
        update_source: The remote the image updates from, when
            auto-update is enabled.
        created_at: The image build timestamp (RFC 3339).
        uploaded_at: The local cache timestamp (RFC 3339).
        expires_at: The expiry timestamp (RFC 3339).
        cached: Whether the image is a cached remote copy.
        profiles: The profiles baked into the image.
    """


class LxdImages:
    """The images cached in the local (or a remote) image store.

    **Commands:**

    ```bash
    lxc image list [<remote>:] --format json [--project <project>]
    ```

    **Returns:**

    ```python
    list[LxdImage]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_images.py``.
    """

    # reemote5-original: there is no other image-visibility fact. The raw
    # JSON array of ``lxc image list --format json`` passed through
    # verbatim; the LxdImage entry documents the common keys but any
    # extra keys the server reports survive. Images are project-scoped
    # only when the project enables features.images; the --project flag
    # is rendered explicitly for determinism. lxd.image compares
    # fingerprints and aliases against this fact, and lxd.instance
    # resolves a desired image reference to a fingerprint through it.

    @classmethod
    def command(cls, remote: str = "", project: str = "default") -> str:
        """Shell listing the image store as JSON."""
        parts = ["image", "list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts, project)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdImage]:
        """Parse the JSON image listing into image entries."""
        data = _parse_json(output)
        return cast(list[LxdImage], data if data is not None else [])

    @classmethod
    async def process(
        cls, host: Host, remote: str = "", project: str = "default"
    ) -> list[LxdImage]:
        images = getattr(host, "lxd_images", None)
        if images is not None:
            # SimulatedHost seam: one simulated daemon per host, so the
            # remote/project kwargs do not key the seam.
            return [cast(LxdImage, dict(entry)) for entry in images]
        stdout = await host.run_shell(cls.command(remote, project))
        return cls.parse(stdout.splitlines())


class LxdImageAlias(TypedDict, total=False):
    """
    Attributes:
        name: The alias name, e.g. ``ubuntu/24.04``.
        description: The alias description, empty when unset.
        target: The fingerprint the alias points at.
        type: The alias target type (always ``image``).
    """


class LxdImageAliases:
    """The image aliases and the fingerprints they point at.

    **Commands:**

    ```bash
    lxc image alias list [<remote>:] --format json [--project <project>]
    ```

    **Returns:**

    ```python
    list[LxdImageAlias]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_image_aliases.py``.
    """

    # reemote5-original: there is no other alias-visibility fact. The raw
    # JSON array of ``lxc image alias list --format json`` passed
    # through verbatim. lxd.image_alias compares the desired
    # alias-to-fingerprint mapping against this fact.

    @classmethod
    def command(cls, remote: str = "", project: str = "default") -> str:
        """Shell listing the image aliases as JSON."""
        parts = ["image", "alias", "list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts, project)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdImageAlias]:
        """Parse the JSON alias listing into alias entries."""
        data = _parse_json(output)
        return cast(list[LxdImageAlias], data if data is not None else [])

    @classmethod
    async def process(
        cls, host: Host, remote: str = "", project: str = "default"
    ) -> list[LxdImageAlias]:
        aliases = getattr(host, "lxd_image_aliases", None)
        if aliases is not None:
            # SimulatedHost seam: copied per entry.
            return [cast(LxdImageAlias, dict(entry)) for entry in aliases]
        stdout = await host.run_shell(cls.command(remote, project))
        return cls.parse(stdout.splitlines())


class LxdInstance(TypedDict, total=False):
    """
    Attributes:
        name: The instance name.
        description: The instance description, empty when unset.
        status: The instance status (``Running``, ``Stopped``,
            ``Frozen``).
        status_code: The LXD numeric status code (103 Running,
            102 Stopped, 110 Frozen, ...).
        type: The instance type (``container`` or ``virtual-machine``).
        architecture: The instance architecture, e.g. ``x86_64``.
        ephemeral: Whether the instance is deleted when it stops.
        stateful: Whether the instance's state was saved at stop.
        location: The cluster member hosting the instance (``none``
            on a standalone server).
        created_at: The creation timestamp (RFC 3339).
        last_used_at: The last-start timestamp (RFC 3339).
        config: The instance's OWN configuration keys, including the
            ``volatile.*`` and ``image.*`` keys LXD records at launch
            (``volatile.base_image`` is the base image fingerprint).
        devices: The instance's OWN devices, keyed by device name,
            each a dict with a ``type`` key plus its properties.
        expanded_config: The configuration with profile inheritance
            applied (present on full listings and expanded reads).
        expanded_devices: The devices with profile inheritance applied.
        profiles: The profile names applied to the instance, in order.
    """


class LxdInstances:
    """All LXD instances (containers and VMs) with full metadata.

    **Commands:**

    ```bash
    lxc list [<remote>:] --format json [--project <project>]
    ```

    **Returns:**

    ```python
    list[LxdInstance]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_instances.py``.
    """

    # reemote5-original superset of ``LxdContainers``:
    # the FULL ``lxc list --format json`` (no --fast), so each entry
    # carries config/devices/profiles and the volatile.base_image
    # fingerprint lxd.instance's drift report compares against, and VMs
    # appear alongside containers. Explicit remote/project scoping
    # replaces the port's reliance on the CLI's current remote/project.
    # The raw JSON array is passed through verbatim.

    @classmethod
    def command(cls, remote: str = "", project: str = "default") -> str:
        """Shell listing every instance as JSON."""
        parts = ["list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts, project)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdInstance]:
        """Parse the JSON instance listing into instance entries."""
        data = _parse_json(output)
        return cast(list[LxdInstance], data if data is not None else [])

    @classmethod
    async def process(
        cls, host: Host, remote: str = "", project: str = "default"
    ) -> list[LxdInstance]:
        instances = getattr(host, "lxd_instances", None)
        if instances is not None:
            # SimulatedHost seam: one simulated daemon per host, so the
            # remote/project kwargs do not key the seam.
            return [cast(LxdInstance, dict(entry)) for entry in instances]
        stdout = await host.run_shell(cls.command(remote, project))
        return cls.parse(stdout.splitlines())


class LxdInstanceConfig:
    """One instance's configuration, with profile inheritance expanded.

    **Commands:**

    ```bash
    lxc config show <remote>:<instance> --expanded --format json [--project <project>] 2>/dev/null || true
    ```

    **Returns:**

    ```python
    LxdInstance | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_instance_config.py``.
    """

    # reemote5-original: there is no other per-instance configuration fact.
    # ``lxc config show --expanded`` returns the instance's editable
    # document: its OWN config/devices plus expanded_config/
    # expanded_devices with profile inheritance applied, so the same
    # LxdInstance entry type serves both this fact and LxdInstances.
    # The 2>/dev/null || true guard reads a MISSING instance as None
    # (the mysql.MysqlUserGrants absent-user precedent) and masks exit
    # 127, so CommandNotFoundError is not on the Raises list.
    # lxd.instance_config compares desired keys against the OWN config
    # (an inherited value is not an own key, so setting it IS a change).

    @classmethod
    def command(
        cls, instance: str, remote: str = "", project: str = "default"
    ) -> str:
        """Shell reading one instance's expanded configuration as JSON."""
        parts = [
            "config",
            "show",
            shlex.quote(_remote_name(instance, remote)),
            "--expanded",
            "--format",
            "json",
        ]
        return _lxc_command(parts, project) + " 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> LxdInstance | None:
        """Parse the JSON configuration document; empty reads as None."""
        data = _parse_json(output)
        return cast(LxdInstance, data) if data is not None else None

    @classmethod
    async def process(
        cls, host: Host, instance: str, remote: str = "", project: str = "default"
    ) -> LxdInstance | None:
        configs = getattr(host, "lxd_instance_configs", None)
        if configs is not None:
            # SimulatedHost seam: instance name -> configuration
            # document; a missing name reads as None.
            entry = configs.get(instance)
            return cast(LxdInstance, dict(entry)) if entry is not None else None
        stdout = await host.run_shell(cls.command(instance, remote, project))
        return cls.parse(stdout.splitlines())


class LxdInstanceStateData(TypedDict, total=False):
    """
    Attributes:
        name: The instance name.
        status: The runtime status (``Running``, ``Stopped``,
            ``Frozen``).
        type: The instance type (``container`` or ``virtual-machine``).
        architecture: The instance architecture.
        pid: The init process ID; absent or 0 when stopped.
        processes: The number of processes; absent when stopped.
        cpu: CPU usage counters (``usage`` in nanoseconds); absent
            when stopped.
        memory: Memory usage gauges (``usage``, ``usage_peak``,
            ``swap_usage``, ``swap_usage_peak`` in bytes); absent when
            stopped.
        network: Per-interface runtime state keyed by interface name,
            each with ``addresses`` (family/address/netmask/scope),
            ``counters``, ``hwaddr``, ``mtu``, ``state`` and ``type``;
            absent when stopped.
        disk: Per-disk usage gauges keyed by device name; absent when
            stopped.
        snapshots: The instance's snapshots (the same entries
            ``LxdInstanceSnapshots`` projects).
    """


class LxdInstanceState:
    """One instance's runtime state: status, PID, memory and network.

    **Commands:**

    ```bash
    lxc info <remote>:<instance> --format json [--project <project>] 2>/dev/null || true
    ```

    **Returns:**

    ```python
    LxdInstanceStateData | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_instance_state.py``.
    """

    # reemote5-original: there is no other instance-state fact. ``lxc info
    # <instance> --format json`` is the CLI rendering of the REST
    # /1.0/instances/<name>/state resource plus identity and snapshots;
    # the runtime keys (pid/processes/cpu/memory/network/disk) appear
    # only while the instance runs. The 2>/dev/null || true guard reads
    # a MISSING instance as None and masks exit 127.
    # lxd.instance_state compares the status string against this fact
    # for idempotent start/stop/freeze. The entry type carries the
    # "Data" suffix because the fact name takes the singular slot (the
    # FirewalldStatusInfo naming precedent).

    @classmethod
    def command(
        cls, instance: str, remote: str = "", project: str = "default"
    ) -> str:
        """Shell reading one instance's runtime state as JSON."""
        parts = [
            "info",
            shlex.quote(_remote_name(instance, remote)),
            "--format",
            "json",
        ]
        return _lxc_command(parts, project) + " 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> LxdInstanceStateData | None:
        """Parse the JSON state document; empty reads as None."""
        data = _parse_json(output)
        return cast(LxdInstanceStateData, data) if data is not None else None

    @classmethod
    async def process(
        cls, host: Host, instance: str, remote: str = "", project: str = "default"
    ) -> LxdInstanceStateData | None:
        states = getattr(host, "lxd_instance_states", None)
        if states is not None:
            # SimulatedHost seam: instance name -> state document; a
            # missing name reads as None.
            entry = states.get(instance)
            return cast(LxdInstanceStateData, dict(entry)) if entry is not None else None
        stdout = await host.run_shell(cls.command(instance, remote, project))
        return cls.parse(stdout.splitlines())


class LxdSnapshot(TypedDict, total=False):
    """
    Attributes:
        name: The snapshot name (the part after ``instance/``).
        description: The snapshot description, empty when unset.
        created_at: The creation timestamp (RFC 3339).
        expires_at: The expiry timestamp (RFC 3339); the zero time
            when the snapshot never expires.
        stateful: Whether the snapshot includes the runtime state.
    """


class LxdInstanceSnapshots:
    """The snapshots of one LXD instance.

    **Commands:**

    ```bash
    lxc info <remote>:<instance> --format json [--project <project>] 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[LxdSnapshot]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/lxd/lxd_lxd_instance_snapshots.py``.
    """

    # reemote5-original: there is no other snapshot-visibility fact. Shares
    # LxdInstanceState's ``lxc info <instance>`` command (the DnfRepoFile
    # shared-command precedent) and projects the document's snapshots
    # array; a missing instance or an instance without snapshots reads
    # as []. lxd.instance_snapshot compares snapshot names against this
    # fact.

    @classmethod
    def command(
        cls, instance: str, remote: str = "", project: str = "default"
    ) -> str:
        """Shell reading the instance document the snapshots project from."""
        parts = [
            "info",
            shlex.quote(_remote_name(instance, remote)),
            "--format",
            "json",
        ]
        return _lxc_command(parts, project) + " 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdSnapshot]:
        """Project the snapshots array out of the JSON instance document."""
        data = _parse_json(output)
        if not data:
            return []
        return cast(list[LxdSnapshot], data.get("snapshots") or [])

    @classmethod
    async def process(
        cls, host: Host, instance: str, remote: str = "", project: str = "default"
    ) -> list[LxdSnapshot]:
        snapshots = getattr(host, "lxd_instance_snapshots", None)
        if snapshots is not None:
            # SimulatedHost seam: instance name -> snapshot entries,
            # copied per entry; a missing name reads as [].
            return [
                cast(LxdSnapshot, dict(entry))
                for entry in snapshots.get(instance, [])
            ]
        stdout = await host.run_shell(cls.command(instance, remote, project))
        return cls.parse(stdout.splitlines())


class LxdNetwork(TypedDict, total=False):
    """
    Attributes:
        name: The network name, e.g. ``lxdbr0``.
        description: The network description, empty when unset.
        type: The network type (``bridge``, ``macvlan``, ``ovn``,
            ``physical``, ``sriov``).
        managed: Whether LXD manages the network (an unmanaged entry
            is a host interface LXD merely knows about).
        status: The network status (``Created``, ``Pending``,
            ``Errored``).
        config: The network configuration (``ipv4.address``,
            ``ipv4.nat``, ``ipv6.address``, ``dns.domain``, ...).
        locations: The cluster members the network exists on
            (``["none"]`` on a standalone server).
    """


class LxdNetworks:
    """The networks LXD knows about, managed and unmanaged.

    **Commands:**

    ```bash
    lxc network list [<remote>:] --format json [--project <project>]
    ```

    **Returns:**

    ```python
    list[LxdNetwork]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_networks.py``.
    """

    # reemote5-original: there is no other network-visibility fact. The raw
    # JSON array of ``lxc network list --format json`` passed through
    # verbatim, including unmanaged host interfaces (managed=False).
    # lxd.network compares existence, type and config against this fact
    # and refuses to touch an unmanaged interface.

    @classmethod
    def command(cls, remote: str = "", project: str = "default") -> str:
        """Shell listing the networks as JSON."""
        parts = ["network", "list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts, project)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdNetwork]:
        """Parse the JSON network listing into network entries."""
        data = _parse_json(output)
        return cast(list[LxdNetwork], data if data is not None else [])

    @classmethod
    async def process(
        cls, host: Host, remote: str = "", project: str = "default"
    ) -> list[LxdNetwork]:
        networks = getattr(host, "lxd_networks", None)
        if networks is not None:
            # SimulatedHost seam: copied per entry.
            return [cast(LxdNetwork, dict(entry)) for entry in networks]
        stdout = await host.run_shell(cls.command(remote, project))
        return cls.parse(stdout.splitlines())


class LxdOperation(TypedDict, total=False):
    """
    Attributes:
        id: The operation UUID.
        description: The human-readable description, e.g.
            ``Starting instance``.
        status: The operation status (``Running``, ``Success``,
            ``Failure``, ``Cancelled``).
        status_code: The LXD numeric status code (103 Running,
            200 Success, 400 Failure, ...).
        created_at: The creation timestamp (RFC 3339).
        updated_at: The last-update timestamp (RFC 3339).
        resources: The resources the operation touches, keyed by
            resource type (``instances``, ``containers``, ...).
        may_cancel: Whether the operation can be cancelled.
        err: The error message, empty unless the operation failed.
    """


class LxdOperations:
    """The background operations currently registered on the daemon.

    **Commands:**

    ```bash
    lxc operation list [<remote>:] --format json
    ```

    **Returns:**

    ```python
    list[LxdOperation]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_operations.py``.
    """

    # reemote5-original: there is no other operation-visibility fact. The
    # raw JSON array of ``lxc operation list --format json`` passed
    # through verbatim -- including the literal ``class`` key
    # (task/websocket/token), which a TypedDict cannot declare as an
    # attribute because ``class`` is a Python keyword; it survives in
    # the passed-through entries untyped. The list shows in-flight AND
    # recently-finished operations until the daemon prunes them;
    # lxd.operation waits for an id to disappear from this list and
    # cancels through it. Operations are server-wide: no --project.

    @classmethod
    def command(cls, remote: str = "") -> str:
        """Shell listing the background operations as JSON."""
        parts = ["operation", "list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdOperation]:
        """Parse the JSON operation listing into operation entries."""
        data = _parse_json(output)
        return cast(list[LxdOperation], data if data is not None else [])

    @classmethod
    async def process(cls, host: Host, remote: str = "") -> list[LxdOperation]:
        operations = getattr(host, "lxd_operations", None)
        if operations is not None:
            # SimulatedHost seam: copied per entry.
            return [cast(LxdOperation, dict(entry)) for entry in operations]
        stdout = await host.run_shell(cls.command(remote))
        return cls.parse(stdout.splitlines())


class LxdProfile(TypedDict, total=False):
    """
    Attributes:
        name: The profile name, e.g. ``default``.
        description: The profile description, empty when unset.
        config: The profile's configuration keys (limits, security
            policies, ...).
        devices: The profile's devices keyed by device name, each a
            dict with a ``type`` key plus its properties.
        used_by: The instance URLs applying the profile (present on
            some server versions).
    """


class LxdProfiles:
    """The configuration profiles with their config and devices.

    **Commands:**

    ```bash
    lxc profile list [<remote>:] --format json [--project <project>]
    ```

    **Returns:**

    ```python
    list[LxdProfile]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_profiles.py``.
    """

    # reemote5-original: there is no other profile-visibility fact. The raw
    # JSON array of ``lxc profile list --format json`` passed through
    # verbatim; the listing already carries each profile's full config
    # and devices, so lxd.profile needs no second per-profile read.

    @classmethod
    def command(cls, remote: str = "", project: str = "default") -> str:
        """Shell listing the profiles as JSON."""
        parts = ["profile", "list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts, project)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdProfile]:
        """Parse the JSON profile listing into profile entries."""
        data = _parse_json(output)
        return cast(list[LxdProfile], data if data is not None else [])

    @classmethod
    async def process(
        cls, host: Host, remote: str = "", project: str = "default"
    ) -> list[LxdProfile]:
        profiles = getattr(host, "lxd_profiles", None)
        if profiles is not None:
            # SimulatedHost seam: copied per entry.
            return [cast(LxdProfile, dict(entry)) for entry in profiles]
        stdout = await host.run_shell(cls.command(remote, project))
        return cls.parse(stdout.splitlines())


class LxdProject(TypedDict, total=False):
    """
    Attributes:
        name: The project name, e.g. ``default``.
        description: The project description, empty when unset.
        config: The project configuration (``features.images``,
            ``features.profiles``, ``limits.instances``,
            ``restricted``, ...).
        used_by: The resource URLs living in the project.
    """


class LxdProjects:
    """The projects defined on the LXD server.

    **Commands:**

    ```bash
    lxc project list [<remote>:] --format json
    ```

    **Returns:**

    ```python
    list[LxdProject]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_projects.py``.
    """

    # reemote5-original: there is no other project-visibility fact. The raw
    # JSON array of ``lxc project list --format json`` passed through
    # verbatim. Projects are server-wide, so there is no --project flag
    # (the project IS the target); every project-scoped fact and
    # operation in this module addresses one of these names.

    @classmethod
    def command(cls, remote: str = "") -> str:
        """Shell listing the projects as JSON."""
        parts = ["project", "list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdProject]:
        """Parse the JSON project listing into project entries."""
        data = _parse_json(output)
        return cast(list[LxdProject], data if data is not None else [])

    @classmethod
    async def process(cls, host: Host, remote: str = "") -> list[LxdProject]:
        projects = getattr(host, "lxd_projects", None)
        if projects is not None:
            # SimulatedHost seam: copied per entry.
            return [cast(LxdProject, dict(entry)) for entry in projects]
        stdout = await host.run_shell(cls.command(remote))
        return cls.parse(stdout.splitlines())


class LxdProjectConfig:
    """One project's configuration document.

    **Commands:**

    ```bash
    lxc project show <remote>:<project> --format json 2>/dev/null || true
    ```

    **Returns:**

    ```python
    LxdProject | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/lxd/lxd_lxd_project_config.py``.
    """

    # reemote5-original: there is no other per-project configuration fact.
    # ``lxc project show`` returns the same document shape the project
    # listing carries, so the LxdProject entry type serves both facts.
    # The 2>/dev/null || true guard reads a MISSING project as None and
    # masks exit 127. lxd.project compares desired config keys against
    # this fact's config mapping.

    @classmethod
    def command(cls, project: str, remote: str = "") -> str:
        """Shell reading one project's configuration as JSON."""
        parts = [
            "project",
            "show",
            shlex.quote(_remote_name(project, remote)),
            "--format",
            "json",
        ]
        return _lxc_command(parts) + " 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> LxdProject | None:
        """Parse the JSON project document; empty reads as None."""
        data = _parse_json(output)
        return cast(LxdProject, data) if data is not None else None

    @classmethod
    async def process(
        cls, host: Host, project: str, remote: str = ""
    ) -> LxdProject | None:
        configs = getattr(host, "lxd_project_configs", None)
        if configs is not None:
            # SimulatedHost seam: project name -> configuration
            # document; a missing name reads as None.
            entry = configs.get(project)
            return cast(LxdProject, dict(entry)) if entry is not None else None
        stdout = await host.run_shell(cls.command(project, remote))
        return cls.parse(stdout.splitlines())


class LxdServerInfoData(TypedDict, total=False):
    """
    Attributes:
        server: The server implementation name (``lxd`` or ``incus``).
        server_name: The server's host name.
        server_version: The server version string, e.g. ``5.21``.
        server_pid: The daemon's process ID.
        server_clustered: Whether the server is part of a cluster.
        server_lts: Whether the server is an LTS release.
        server_event_mode: The cluster event distribution mode.
        api_version: The REST API version, e.g. ``1.0``.
        api_status: The API stability status (``stable``,
            ``development``, ``deprecated``).
        api_extensions: The supported API extension names -- the
            feature-gating list operations and playbooks check before
            using extension-dependent features.
        auth: The caller's authentication state (``trusted``,
            ``untrusted``).
        auth_user_name: The authenticated user name.
        auth_user_method: The authentication method (``unix``,
            ``tls``).
        public: Whether the server is exposed publicly.
        config: The daemon's configuration keys.
        environment: The server environment (kernel, OS, storage
            driver, LXC version, addresses, ...); dynamic keys keep a
            plain dict.
    """


class LxdServerInfo:
    """The LXD daemon's metadata, environment and API extensions.

    **Commands:**

    ```bash
    lxc info [<remote>:] --format json
    ```

    **Returns:**

    ```python
    LxdServerInfoData
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_server_info.py``.
    """

    # reemote5-original: there is no other daemon-visibility fact. ``lxc
    # info`` with no instance argument renders the REST /1.0 document;
    # the raw JSON object is passed through verbatim (an unreachable or
    # misconfigured daemon fails the command, it does not read as {}).
    # api_extensions is the capability list the discussion's
    # feature-gating note calls for: a deploy can gather this fact and
    # branch on extension membership before yielding an operation. The
    # entry type carries the "Data" suffix because the fact name takes
    # the singular slot (the FirewalldStatusInfo naming precedent).

    @classmethod
    def command(cls, remote: str = "") -> str:
        """Shell reading the daemon metadata as JSON."""
        parts = ["info"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts)

    @classmethod
    def parse(cls, output: Iterable[str]) -> LxdServerInfoData:
        """Parse the JSON server document; empty reads as {}."""
        data = _parse_json(output)
        return cast(LxdServerInfoData, data if data is not None else {})

    @classmethod
    async def process(cls, host: Host, remote: str = "") -> LxdServerInfoData:
        info = getattr(host, "lxd_server_info", None)
        if info is not None:
            # SimulatedHost seam: the server document, copied.
            return cast(LxdServerInfoData, dict(info))
        stdout = await host.run_shell(cls.command(remote))
        return cls.parse(stdout.splitlines())


class LxdServerResourcesData(TypedDict, total=False):
    """
    Attributes:
        cpu: The CPU topology (``sockets`` with per-core details,
            ``total`` core count).
        memory: The memory gauges (``used``, ``total`` in bytes).
        gpu: The graphics cards (``cards``, ``total``).
        network: The network interfaces (``cards``, ``total``).
        storage: The storage devices (``disks``, ``total``).
        system: The system identity (vendor, product, UUID, firmware,
            chassis, motherboard).
        usb: The USB devices (``devices``, ``total``).
        pci: The PCI devices (``devices``, ``total``).
    """


class LxdServerResources:
    """The hardware resources of the LXD host: CPU, memory, GPU, disks.

    **Commands:**

    ```bash
    lxc info [<remote>:] --resources --format json
    ```

    **Returns:**

    ```python
    LxdServerResourcesData
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/lxd/lxd_lxd_server_resources.py``.
    """

    # reemote5-original: there is no other host-hardware fact for LXD.
    # ``lxc info --resources`` renders the REST /1.0/resources document;
    # the raw JSON object is passed through verbatim. The nested
    # hardware records are deeply dynamic (per-socket CPU caches,
    # per-card PCI addresses), so the entry declares the top-level
    # sections only and keeps each a plain dict (the CpuInfo
    # dynamic-key exception).

    @classmethod
    def command(cls, remote: str = "") -> str:
        """Shell reading the host hardware inventory as JSON."""
        parts = ["info"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--resources", "--format", "json"])
        return _lxc_command(parts)

    @classmethod
    def parse(cls, output: Iterable[str]) -> LxdServerResourcesData:
        """Parse the JSON resources document; empty reads as {}."""
        data = _parse_json(output)
        return cast(LxdServerResourcesData, data if data is not None else {})

    @classmethod
    async def process(cls, host: Host, remote: str = "") -> LxdServerResourcesData:
        resources = getattr(host, "lxd_server_resources", None)
        if resources is not None:
            # SimulatedHost seam: the resources document, copied.
            return cast(LxdServerResourcesData, dict(resources))
        stdout = await host.run_shell(cls.command(remote))
        return cls.parse(stdout.splitlines())


class LxdStoragePool(TypedDict, total=False):
    """
    Attributes:
        name: The pool name, e.g. ``default``.
        description: The pool description, empty when unset.
        driver: The storage driver (``dir``, ``zfs``, ``btrfs``,
            ``lvm``, ``ceph``).
        status: The pool status (``Created``, ``Pending``,
            ``Errored``).
        config: The pool configuration (``source``, ``size``,
            driver-specific keys).
        locations: The cluster members the pool exists on
            (``["none"]`` on a standalone server).
    """


class LxdStoragePools:
    """The storage pools configured on the LXD server.

    **Commands:**

    ```bash
    lxc storage list [<remote>:] --format json
    ```

    **Returns:**

    ```python
    list[LxdStoragePool]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_lxd_storage_pools.py``.
    """

    # reemote5-original: there is no other storage-visibility fact. The raw
    # JSON array of ``lxc storage list --format json`` passed through
    # verbatim. Space utilization is deliberately NOT gathered here:
    # the listing carries config (including the requested size) but
    # per-pool usage needs a per-volume sweep; LxdStorageVolumes plus
    # the CLI's ``lxc storage info`` cover that when needed. Pools are
    # server-wide: no --project.

    @classmethod
    def command(cls, remote: str = "") -> str:
        """Shell listing the storage pools as JSON."""
        parts = ["storage", "list"]
        if remote:
            parts.append(f"{remote}:")
        parts.extend(["--format", "json"])
        return _lxc_command(parts)

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdStoragePool]:
        """Parse the JSON pool listing into pool entries."""
        data = _parse_json(output)
        return cast(list[LxdStoragePool], data if data is not None else [])

    @classmethod
    async def process(cls, host: Host, remote: str = "") -> list[LxdStoragePool]:
        pools = getattr(host, "lxd_storage_pools", None)
        if pools is not None:
            # SimulatedHost seam: copied per entry.
            return [cast(LxdStoragePool, dict(entry)) for entry in pools]
        stdout = await host.run_shell(cls.command(remote))
        return cls.parse(stdout.splitlines())


class LxdStorageVolume(TypedDict, total=False):
    """
    Attributes:
        name: The volume name.
        description: The volume description, empty when unset.
        type: The volume type (``custom``, ``container``,
            ``virtual-machine``, ``image``); instance and image volumes
            are managed by LXD, custom volumes by the user.
        content_type: The content type (``filesystem`` or ``block``).
        config: The volume configuration (``size``,
            ``security.shifted``, driver-specific keys).
        location: The cluster member holding the volume.
        created_at: The creation timestamp (RFC 3339).
        used_by: The instance URLs attaching the volume.
    """


class LxdStorageVolumes:
    """The volumes in one storage pool, custom and instance-managed.

    **Commands:**

    ```bash
    lxc storage volume list <remote>:<pool> --format json [--project <project>] 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[LxdStorageVolume]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/lxd/lxd_lxd_storage_volumes.py``.
    """

    # reemote5-original: there is no other volume-visibility fact. The raw
    # JSON array of ``lxc storage volume list <pool> --format json``
    # passed through verbatim; the listing includes instance/image
    # volumes alongside custom ones (the type key discriminates).
    # The 2>/dev/null || true guard reads a MISSING pool as [] and masks
    # exit 127. lxd.storage_volume manages CUSTOM volumes only and
    # compares their names/config against this fact.

    @classmethod
    def command(
        cls, pool: str, remote: str = "", project: str = "default"
    ) -> str:
        """Shell listing one pool's volumes as JSON."""
        parts = [
            "storage",
            "volume",
            "list",
            shlex.quote(_remote_name(pool, remote)),
            "--format",
            "json",
        ]
        return _lxc_command(parts, project) + " 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LxdStorageVolume]:
        """Parse the JSON volume listing into volume entries."""
        data = _parse_json(output)
        return cast(list[LxdStorageVolume], data if data is not None else [])

    @classmethod
    async def process(
        cls, host: Host, pool: str, remote: str = "", project: str = "default"
    ) -> list[LxdStorageVolume]:
        volumes = getattr(host, "lxd_storage_volumes", None)
        if volumes is not None:
            # SimulatedHost seam: pool name -> volume entries, copied
            # per entry; a missing pool reads as [].
            return [
                cast(LxdStorageVolume, dict(entry))
                for entry in volumes.get(pool, [])
            ]
        stdout = await host.run_shell(cls.command(pool, remote, project))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class LxdLaunchCommand(Command):
    """``lxc launch <image> [<remote>:]<instance> ... < /dev/null``.

    Creates AND starts an instance in one step. The trailing
    ``< /dev/null`` matters because launch reads stdin for interactive
    prompts, which a non-interactive SSH shell must never block on.
    """

    image: str
    instance: str
    instance_type: str = "container"
    profiles: tuple[str, ...] = ()
    config: tuple[str, ...] = ()
    devices: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    ephemeral: bool = False
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        parts = [
            "launch",
            shlex.quote(self.image),
            shlex.quote(_remote_name(self.instance, self.remote)),
        ]
        if self.instance_type == "vm":
            parts.append("--vm")
        if self.ephemeral:
            parts.append("--ephemeral")
        for profile in self.profiles:
            parts.extend(["-p", shlex.quote(profile)])
        for entry in self.config:
            parts.extend(["-c", shlex.quote(entry)])
        for name, device_type, props in self.devices:
            parts.extend(["-d", shlex.quote(",".join([name, device_type, *props]))])
        return _lxc_command(parts, self.project) + " < /dev/null"


@dataclass(frozen=True)
class LxdStateCommand(Command):
    """``lxc start/stop/restart/pause/resume [<remote>:]<instance>``.

    One descriptor for the five lifecycle verbs; the CLI blocks until
    the daemon's asynchronous state operation completes.
    """

    instance: str
    action: str
    force: bool = False
    timeout: int | None = None
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        parts = [self.action, shlex.quote(_remote_name(self.instance, self.remote))]
        if self.force:
            parts.append("--force")
        if self.timeout is not None:
            parts.extend(["--timeout", str(self.timeout)])
        return _lxc_command(parts, self.project)


@dataclass(frozen=True)
class LxdDeleteCommand(Command):
    """``lxc delete [<remote>:]<instance>[/snapshot]``.

    Deletes an instance, or one of its snapshots when the name carries
    the ``instance/snapshot`` suffix. A running instance must be stopped
    first (the operations yield a stop before this command).
    """

    instance: str
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        parts = ["delete", shlex.quote(_remote_name(self.instance, self.remote))]
        return _lxc_command(parts, self.project)


async def container(
    ctx: OpContext,
    id: str,
    present: bool = True,
    image: str = "ubuntu:16.04",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add or remove an LXD container.

    Args:
        ctx (OpContext): Execution context for the operation.
        id (str): Name/identifier for the container.
        present (bool, optional): Whether the container should exist.
            Defaults to True.
        image (str, optional): Image to base the container on.
            Defaults to "ubuntu:16.04".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD container commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_container.py``.
    """

    # Create or remove a container by image: the id/present/image
    # options, the LxdContainers fact read, the stop-then-delete
    # sequence for a running container and the ``lxc launch <image>
    # <id> < /dev/null`` create shell (names are ``shlex``-quoted). The
    # unscoped command reads the CLI's current remote/project.
    # Its documented limitation stands for THIS operation: it
    # does not check an existing container's base image -- lxd.instance
    # is the comprehensive successor that reports image drift and
    # manages VMs, profiles, config and devices. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    current_containers = await ctx.facts.get(LxdContainers)
    current = next((c for c in current_containers if c["name"] == id), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"container {id} will be deleted"
            if current.get("status") == "Running":
                yield LxdStateCommand(instance=id, action="stop")
            yield LxdDeleteCommand(instance=id)
        else:
            ctx.reason = f"container {id} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"container {id} will be created"
        yield LxdLaunchCommand(image=image, instance=id)
    else:
        ctx.reason = f"container {id} exists"


@dataclass(frozen=True)
class LxdImageCommand(Command):
    """``lxc image copy <source> <remote>: [--alias <alias>]`` /
    ``lxc image delete [<remote>:]<image>``.

    ``present=True`` caches an image from a source remote (the CLI
    blocks until the download operation completes); ``present=False``
    removes it by fingerprint or alias.
    """

    image: str
    present: bool = True
    source: str = ""
    alias: str = ""
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        if not self.present:
            parts = ["image", "delete", shlex.quote(_remote_name(self.image, self.remote))]
            return _lxc_command(parts, self.project)
        destination = f"{self.remote or 'local'}:"
        parts = ["image", "copy", shlex.quote(self.source), destination]
        if self.alias:
            parts.extend(["--alias", shlex.quote(self.alias)])
        return _lxc_command(parts, self.project)


def _is_fingerprint(reference: str) -> bool:
    """Whether an image reference looks like a full SHA-256 fingerprint."""
    return len(reference) == 64 and all(c in "0123456789abcdef" for c in reference)


async def image(
    ctx: OpContext,
    image: str,
    present: bool = True,
    source: str = "",
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Cache an LXD image locally or remove it from the image store.

    Args:
        ctx (OpContext): Execution context for the operation.
        image (str): The image alias (e.g. ``ubuntu/24.04``) or full
            fingerprint to manage.
        present (bool, optional): Whether the image should be cached.
            Defaults to True.
        source (str, optional): The remote image reference to copy
            from when caching, e.g. ``images:ubuntu/24.04``; required
            when present=True and the image is absent. Defaults to "".
        remote (str, optional): The configured lxc remote whose image
            store to manage; empty means the local daemon. Defaults
            to "".
        project (str, optional): The project whose image store to
            manage. Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD image commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_image.py``.
    """

    # reemote5-original (there is no other image operation). Idempotent
    # against LxdImages: an image already present by fingerprint OR
    # alias is a no-op; an absent one copies from ``source`` (the CLI
    # waits on the asynchronous download natively), adding --alias
    # when the reference is an alias rather than a fingerprint.
    # present=False deletes by the given reference (the CLI accepts
    # both fingerprints and aliases). Image imports from
    # controller-side files are deliberately out of scope: reemote5
    # transfers take IO streams, so spool a tarball with files.put and
    # run ``lxc image import`` through an action when needed.
    # ``**kwargs`` absorbs any further global arguments.
    images = await ctx.facts.get(LxdImages, remote=remote, project=project)
    current = next(
        (
            entry
            for entry in images
            if entry.get("fingerprint") == image
            or any(alias.get("name") == image for alias in entry.get("aliases") or [])
        ),
        None,
    )

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"lxd image {image} will be deleted"
            yield LxdImageCommand(
                image=image, present=False, remote=remote, project=project
            )
        else:
            ctx.reason = f"lxd image {image} does not exist"
        return

    if current is not None:
        ctx.reason = f"lxd image {image} already cached"
        return

    if not source:
        raise ValueError(
            f"lxd.image requires source to cache the absent image {image!r}"
        )
    ctx.changed = True
    ctx.reason = f"lxd image {image} will be cached from {source}"
    yield LxdImageCommand(
        image=image,
        source=source,
        alias="" if _is_fingerprint(image) else image,
        remote=remote,
        project=project,
    )


@dataclass(frozen=True)
class LxdImageAliasCommand(Command):
    """``lxc image alias create [<remote>:]<alias> <fingerprint>`` /
    ``lxc image alias delete [<remote>:]<alias>``.

    ``present=True`` points the alias at a fingerprint; ``present=False``
    removes the alias (never the image it targets).
    """

    alias: str
    fingerprint: str = ""
    present: bool = True
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        target = shlex.quote(_remote_name(self.alias, self.remote))
        if not self.present:
            parts = ["image", "alias", "delete", target]
        else:
            parts = ["image", "alias", "create", target, shlex.quote(self.fingerprint)]
        return _lxc_command(parts, self.project)


async def image_alias(
    ctx: OpContext,
    alias: str,
    fingerprint: str = "",
    present: bool = True,
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Point an LXD image alias at a fingerprint, or remove the alias.

    Args:
        ctx (OpContext): Execution context for the operation.
        alias (str): The alias name to manage, e.g. ``ubuntu/24.04``.
        fingerprint (str, optional): The image fingerprint the alias
            should target; required when present=True. Defaults to "".
        present (bool, optional): Whether the alias should exist.
            Defaults to True.
        remote (str, optional): The configured lxc remote whose alias
            to manage; empty means the local daemon. Defaults to "".
        project (str, optional): The project whose alias to manage.
            Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD image alias commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_image_alias.py``.
    """

    # reemote5-original (there is no other alias operation). Idempotent
    # against LxdImageAliases: an alias already targeting the desired
    # fingerprint is a no-op; one targeting a DIFFERENT fingerprint is
    # deleted and recreated (the CLI has no alias-repoint verb, and
    # create fails on an existing name). present=False removes the
    # alias only -- the image itself stays cached. ``**kwargs`` absorbs
    # any further global arguments.
    if present and not fingerprint:
        raise ValueError(
            f"lxd.image_alias requires fingerprint when present=True ({alias!r})"
        )
    aliases = await ctx.facts.get(LxdImageAliases, remote=remote, project=project)
    current = next((entry for entry in aliases if entry.get("name") == alias), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"lxd image alias {alias} will be deleted"
            yield LxdImageAliasCommand(
                alias=alias, present=False, remote=remote, project=project
            )
        else:
            ctx.reason = f"lxd image alias {alias} does not exist"
        return

    if current is not None and current.get("target") == fingerprint:
        ctx.reason = f"lxd image alias {alias} already targets {fingerprint}"
        return

    ctx.changed = True
    if current is not None:
        ctx.reason = (
            f"lxd image alias {alias} will be repointed to {fingerprint}"
        )
        yield LxdImageAliasCommand(
            alias=alias, present=False, remote=remote, project=project
        )
    else:
        ctx.reason = f"lxd image alias {alias} will be created"
    yield LxdImageAliasCommand(
        alias=alias, fingerprint=fingerprint, remote=remote, project=project
    )


@dataclass(frozen=True)
class LxdSetCommand(Command):
    """``lxc config/profile/project/network/storage set|unset <target> <key> [value]``.

    One descriptor for the five resource ``set`` verbs (they share the
    same argument shape); ``value=None`` renders ``unset``. The
    ``resource`` field selects the verb family and the SimulatedHost
    arm's target store.
    """

    resource: str
    target: str
    key: str
    value: str | None = None
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        verb = "set" if self.value is not None else "unset"
        parts = [
            self.resource,
            verb,
            shlex.quote(_remote_name(self.target, self.remote)),
            shlex.quote(self.key),
        ]
        if self.value is not None:
            parts.append(shlex.quote(self.value))
        return _lxc_command(parts, self.project)


@dataclass(frozen=True)
class LxdDeviceCommand(Command):
    """``lxc config/profile device add|remove <target> <device> [type props...]``.

    ``present=True`` attaches a device (``add`` with its type and
    ``key=value`` properties); ``present=False`` detaches it. A device
    whose definition drifted is removed and re-added -- the CLI's
    ``override`` only covers profile-inherited devices.
    """

    resource: str
    target: str
    device: str
    present: bool = True
    device_type: str = ""
    props: tuple[str, ...] = ()
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        target = shlex.quote(_remote_name(self.target, self.remote))
        if not self.present:
            parts = [self.resource, "device", "remove", target, shlex.quote(self.device)]
        else:
            parts = [
                self.resource,
                "device",
                "add",
                target,
                shlex.quote(self.device),
                shlex.quote(self.device_type),
                *(shlex.quote(prop) for prop in self.props),
            ]
        return _lxc_command(parts, self.project)


@dataclass(frozen=True)
class LxdProfileAssignCommand(Command):
    """``lxc profile assign [<remote>:]<instance> <profile,profile,...>``.

    Replaces the instance's profile list wholesale, in the given order
    (profile application order matters in LXD).
    """

    instance: str
    profiles: tuple[str, ...] = ()
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        parts = [
            "profile",
            "assign",
            shlex.quote(_remote_name(self.instance, self.remote)),
            shlex.quote(",".join(self.profiles)),
        ]
        return _lxc_command(parts, self.project)


async def instance(
    ctx: OpContext,
    instance: str,
    present: bool = True,
    image: str = "images:ubuntu/24.04",
    instance_type: str = "container",
    profiles: tuple[str, ...] = (),
    config: tuple[str, ...] = (),
    devices: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    ephemeral: bool = False,
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Ensure an LXD container or VM exists with the desired configuration.

    Args:
        ctx (OpContext): Execution context for the operation.
        instance (str): The instance name to manage.
        present (bool, optional): Whether the instance should exist.
            Defaults to True.
        image (str, optional): The image reference to launch from,
            e.g. ``images:ubuntu/24.04`` or a fingerprint. Defaults to
            "images:ubuntu/24.04".
        instance_type (str, optional): ``container`` or ``vm``.
            Defaults to "container".
        profiles (tuple[str, ...], optional): The profiles to apply,
            in order; empty leaves the project default. Defaults to ().
        config (tuple[str, ...], optional): Configuration entries as
            ``"key=value"`` strings, e.g. ``("limits.cpu=2",)``.
            Defaults to ().
        devices (tuple[tuple[str, str, tuple[str, ...]], ...], optional):
            Devices as ``(name, type, ("key=value", ...))`` triples,
            e.g. ``(("eth0", "nic", ("network=lxdbr0",)),)``.
            Defaults to ().
        ephemeral (bool, optional): Whether the instance is deleted
            when it stops (creation-only). Defaults to False.
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        project (str, optional): The project to manage the instance
            in. Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD instance commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_instance.py``.
    """

    # reemote5-original successor to ``lxd.container`` (which
    # stays as the simple port). Idempotent against LxdInstances:
    # absent -> one launch carrying type/profiles/config/devices/
    # ephemeral; existing -> NON-DESTRUCTIVE reconciliation of profiles
    # (assign), config (set per drifted key) and devices (remove+add
    # per drifted device). Image-drift decision (developer-approved):
    # the desired image reference is resolved to a fingerprint through
    # LxdImages and compared against the instance's
    # config["volatile.base_image"]; a mismatch is REPORTED in the
    # reason, never recreated -- destructive recreation stays an
    # explicit present=False choice. An unresolvable reference (image
    # not cached locally) skips the check silently. A type mismatch
    # raises OperationError (LXD cannot convert container<->VM, the
    # postgresql.tablespace immutable-location precedent); ephemeral
    # is creation-only. present=False stops a running instance then
    # deletes it, the documented sequence. ``**kwargs`` absorbs any
    # global arguments.
    if instance_type not in ("container", "vm"):
        raise ValueError(
            f"instance_type must be 'container' or 'vm', got {instance_type!r}"
        )
    pairs = _config_pairs(config)
    for name, _device_type, props in devices:
        _device_props(props)

    instances = await ctx.facts.get(LxdInstances, remote=remote, project=project)
    current = next((entry for entry in instances if entry.get("name") == instance), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"lxd instance {instance} will be deleted"
            if current.get("status") == "Running":
                yield LxdStateCommand(
                    instance=instance, action="stop", remote=remote, project=project
                )
            yield LxdDeleteCommand(instance=instance, remote=remote, project=project)
        else:
            ctx.reason = f"lxd instance {instance} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"lxd instance {instance} will be created"
        yield LxdLaunchCommand(
            image=image,
            instance=instance,
            instance_type=instance_type,
            profiles=profiles,
            config=config,
            devices=devices,
            ephemeral=ephemeral,
            remote=remote,
            project=project,
        )
        return

    current_type = current.get("type")
    desired_type = "virtual-machine" if instance_type == "vm" else "container"
    if current_type and current_type != desired_type:
        raise OperationError(
            f"lxd instance {instance} is a {current_type}, cannot become "
            f"{desired_type}; delete it first"
        )

    reasons: list[str] = []
    changed = False

    if profiles and tuple(current.get("profiles") or []) != profiles:
        changed = True
        reasons.append("profiles will be assigned")
        yield LxdProfileAssignCommand(
            instance=instance, profiles=profiles, remote=remote, project=project
        )

    current_config = current.get("config") or {}
    drifted_keys = [key for key, value in pairs if current_config.get(key) != value]
    for key, value in pairs:
        if key in drifted_keys:
            changed = True
            yield LxdSetCommand(
                resource="config",
                target=instance,
                key=key,
                value=value,
                remote=remote,
                project=project,
            )
    if drifted_keys:
        reasons.append(f"config keys will be set: {', '.join(sorted(drifted_keys))}")

    current_devices = current.get("devices") or {}
    drifted_devices = []
    for name, device_type, props in devices:
        desired = {"type": device_type, **_device_props(props)}
        if current_devices.get(name) != desired:
            drifted_devices.append(name)
            changed = True
            if name in current_devices:
                yield LxdDeviceCommand(
                    resource="config",
                    target=instance,
                    device=name,
                    present=False,
                    remote=remote,
                    project=project,
                )
            yield LxdDeviceCommand(
                resource="config",
                target=instance,
                device=name,
                device_type=device_type,
                props=props,
                remote=remote,
                project=project,
            )
    if drifted_devices:
        reasons.append(
            f"devices will be attached: {', '.join(sorted(drifted_devices))}"
        )

    # Image-drift report (never a recreation): resolve the desired
    # reference through the image store and compare fingerprints.
    images = await ctx.facts.get(LxdImages, remote=remote, project=project)
    reference = image.partition(":")[2] if ":" in image else image
    matched = next(
        (
            entry
            for entry in images
            if entry.get("fingerprint") == reference
            or any(
                alias.get("name") == reference for alias in entry.get("aliases") or []
            )
        ),
        None,
    )
    if matched is not None:
        base = (current.get("config") or {}).get("volatile.base_image")
        if base and base != matched.get("fingerprint"):
            reasons.append(
                f"base image differs from {image} (not recreated)"
            )

    ctx.changed = changed
    if changed:
        ctx.reason = f"lxd instance {instance} will be updated ({'; '.join(reasons)})"
    elif reasons:
        ctx.reason = f"lxd instance {instance} exists ({'; '.join(reasons)})"
    else:
        ctx.reason = f"lxd instance {instance} already exists as configured"


async def instance_config(
    ctx: OpContext,
    instance: str,
    config: tuple[str, ...] = (),
    devices: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    profiles: tuple[str, ...] | None = None,
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Update an existing LXD instance's config, devices and profiles.

    Args:
        ctx (OpContext): Execution context for the operation.
        instance (str): The instance to update (must already exist).
        config (tuple[str, ...], optional): Configuration entries as
            ``"key=value"`` strings, set only where they differ.
            Defaults to ().
        devices (tuple[tuple[str, str, tuple[str, ...]], ...], optional):
            Devices as ``(name, type, ("key=value", ...))`` triples,
            re-attached only where they differ. Defaults to ().
        profiles (tuple[str, ...] | None, optional): The profile list
            to assign in order; None leaves the current list alone.
            Defaults to None.
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        project (str, optional): The project the instance lives in.
            Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD instance configuration commands.

    **Raises:**

    ```python
    HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_instance_config.py``.
    """

    # reemote5-original (there is no other configuration operation). The
    # dedicated reconciliation half of lxd.instance for an instance
    # that must already exist: reads LxdInstanceConfig and compares
    # desired keys against the instance's OWN config (an inherited
    # profile value is not an own key, so setting it IS a change);
    # drifted devices are removed and re-added; profiles are assigned
    # wholesale when the tuple differs. A missing instance raises
    # OperationError in prepare (create it with lxd.instance first).
    # Config keys are only SET, never unset -- pass lxd.instance or the
    # CLI for removals. ``**kwargs`` absorbs any further global arguments.
    pairs = _config_pairs(config)
    for name, _device_type, props in devices:
        _device_props(props)

    current = await ctx.facts.get(
        LxdInstanceConfig, instance, remote=remote, project=project
    )
    if current is None:
        raise OperationError(f"lxd instance {instance} does not exist")

    reasons: list[str] = []
    changed = False

    if profiles is not None and tuple(current.get("profiles") or []) != profiles:
        changed = True
        reasons.append("profiles will be assigned")
        yield LxdProfileAssignCommand(
            instance=instance, profiles=profiles, remote=remote, project=project
        )

    current_config = current.get("config") or {}
    drifted_keys = [key for key, value in pairs if current_config.get(key) != value]
    for key, value in pairs:
        if key in drifted_keys:
            changed = True
            yield LxdSetCommand(
                resource="config",
                target=instance,
                key=key,
                value=value,
                remote=remote,
                project=project,
            )
    if drifted_keys:
        reasons.append(f"config keys will be set: {', '.join(sorted(drifted_keys))}")

    current_devices = current.get("devices") or {}
    drifted_devices = []
    for name, device_type, props in devices:
        desired = {"type": device_type, **_device_props(props)}
        if current_devices.get(name) != desired:
            drifted_devices.append(name)
            changed = True
            if name in current_devices:
                yield LxdDeviceCommand(
                    resource="config",
                    target=instance,
                    device=name,
                    present=False,
                    remote=remote,
                    project=project,
                )
            yield LxdDeviceCommand(
                resource="config",
                target=instance,
                device=name,
                device_type=device_type,
                props=props,
                remote=remote,
                project=project,
            )
    if drifted_devices:
        reasons.append(
            f"devices will be attached: {', '.join(sorted(drifted_devices))}"
        )

    ctx.changed = changed
    if changed:
        ctx.reason = (
            f"lxd instance {instance} will be reconfigured ({'; '.join(reasons)})"
        )
    else:
        ctx.reason = f"lxd instance {instance} configuration already up to date"


# The instance_state option -> (CLI verb, target status) mapping. The
# restarted/unfrozen actions have no comparable target status: they are
# stateless and always yield, like any non-idempotent option.
_STATE_ACTIONS = {
    "started": ("start", "Running"),
    "stopped": ("stop", "Stopped"),
    "frozen": ("pause", "Frozen"),
    "restarted": ("restart", None),
    "unfrozen": ("resume", None),
}


async def instance_state(
    ctx: OpContext,
    instance: str,
    state: str = "started",
    force: bool = False,
    timeout: int | None = None,
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Drive an LXD instance's lifecycle state.

    Args:
        ctx (OpContext): Execution context for the operation.
        instance (str): The instance to manage (must already exist).
        state (str, optional): The desired state: ``started``,
            ``stopped``, ``frozen``, ``restarted`` or ``unfrozen``.
            Defaults to "started".
        force (bool, optional): Force the action (kill on stop,
            unfreeze on start). Defaults to False.
        timeout (int | None, optional): Seconds to wait for a clean
            shutdown before killing (stop only); None uses the daemon
            default. Defaults to None.
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        project (str, optional): The project the instance lives in.
            Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD instance state commands.

    **Raises:**

    ```python
    HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_instance_state.py``.
    """

    # reemote5-original (there is no other lifecycle operation). Idempotent
    # against LxdInstanceState for started/stopped/frozen: the CLI verb
    # (start/stop/pause) is yielded only when the status differs from
    # the target (Running/Stopped/Frozen). restarted and unfrozen are
    # STATELESS and always yield -- a restart is an action, not a
    # desired state, and a Running instance may still be frozen-state
    # ambiguous, matching the non-idempotent-option convention.
    # The CLI blocks on the daemon's asynchronous state operation, so
    # no explicit operation-wait is needed. A missing instance raises
    # OperationError in prepare. ``**kwargs`` absorbs any further global
    # arguments.
    if state not in _STATE_ACTIONS:
        raise ValueError(
            f"state must be one of {', '.join(sorted(_STATE_ACTIONS))}, got {state!r}"
        )
    action, target_status = _STATE_ACTIONS[state]

    if target_status is None:
        ctx.changed = True
        ctx.reason = f"lxd instance {instance} will be {state}"
        yield LxdStateCommand(
            instance=instance,
            action=action,
            force=force,
            timeout=timeout,
            remote=remote,
            project=project,
        )
        return

    current = await ctx.facts.get(
        LxdInstanceState, instance, remote=remote, project=project
    )
    if current is None:
        raise OperationError(f"lxd instance {instance} does not exist")
    if current.get("status") == target_status:
        ctx.reason = f"lxd instance {instance} already {state}"
        return

    ctx.changed = True
    ctx.reason = f"lxd instance {instance} will be {state}"
    yield LxdStateCommand(
        instance=instance,
        action=action,
        force=force,
        timeout=timeout,
        remote=remote,
        project=project,
    )


@dataclass(frozen=True)
class LxdCopyCommand(Command):
    """``lxc copy [<remote>:]<instance> [<dest_remote>:]<dest> [flags]``.

    Copies an instance (with its snapshots by default;
    ``instance_only=True`` renders ``--instance-only``). The CLI blocks
    until the daemon's asynchronous copy operation completes.
    """

    instance: str
    dest_instance: str
    dest_remote: str = ""
    instance_only: bool = False
    stateless: bool = False
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        parts = [
            "copy",
            shlex.quote(_remote_name(self.instance, self.remote)),
            shlex.quote(_remote_name(self.dest_instance, self.dest_remote)),
        ]
        if self.instance_only:
            parts.append("--instance-only")
        if self.stateless:
            parts.append("--stateless")
        return _lxc_command(parts, self.project)


async def instance_copy(
    ctx: OpContext,
    instance: str,
    dest_instance: str,
    dest_remote: str = "",
    instance_only: bool = False,
    stateless: bool = False,
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Copy an LXD instance to a new name, locally or to another remote.

    Args:
        ctx (OpContext): Execution context for the operation.
        instance (str): The source instance name (must already exist).
        dest_instance (str): The destination instance name.
        dest_remote (str, optional): The configured lxc remote to copy
            to; empty means the same daemon as the source. Defaults
            to "".
        instance_only (bool, optional): Copy without the snapshots.
            Defaults to False.
        stateless (bool, optional): Copy without the source's runtime
            state. Defaults to False.
        remote (str, optional): The configured lxc remote holding the
            source; empty means the local daemon. Defaults to "".
        project (str, optional): The project the source lives in.
            Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD copy commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_instance_copy.py``.
    """

    # reemote5-original (there is no other copy operation). Idempotent on
    # the DESTINATION's existence when source and destination address
    # the same daemon (dest_remote empty or equal to remote): the
    # destination is looked up in LxdInstances and an existing one is a
    # no-op (no refresh semantics -- delete it first to re-copy). A
    # CROSS-REMOTE copy cannot read the destination's fact through this
    # host's CLI scope, so it is STATELESS and always yields; the CLI
    # itself fails if the destination name is taken. The source's
    # existence is left to the CLI (a missing source fails the copy at
    # apply time). ``**kwargs`` absorbs any further global arguments.
    if not dest_instance:
        raise ValueError("lxd.instance_copy requires a dest_instance name")

    same_daemon = (dest_remote or "local") == (remote or "local")
    if same_daemon:
        instances = await ctx.facts.get(LxdInstances, remote=remote, project=project)
        if any(entry.get("name") == dest_instance for entry in instances):
            ctx.reason = f"lxd instance {dest_instance} already exists"
            return

    ctx.changed = True
    ctx.reason = f"lxd instance {instance} will be copied to {dest_instance}"
    yield LxdCopyCommand(
        instance=instance,
        dest_instance=dest_instance,
        dest_remote=dest_remote,
        instance_only=instance_only,
        stateless=stateless,
        remote=remote,
        project=project,
    )


@dataclass(frozen=True)
class LxdSnapshotCommand(Command):
    """``lxc snapshot [<remote>:]<instance> <snapshot> [--stateful]``.

    Creates a snapshot; ``stateful=True`` includes the runtime state.
    Deletion goes through ``LxdDeleteCommand`` with the
    ``instance/snapshot`` name form.
    """

    instance: str
    snapshot: str
    stateful: bool = False
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        parts = [
            "snapshot",
            shlex.quote(_remote_name(self.instance, self.remote)),
            shlex.quote(self.snapshot),
        ]
        if self.stateful:
            parts.append("--stateful")
        return _lxc_command(parts, self.project)


@dataclass(frozen=True)
class LxdRestoreCommand(Command):
    """``lxc restore [<remote>:]<instance> <snapshot> [--stateful]``.

    Restores an instance to a snapshot. A non-stateful restore requires
    the instance to be stopped first (lxd.instance_state).
    """

    instance: str
    snapshot: str
    stateful: bool = False
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        parts = [
            "restore",
            shlex.quote(_remote_name(self.instance, self.remote)),
            shlex.quote(self.snapshot),
        ]
        if self.stateful:
            parts.append("--stateful")
        return _lxc_command(parts, self.project)


async def instance_snapshot(
    ctx: OpContext,
    instance: str,
    snapshot: str,
    present: bool = True,
    stateful: bool = False,
    restore: bool = False,
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, delete or restore an LXD instance snapshot.

    Args:
        ctx (OpContext): Execution context for the operation.
        instance (str): The instance to snapshot.
        snapshot (str): The snapshot name.
        present (bool, optional): Whether the snapshot should exist.
            Defaults to True.
        stateful (bool, optional): Include the runtime state in the
            snapshot (or restore it). Defaults to False.
        restore (bool, optional): Restore the instance to this
            snapshot instead of managing its existence; always runs
            (a restore is an action, not a desired state). Defaults
            to False.
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        project (str, optional): The project the instance lives in.
            Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD snapshot commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/lxd/lxd_instance_snapshot.py``.
    """

    # reemote5-original (there is no other snapshot operation). Idempotent
    # against LxdInstanceSnapshots: an absent snapshot is created
    # (lxc snapshot, --stateful on request), an unwanted one deleted
    # through the instance/snapshot name form. restore=True is
    # STATELESS and always yields lxc restore -- stop the instance
    # first for a non-stateful restore (the CLI enforces it at apply
    # time); combining restore=True with present=False raises
    # ValueError in prepare. ``**kwargs`` absorbs any further global
    # arguments.
    if restore and not present:
        raise ValueError("lxd.instance_snapshot cannot restore with present=False")

    if restore:
        ctx.changed = True
        ctx.reason = f"lxd instance {instance} will be restored to {snapshot}"
        yield LxdRestoreCommand(
            instance=instance,
            snapshot=snapshot,
            stateful=stateful,
            remote=remote,
            project=project,
        )
        return

    snapshots = await ctx.facts.get(
        LxdInstanceSnapshots, instance, remote=remote, project=project
    )
    exists = any(entry.get("name") == snapshot for entry in snapshots)

    if not present:
        if exists:
            ctx.changed = True
            ctx.reason = f"lxd snapshot {instance}/{snapshot} will be deleted"
            yield LxdDeleteCommand(
                instance=f"{instance}/{snapshot}", remote=remote, project=project
            )
        else:
            ctx.reason = f"lxd snapshot {instance}/{snapshot} does not exist"
        return

    if exists:
        ctx.reason = f"lxd snapshot {instance}/{snapshot} already exists"
        return

    ctx.changed = True
    ctx.reason = f"lxd snapshot {instance}/{snapshot} will be created"
    yield LxdSnapshotCommand(
        instance=instance,
        snapshot=snapshot,
        stateful=stateful,
        remote=remote,
        project=project,
    )


@dataclass(frozen=True)
class LxdNetworkCommand(Command):
    """``lxc network create <name> --type <type> [key=value ...]`` /
    ``lxc network delete <name>``.

    Creates a managed network with its type and creation-time config,
    or deletes it. Config drift on an existing network goes through
    ``LxdSetCommand`` with ``resource="network"``.
    """

    network: str
    present: bool = True
    network_type: str = "bridge"
    config: tuple[str, ...] = ()
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        target = shlex.quote(_remote_name(self.network, self.remote))
        if not self.present:
            parts = ["network", "delete", target]
        else:
            parts = ["network", "create", target, "--type", shlex.quote(self.network_type)]
            parts.extend(shlex.quote(entry) for entry in self.config)
        return _lxc_command(parts, self.project)


async def network(
    ctx: OpContext,
    network: str,
    present: bool = True,
    network_type: str = "bridge",
    config: tuple[str, ...] = (),
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, configure or delete an LXD managed network.

    Args:
        ctx (OpContext): Execution context for the operation.
        network (str): The network name to manage, e.g. ``lxdbr0``.
        present (bool, optional): Whether the network should exist.
            Defaults to True.
        network_type (str, optional): The network type (``bridge``,
            ``macvlan``, ``ovn``, ``physical``, ``sriov``); immutable
            after creation. Defaults to "bridge".
        config (tuple[str, ...], optional): Configuration entries as
            ``"key=value"`` strings, e.g. ``("ipv4.nat=true",)``.
            Defaults to ().
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        project (str, optional): The project to manage the network
            in. Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD network commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_network.py``.
    """

    # reemote5-original (there is no other network operation). Idempotent
    # against LxdNetworks: absent -> create with --type and the
    # creation-time config; existing -> set per drifted config key.
    # A type mismatch raises OperationError (LXD cannot change a
    # network's type, the postgresql.tablespace precedent), and an
    # UNMANAGED interface (managed=False, a host NIC LXD merely knows
    # about) raises OperationError rather than being adopted or
    # deleted. present=False deletes the managed network.
    # ``**kwargs`` absorbs any further global arguments.
    pairs = _config_pairs(config)
    networks = await ctx.facts.get(LxdNetworks, remote=remote, project=project)
    current = next((entry for entry in networks if entry.get("name") == network), None)

    if not present:
        if current is not None:
            if not current.get("managed", True):
                raise OperationError(
                    f"lxd network {network} is unmanaged; refusing to delete it"
                )
            ctx.changed = True
            ctx.reason = f"lxd network {network} will be deleted"
            yield LxdNetworkCommand(
                network=network, present=False, remote=remote, project=project
            )
        else:
            ctx.reason = f"lxd network {network} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"lxd network {network} will be created"
        yield LxdNetworkCommand(
            network=network,
            network_type=network_type,
            config=config,
            remote=remote,
            project=project,
        )
        return

    if not current.get("managed", True):
        raise OperationError(
            f"lxd network {network} is unmanaged; refusing to configure it"
        )
    current_type = current.get("type")
    if current_type and current_type != network_type:
        raise OperationError(
            f"lxd network {network} is a {current_type}, cannot become "
            f"{network_type}; delete it first"
        )

    current_config = current.get("config") or {}
    drifted_keys = [key for key, value in pairs if current_config.get(key) != value]
    if drifted_keys:
        ctx.changed = True
        ctx.reason = (
            f"lxd network {network} config will be set: "
            f"{', '.join(sorted(drifted_keys))}"
        )
        for key, value in pairs:
            if key in drifted_keys:
                yield LxdSetCommand(
                    resource="network",
                    target=network,
                    key=key,
                    value=value,
                    remote=remote,
                    project=project,
                )
    else:
        ctx.reason = f"lxd network {network} already exists as configured"


@dataclass(frozen=True)
class LxdOperationCancelCommand(Command):
    """``lxc operation delete [<remote>:]<id>``: cancel a background operation."""

    operation_id: str
    remote: str = ""

    def shell(self) -> str:
        parts = [
            "operation",
            "delete",
            shlex.quote(_remote_name(self.operation_id, self.remote)),
        ]
        return _lxc_command(parts)


@dataclass(frozen=True)
class LxdOperationWaitCommand(WaitCommand):
    """Poll ``LxdOperations`` until an operation id is no longer registered.

    The lxd-local wait descriptor (cross-namespace imports are
    forbidden, so server.WaitForFactCommand is not reused): the daemon
    prunes finished operations from the list, so disappearance is the
    completion signal. Like every wait it is unprivileged and has no
    ``shell()`` -- ``__str__`` is the loggable form.
    """

    operation_id: str
    timeout: float = 300.0
    interval: float = 5.0
    remote: str = ""

    def __str__(self) -> str:
        return (
            f"wait for lxd operation {self.operation_id} to finish "
            f"every {self.interval}s up to {self.timeout}s"
        )

    async def wait(self, host: Host) -> str:
        """Poll the operation list until the id disappears or time out.

        Reads ``LxdOperations.process`` directly (never a FactCache,
        which would pin the first value); the deadline is checked after
        each poll, so an already-finished operation succeeds at once.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        polls = 0
        while True:
            polls += 1
            operations = await LxdOperations.process(host, remote=self.remote)
            if all(entry.get("id") != self.operation_id for entry in operations):
                return f"operation {self.operation_id} finished after {polls} poll(s)"
            if loop.time() >= deadline:
                raise WaitTimeoutError(
                    f"host {host.name}: {self} -- operation still registered "
                    f"after {self.timeout}s"
                )
            await asyncio.sleep(self.interval)


async def operation(
    ctx: OpContext,
    operation_id: str,
    cancel: bool = False,
    wait: bool = True,
    timeout: float = 300.0,
    interval: float = 5.0,
    remote: str = "",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Wait on or cancel an LXD background operation by id.

    Args:
        ctx (OpContext): Execution context for the operation.
        operation_id (str): The background operation's UUID.
        cancel (bool, optional): Cancel the operation if it is still
            registered. Defaults to False.
        wait (bool, optional): Wait for the operation to finish (after
            cancelling, when both are set). Defaults to True.
        timeout (float, optional): Seconds to keep polling before
            raising ``WaitTimeoutError``. Defaults to 300.0.
        interval (float, optional): Seconds between polls. Defaults
            to 5.0.
        remote (str, optional): The configured lxc remote holding the
            operation; empty means the local daemon. Defaults to "".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD operation commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError, WaitTimeoutError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_operation.py``.
    """

    # reemote5-original (there is no other operation primitive). The
    # lower-level handle the discussion calls for: long-running
    # mutations started out of band (image downloads, migrations) can
    # be waited on or cancelled explicitly. The wait is STATELESS and
    # always yields (a wait is not idempotent, the server.wait_service
    # precedent) -- an already-finished operation succeeds on the first
    # poll at apply time. The cancel half IS idempotent against
    # LxdOperations: a registered id yields lxc operation delete, an
    # already-pruned one is a no-op. cancel=False with wait=False
    # raises ValueError (nothing to do). ``**kwargs`` absorbs any
    # global arguments.
    if not cancel and not wait:
        raise ValueError("lxd.operation requires cancel=True or wait=True")

    reasons: list[str] = []
    if cancel:
        operations = await ctx.facts.get(LxdOperations, remote=remote)
        if any(entry.get("id") == operation_id for entry in operations):
            ctx.changed = True
            reasons.append(f"operation {operation_id} will be cancelled")
            yield LxdOperationCancelCommand(operation_id=operation_id, remote=remote)
        else:
            reasons.append(f"operation {operation_id} is no longer registered")

    if wait:
        ctx.changed = True
        reasons.append(f"wait for operation {operation_id} to finish")
        yield LxdOperationWaitCommand(
            operation_id=operation_id,
            timeout=timeout,
            interval=interval,
            remote=remote,
        )

    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class LxdProfileCommand(Command):
    """``lxc profile create [<remote>:]<name>`` / ``lxc profile delete ...``.

    Creates an empty profile or deletes one. Config and devices are
    attached afterwards through ``LxdSetCommand`` (``resource="profile"``)
    and ``LxdDeviceCommand`` -- the create verb takes no config flags.
    """

    profile: str
    present: bool = True
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        verb = "create" if self.present else "delete"
        parts = ["profile", verb, shlex.quote(_remote_name(self.profile, self.remote))]
        return _lxc_command(parts, self.project)


async def profile(
    ctx: OpContext,
    profile: str,
    present: bool = True,
    config: tuple[str, ...] = (),
    devices: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, update or delete an LXD configuration profile.

    Args:
        ctx (OpContext): Execution context for the operation.
        profile (str): The profile name to manage.
        present (bool, optional): Whether the profile should exist.
            Defaults to True.
        config (tuple[str, ...], optional): Configuration entries as
            ``"key=value"`` strings, e.g. ``("limits.cpu=2",)``.
            Defaults to ().
        devices (tuple[tuple[str, str, tuple[str, ...]], ...], optional):
            Devices as ``(name, type, ("key=value", ...))`` triples.
            Defaults to ().
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        project (str, optional): The project to manage the profile
            in. Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD profile commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_profile.py``.
    """

    # reemote5-original (there is no other profile operation). Idempotent
    # against LxdProfiles (the listing already carries each profile's
    # config and devices): absent -> create, then set every requested
    # config key and attach every requested device (the create verb
    # takes no config flags); existing -> set/attach only the drifted
    # entries, a drifted device being removed and re-added. Keys and
    # devices are only ADDED/updated, never removed -- the operation
    # reconciles toward the requested subset, it does not prune.
    # present=False deletes the profile (the CLI fails while instances
    # still apply it). ``**kwargs`` absorbs any further global arguments.
    pairs = _config_pairs(config)
    for name, _device_type, props in devices:
        _device_props(props)

    profiles = await ctx.facts.get(LxdProfiles, remote=remote, project=project)
    current = next((entry for entry in profiles if entry.get("name") == profile), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"lxd profile {profile} will be deleted"
            yield LxdProfileCommand(
                profile=profile, present=False, remote=remote, project=project
            )
        else:
            ctx.reason = f"lxd profile {profile} does not exist"
        return

    reasons: list[str] = []
    changed = False

    if current is None:
        changed = True
        reasons.append("profile will be created")
        yield LxdProfileCommand(profile=profile, remote=remote, project=project)

    current_config = (current or {}).get("config") or {}
    drifted_keys = [key for key, value in pairs if current_config.get(key) != value]
    for key, value in pairs:
        if key in drifted_keys:
            changed = True
            yield LxdSetCommand(
                resource="profile",
                target=profile,
                key=key,
                value=value,
                remote=remote,
                project=project,
            )
    if drifted_keys:
        reasons.append(f"config keys will be set: {', '.join(sorted(drifted_keys))}")

    current_devices = (current or {}).get("devices") or {}
    drifted_devices = []
    for name, device_type, props in devices:
        desired = {"type": device_type, **_device_props(props)}
        if current_devices.get(name) != desired:
            drifted_devices.append(name)
            changed = True
            if name in current_devices:
                yield LxdDeviceCommand(
                    resource="profile",
                    target=profile,
                    device=name,
                    present=False,
                    remote=remote,
                    project=project,
                )
            yield LxdDeviceCommand(
                resource="profile",
                target=profile,
                device=name,
                device_type=device_type,
                props=props,
                remote=remote,
                project=project,
            )
    if drifted_devices:
        reasons.append(
            f"devices will be attached: {', '.join(sorted(drifted_devices))}"
        )

    ctx.changed = changed
    if changed:
        ctx.reason = f"lxd profile {profile} will be updated ({'; '.join(reasons)})"
    else:
        ctx.reason = f"lxd profile {profile} already exists as configured"


@dataclass(frozen=True)
class LxdProjectCommand(Command):
    """``lxc project create [<remote>:]<name> [-c key=value ...]`` /
    ``lxc project delete ...``.

    Creates a project with its creation-time config, or deletes it.
    Config drift on an existing project goes through ``LxdSetCommand``
    with ``resource="project"``.
    """

    project: str
    present: bool = True
    config: tuple[str, ...] = ()
    remote: str = ""

    def shell(self) -> str:
        target = shlex.quote(_remote_name(self.project, self.remote))
        if not self.present:
            parts = ["project", "delete", target]
        else:
            parts = ["project", "create", target]
            for entry in self.config:
                parts.extend(["-c", shlex.quote(entry)])
        return _lxc_command(parts)


async def project(
    ctx: OpContext,
    project: str,
    present: bool = True,
    config: tuple[str, ...] = (),
    remote: str = "",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, configure or delete an LXD project.

    Args:
        ctx (OpContext): Execution context for the operation.
        project (str): The project name to manage.
        present (bool, optional): Whether the project should exist.
            Defaults to True.
        config (tuple[str, ...], optional): Configuration entries as
            ``"key=value"`` strings, e.g.
            ``("features.images=true", "features.profiles=true")``.
            Defaults to ().
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD project commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_project.py``.
    """

    # reemote5-original (there is no other project operation). Idempotent
    # against LxdProjects (existence) and LxdProjectConfig (config
    # drift): absent -> create with the -c config flags; existing ->
    # set per drifted key (features and limits are mutable after
    # creation). present=False deletes the project (the CLI fails
    # while it is still in use -- empty it first). The ``default``
    # project cannot be deleted; the CLI enforces that at apply time.
    # ``**kwargs`` absorbs any further global arguments.
    pairs = _config_pairs(config)
    projects = await ctx.facts.get(LxdProjects, remote=remote)
    current = next((entry for entry in projects if entry.get("name") == project), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"lxd project {project} will be deleted"
            yield LxdProjectCommand(project=project, present=False, remote=remote)
        else:
            ctx.reason = f"lxd project {project} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"lxd project {project} will be created"
        yield LxdProjectCommand(project=project, config=config, remote=remote)
        return

    current_config_doc = await ctx.facts.get(LxdProjectConfig, project, remote=remote)
    current_config = (current_config_doc or {}).get("config") or {}
    drifted_keys = [key for key, value in pairs if current_config.get(key) != value]
    if drifted_keys:
        ctx.changed = True
        ctx.reason = (
            f"lxd project {project} config will be set: "
            f"{', '.join(sorted(drifted_keys))}"
        )
        for key, value in pairs:
            if key in drifted_keys:
                yield LxdSetCommand(
                    resource="project",
                    target=project,
                    key=key,
                    value=value,
                    remote=remote,
                )
    else:
        ctx.reason = f"lxd project {project} already exists as configured"


@dataclass(frozen=True)
class LxdStoragePoolCommand(Command):
    """``lxc storage create [<remote>:]<pool> <driver> [key=value ...]`` /
    ``lxc storage delete [<remote>:]<pool>``.

    Creates a pool with its driver and creation-time config, or deletes
    it. Config drift on an existing pool goes through ``LxdSetCommand``
    with ``resource="storage"``.
    """

    pool: str
    present: bool = True
    driver: str = "dir"
    config: tuple[str, ...] = ()
    remote: str = ""

    def shell(self) -> str:
        target = shlex.quote(_remote_name(self.pool, self.remote))
        if not self.present:
            parts = ["storage", "delete", target]
        else:
            parts = ["storage", "create", target, shlex.quote(self.driver)]
            parts.extend(shlex.quote(entry) for entry in self.config)
        return _lxc_command(parts)


async def storage_pool(
    ctx: OpContext,
    pool: str,
    present: bool = True,
    driver: str = "dir",
    config: tuple[str, ...] = (),
    remote: str = "",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, configure or delete an LXD storage pool.

    Args:
        ctx (OpContext): Execution context for the operation.
        pool (str): The pool name to manage.
        present (bool, optional): Whether the pool should exist.
            Defaults to True.
        driver (str, optional): The storage driver (``dir``, ``zfs``,
            ``btrfs``, ``lvm``, ``ceph``); immutable after creation.
            Defaults to "dir".
        config (tuple[str, ...], optional): Configuration entries as
            ``"key=value"`` strings, e.g. ``("source=/var/lib/lxd/pools/fast",)``.
            Defaults to ().
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD storage pool commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/lxd/lxd_storage_pool.py``.
    """

    # reemote5-original (there is no other storage operation). Idempotent
    # against LxdStoragePools: absent -> create with the driver and
    # creation-time config; existing -> set per drifted config key.
    # A driver mismatch raises OperationError (LXD cannot change a
    # pool's driver, the postgresql.tablespace precedent).
    # present=False deletes the pool (the CLI fails while volumes
    # still live there -- empty it first). Pools are server-wide: no
    # project option. ``**kwargs`` absorbs any further global arguments.
    pairs = _config_pairs(config)
    pools = await ctx.facts.get(LxdStoragePools, remote=remote)
    current = next((entry for entry in pools if entry.get("name") == pool), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"lxd storage pool {pool} will be deleted"
            yield LxdStoragePoolCommand(pool=pool, present=False, remote=remote)
        else:
            ctx.reason = f"lxd storage pool {pool} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"lxd storage pool {pool} will be created"
        yield LxdStoragePoolCommand(
            pool=pool, driver=driver, config=config, remote=remote
        )
        return

    current_driver = current.get("driver")
    if current_driver and current_driver != driver:
        raise OperationError(
            f"lxd storage pool {pool} uses {current_driver}, cannot become "
            f"{driver}; delete it first"
        )

    current_config = current.get("config") or {}
    drifted_keys = [key for key, value in pairs if current_config.get(key) != value]
    if drifted_keys:
        ctx.changed = True
        ctx.reason = (
            f"lxd storage pool {pool} config will be set: "
            f"{', '.join(sorted(drifted_keys))}"
        )
        for key, value in pairs:
            if key in drifted_keys:
                yield LxdSetCommand(
                    resource="storage",
                    target=pool,
                    key=key,
                    value=value,
                    remote=remote,
                )
    else:
        ctx.reason = f"lxd storage pool {pool} already exists as configured"


@dataclass(frozen=True)
class LxdStorageVolumeCommand(Command):
    """``lxc storage volume create [<remote>:]<pool> <volume> [key=value ...]`` /
    ``lxc storage volume delete [<remote>:]<pool> <volume>``.

    Creates a CUSTOM volume (the only user-manageable type) with its
    creation-time config, or deletes one.
    """

    pool: str
    volume: str
    present: bool = True
    config: tuple[str, ...] = ()
    remote: str = ""
    project: str = ""

    def shell(self) -> str:
        pool = shlex.quote(_remote_name(self.pool, self.remote))
        volume = shlex.quote(self.volume)
        verb = "create" if self.present else "delete"
        parts = ["storage", "volume", verb, pool, volume]
        if self.present:
            parts.extend(shlex.quote(entry) for entry in self.config)
        return _lxc_command(parts, self.project)


async def storage_volume(
    ctx: OpContext,
    pool: str,
    volume: str,
    present: bool = True,
    config: tuple[str, ...] = (),
    remote: str = "",
    project: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create or delete a custom LXD storage volume in a pool.

    Args:
        ctx (OpContext): Execution context for the operation.
        pool (str): The storage pool holding the volume (must already
            exist).
        volume (str): The custom volume name to manage.
        present (bool, optional): Whether the volume should exist.
            Defaults to True.
        config (tuple[str, ...], optional): Configuration entries as
            ``"key=value"`` strings, e.g. ``("size=10GiB",)``;
            creation-only. Defaults to ().
        remote (str, optional): The configured lxc remote to manage;
            empty means the local daemon. Defaults to "".
        project (str, optional): The project to manage the volume in.
            Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated LXD storage volume commands.

    **Raises:**

    ```python
    HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/lxd/lxd_storage_volume.py``.
    """

    # reemote5-original (there is no other volume operation). Idempotent
    # on EXISTENCE against LxdStorageVolumes (the mysql.table
    # precedent): an absent custom volume is created with its config;
    # an existing one is left alone -- config is creation-only, the
    # fact provides the visibility and ``lxc storage volume set``
    # through an action the escape hatch. A name collision with an
    # instance/image volume (type != custom) raises OperationError:
    # those volumes are managed by LXD, never by this operation.
    # present=False deletes the custom volume (the CLI fails while it
    # is still attached). ``**kwargs`` absorbs any further global
    # arguments.
    _config_pairs(config)
    volumes = await ctx.facts.get(
        LxdStorageVolumes, pool, remote=remote, project=project
    )
    current = next((entry for entry in volumes if entry.get("name") == volume), None)

    if current is not None and current.get("type", "custom") != "custom":
        raise OperationError(
            f"lxd volume {pool}/{volume} is a {current.get('type')} volume, "
            "managed by LXD; only custom volumes are managed here"
        )

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"lxd volume {pool}/{volume} will be deleted"
            yield LxdStorageVolumeCommand(
                pool=pool, volume=volume, present=False, remote=remote, project=project
            )
        else:
            ctx.reason = f"lxd volume {pool}/{volume} does not exist"
        return

    if current is not None:
        ctx.reason = f"lxd volume {pool}/{volume} already exists"
        return

    ctx.changed = True
    ctx.reason = f"lxd volume {pool}/{volume} will be created"
    yield LxdStorageVolumeCommand(
        pool=pool, volume=volume, config=config, remote=remote, project=project
    )


__all__ = [
    "LxdContainer",
    "LxdContainers",
    "LxdCopyCommand",
    "LxdDeleteCommand",
    "LxdDeviceCommand",
    "LxdImage",
    "LxdImageAlias",
    "LxdImageAliasCommand",
    "LxdImageAliases",
    "LxdImageCommand",
    "LxdImages",
    "LxdInstance",
    "LxdInstanceConfig",
    "LxdInstanceSnapshots",
    "LxdInstanceState",
    "LxdInstanceStateData",
    "LxdInstances",
    "LxdLaunchCommand",
    "LxdNetwork",
    "LxdNetworkCommand",
    "LxdNetworks",
    "LxdOperation",
    "LxdOperationCancelCommand",
    "LxdOperationWaitCommand",
    "LxdOperations",
    "LxdProfile",
    "LxdProfileAssignCommand",
    "LxdProfileCommand",
    "LxdProfiles",
    "LxdProject",
    "LxdProjectCommand",
    "LxdProjectConfig",
    "LxdProjects",
    "LxdRestoreCommand",
    "LxdServerInfo",
    "LxdServerInfoData",
    "LxdServerResources",
    "LxdServerResourcesData",
    "LxdSetCommand",
    "LxdSnapshot",
    "LxdSnapshotCommand",
    "LxdStateCommand",
    "LxdStoragePool",
    "LxdStoragePoolCommand",
    "LxdStoragePools",
    "LxdStorageVolume",
    "LxdStorageVolumeCommand",
    "LxdStorageVolumes",
    "container",
    "image",
    "image_alias",
    "instance",
    "instance_config",
    "instance_copy",
    "instance_snapshot",
    "instance_state",
    "network",
    "operation",
    "profile",
    "project",
    "storage_pool",
    "storage_volume",
]