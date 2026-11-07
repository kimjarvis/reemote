# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the lxd module: the sixteen ``Lxd*`` facts and the
``container``/``instance``/``instance_state``/``instance_config``/
``instance_snapshot``/``instance_copy``/``image``/``image_alias``/
``network``/``profile``/``project``/``storage_pool``/``storage_volume``/
``operation`` operations.

Covers ``reemote5.modules.lxd.lxd``. ``LxdContainers`` and ``container``
mirror pyinfra's ``lxd.LxdContainers`` / ``lxd.container``, so their
real-transport parity reuses pyinfra's own fixtures verbatim (the canned
``lxc list --format json --fast`` output, the launch/stop/delete shells
including the injection-quoting case, and the noop reasons). The fifteen
reemote5-original facts have no upstream fixture, so they are exercised
with realistic canned ``lxc --format json`` documents plus their parsing
edge cases (empty output, the guarded single-target reads, the shared
``lxc info`` command, the snapshots projection).

Every fact also reads its ``SimulatedHost`` seam with copy-on-read
semantics, every descriptor's shell rendering is checked (remote prefix,
explicit ``--project`` flag, ``shlex`` quoting, the elevated forms), and
the ``execute_command`` arms are driven through ``send_command`` so a
create-then-noop cycle is observable in the simulated state.
"""

import hashlib
import json

import pytest

import reemote5
from reemote5.commands import WaitTimeoutError
from reemote5.core import FactCache, OpContext, OperationError, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import lxd
from reemote5.modules.lxd.lxd import (
    LxdContainer,
    LxdContainers,
    LxdCopyCommand,
    LxdDeleteCommand,
    LxdDeviceCommand,
    LxdImage,
    LxdImageAlias,
    LxdImageAliasCommand,
    LxdImageAliases,
    LxdImageCommand,
    LxdImages,
    LxdInstance,
    LxdInstanceConfig,
    LxdInstances,
    LxdInstanceSnapshots,
    LxdInstanceState,
    LxdInstanceStateData,
    LxdLaunchCommand,
    LxdNetwork,
    LxdNetworkCommand,
    LxdNetworks,
    LxdOperation,
    LxdOperationCancelCommand,
    LxdOperations,
    LxdOperationWaitCommand,
    LxdProfile,
    LxdProfileAssignCommand,
    LxdProfileCommand,
    LxdProfiles,
    LxdProject,
    LxdProjectCommand,
    LxdProjectConfig,
    LxdProjects,
    LxdRestoreCommand,
    LxdServerInfo,
    LxdServerInfoData,
    LxdServerResources,
    LxdServerResourcesData,
    LxdSetCommand,
    LxdSnapshot,
    LxdSnapshotCommand,
    LxdStateCommand,
    LxdStoragePool,
    LxdStoragePoolCommand,
    LxdStoragePools,
    LxdStorageVolume,
    LxdStorageVolumeCommand,
    LxdStorageVolumes,
    _config_pairs,
    _device_props,
    _is_fingerprint,
    _lxc_command,
    _parse_json,
    _remote_name,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


# --- shared helpers ----------------------------------------------------------


async def materialize(host: Host, operation, **kwargs):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in operation(ctx, **kwargs)]
    return ctx, commands


# A full SHA-256 fingerprint (the sha256 of the empty string) and the
# deterministic fingerprint the simulated ``lxc image copy`` derives from
# its source reference -- the image-drift report compares the two forms.
FP = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
COPY_FP = hashlib.sha256(b"images:ubuntu/24.04").hexdigest()

IMAGE_ENTRY: LxdImage = {
    "fingerprint": FP,
    "aliases": [{"name": "ubuntu/24.04", "description": ""}],
    "public": False,
    "cached": True,
    "size": 123456,
    "type": "container",
    "properties": {"os": "ubuntu", "release": "24.04"},
}
ALIAS_ENTRY: LxdImageAlias = {
    "name": "ubuntu/24.04",
    "target": FP,
    "type": "image",
    "description": "",
}
INSTANCE_ENTRY: LxdInstance = {
    "name": "web1",
    "description": "",
    "status": "Running",
    "type": "container",
    "architecture": "x86_64",
    "ephemeral": False,
    "profiles": ["default"],
    "config": {"limits.cpu": "2", "volatile.base_image": FP},
    "devices": {"eth0": {"type": "nic", "network": "lxdbr0"}},
    "location": "none",
}
CONFIG_DOC: LxdInstance = {
    "name": "web1",
    "description": "",
    "config": {"limits.cpu": "2", "volatile.base_image": FP},
    "devices": {"eth0": {"type": "nic", "network": "lxdbr0"}},
    "profiles": ["default"],
    "expanded_config": {"limits.cpu": "2", "limits.memory": "2GB"},
    "expanded_devices": {
        "eth0": {"type": "nic", "network": "lxdbr0"},
        "root": {"type": "disk", "path": "/", "pool": "default"},
    },
}
SNAPSHOT_ENTRY: LxdSnapshot = {
    "name": "snap0",
    "description": "",
    "stateful": False,
}
STATE_DOC: LxdInstanceStateData = {
    "name": "web1",
    "status": "Running",
    "type": "container",
    "pid": 4242,
    "processes": 12,
    "memory": {"usage": 1024, "usage_peak": 2048},
    "network": {
        "eth0": {
            "addresses": [
                {
                    "family": "inet",
                    "address": "10.0.0.2",
                    "netmask": "24",
                    "scope": "global",
                }
            ],
            "state": "up",
        }
    },
    "snapshots": [SNAPSHOT_ENTRY],
}
NETWORK_ENTRY: LxdNetwork = {
    "name": "lxdbr0",
    "description": "",
    "type": "bridge",
    "managed": True,
    "status": "Created",
    "config": {"ipv4.address": "10.0.0.1/24", "ipv4.nat": "true"},
    "locations": ["none"],
}
UNMANAGED_NETWORK: LxdNetwork = {
    "name": "eth0",
    "type": "physical",
    "managed": False,
    "status": "",
    "config": {},
}
OPERATION_ENTRY: LxdOperation = {
    "id": "6d1c2f8e-0000-4000-8000-000000000001",
    "description": "Starting instance",
    "status": "Running",
    "status_code": 103,
    "may_cancel": True,
    "err": "",
}
PROFILE_ENTRY: LxdProfile = {
    "name": "default",
    "description": "Default LXD profile",
    "config": {"limits.memory": "2GB"},
    "devices": {"root": {"type": "disk", "path": "/", "pool": "default"}},
    "used_by": ["/1.0/instances/web1"],
}
PROJECT_ENTRY: LxdProject = {
    "name": "default",
    "description": "Default LXD project",
    "config": {"features.images": "false", "features.profiles": "false"},
    "used_by": [],
}
SERVER_INFO: LxdServerInfoData = {
    "server": "lxd",
    "server_version": "5.21",
    "api_version": "1.0",
    "api_status": "stable",
    "api_extensions": ["etag", "instances", "projects"],
    "auth": "trusted",
    "config": {},
    "environment": {"kernel": "Linux", "server": "lxd"},
}
SERVER_RESOURCES: LxdServerResourcesData = {
    "cpu": {"total": 8},
    "memory": {"used": 1024, "total": 8192},
    "storage": {"disks": [], "total": 1},
}
POOL_ENTRY: LxdStoragePool = {
    "name": "default",
    "description": "",
    "driver": "dir",
    "status": "Created",
    "config": {"source": "/var/lib/lxd/storage-pools/default"},
    "locations": ["none"],
}
VOLUME_ENTRY: LxdStorageVolume = {
    "name": "data",
    "description": "",
    "type": "custom",
    "content_type": "filesystem",
    "config": {"size": "10GiB"},
    "location": "none",
}


def test_parse_json_joins_lines_and_reads_empty_as_none():
    # The guarded single-target facts produce NO stdout for a missing
    # target, so empty output is None rather than a decode error.
    assert _parse_json([]) is None
    assert _parse_json(["", "  "]) is None
    assert _parse_json(['{"a":', " 1}"]) == {"a": 1}


def test_lxc_command_renders_the_project_flag_only_when_set():
    # The parity descriptors carry project="" and render upstream's exact
    # unscoped shell; the new operations pass "default" explicitly.
    assert _lxc_command(["list", "--format", "json"]) == "lxc list --format json"
    assert _lxc_command(["list"], "default") == "lxc list --project default"
    assert _lxc_command(["list"], "my project") == "lxc list --project 'my project'"


def test_remote_name_prefixes_only_a_non_empty_remote():
    assert _remote_name("web1", "") == "web1"
    assert _remote_name("web1", "prod") == "prod:web1"


def test_config_pairs_splits_on_the_first_equals():
    assert _config_pairs(()) == []
    assert _config_pairs(("limits.cpu=2",)) == [("limits.cpu", "2")]
    # A value may itself contain "=" (only the first one splits), and an
    # empty value is a legitimate unset-by-empty-string request.
    assert _config_pairs(("raw.lxc=lxc.foo=bar",)) == [("raw.lxc", "lxc.foo=bar")]
    assert _config_pairs(("user.note=",)) == [("user.note", "")]


def test_config_pairs_rejects_malformed_entries_in_prepare():
    with pytest.raises(ValueError, match="expected 'key=value'"):
        _config_pairs(("limits.cpu",))
    with pytest.raises(ValueError, match="expected 'key=value'"):
        _config_pairs(("=2",))


def test_device_props_and_is_fingerprint():
    assert _device_props(("network=lxdbr0", "nictype=bridged")) == {
        "network": "lxdbr0",
        "nictype": "bridged",
    }
    assert _is_fingerprint(FP)
    assert not _is_fingerprint("ubuntu/24.04")
    assert not _is_fingerprint(FP[:-1])  # 63 chars
    assert not _is_fingerprint(FP.upper())  # uppercase is not lxc's form


# --- exports -----------------------------------------------------------------


def test_lxd_namespace_exports_operations_and_descriptors():
    # The fourteen operations are reached through the namespace and the
    # package, never the top level; the eighteen descriptors and the
    # sixteen facts (plus their entry types) are re-exported at all three
    # sites like every other module.
    for name in (
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
    ):
        assert getattr(lxd, name) is getattr(lxd.lxd, name)
        assert not hasattr(reemote5, name)
    for name in (
        "LxdContainers",
        "LxdImageAliases",
        "LxdImages",
        "LxdInstanceConfig",
        "LxdInstanceSnapshots",
        "LxdInstanceState",
        "LxdInstances",
        "LxdNetworks",
        "LxdOperations",
        "LxdProfiles",
        "LxdProjectConfig",
        "LxdProjects",
        "LxdServerInfo",
        "LxdServerResources",
        "LxdStoragePools",
        "LxdStorageVolumes",
        "LxdCopyCommand",
        "LxdDeleteCommand",
        "LxdDeviceCommand",
        "LxdImageAliasCommand",
        "LxdImageCommand",
        "LxdLaunchCommand",
        "LxdNetworkCommand",
        "LxdOperationCancelCommand",
        "LxdOperationWaitCommand",
        "LxdProfileAssignCommand",
        "LxdProfileCommand",
        "LxdProjectCommand",
        "LxdRestoreCommand",
        "LxdSetCommand",
        "LxdSnapshotCommand",
        "LxdStateCommand",
        "LxdStoragePoolCommand",
        "LxdStorageVolumeCommand",
    ):
        module_cls = getattr(lxd.lxd, name)
        assert getattr(lxd, name) is module_cls
        assert getattr(reemote5, name) is module_cls


def test_lxd_all_lists_are_sorted_and_complete():
    module_all = lxd.lxd.__all__
    assert module_all == sorted(module_all)
    assert len(module_all) == 62
    # The package adds the submodule name to the module's own list.
    assert sorted(lxd.__all__) == sorted([*module_all, "lxd"])


# --- LxdContainers (pyinfra parity) ------------------------------------------

# pyinfra's tests/facts/lxd.LxdContainers fixture, verbatim.
CONTAINERS_OUTPUT = '[{"name": "container1", "status": "Running"},{"name": "container2"}]'
CONTAINERS_FACT: list[LxdContainer] = [
    {"name": "container1", "status": "Running"},
    {"name": "container2"},
]


def test_containers_command_is_pyinfras_unscoped_fast_listing():
    assert LxdContainers.COMMAND == "lxc list --format json --fast"


async def test_containers_real_transport_matches_pyinfras_fixture():
    host = CannedShellHost(CONTAINERS_OUTPUT)
    assert await LxdContainers.process(host) == CONTAINERS_FACT
    assert host.shells == ["lxc list --format json --fast"]


def test_containers_parse_passes_extra_keys_through_and_empty_is_a_list():
    # The hardened deviation: empty stdout parses to [] instead of
    # pyinfra's assert-on-one-line crash. Extra fast columns survive.
    assert LxdContainers.parse([]) == []
    assert LxdContainers.parse([""]) == []
    lines = ['[{"name": "c1", "ipv4": "10.0.0.2", "snapshots": 2}]']
    assert LxdContainers.parse(lines) == [
        {"name": "c1", "ipv4": "10.0.0.2", "snapshots": 2}
    ]


async def test_containers_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_containers=CONTAINERS_FACT)
    assert await LxdContainers.process(host) == CONTAINERS_FACT
    result = await LxdContainers.process(host)
    result[0]["status"] = "Stopped"
    assert host.lxd_containers[0]["status"] == "Running"
    assert await LxdContainers.process(SimulatedHost(name="h")) == []


# --- LxdImages ---------------------------------------------------------------


def test_images_command_renders_remote_and_project():
    assert LxdImages.command() == "lxc image list --format json --project default"
    assert LxdImages.command(remote="prod") == (
        "lxc image list prod: --format json --project default"
    )
    assert LxdImages.command(project="apps") == (
        "lxc image list --format json --project apps"
    )
    assert LxdImages.command(project="") == "lxc image list --format json"


async def test_images_real_transport_passes_the_document_through():
    host = CannedShellHost(json.dumps([IMAGE_ENTRY]))
    assert await LxdImages.process(host) == [IMAGE_ENTRY]
    assert host.shells == ["lxc image list --format json --project default"]
    assert LxdImages.parse([]) == []


async def test_images_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_images=[IMAGE_ENTRY])
    assert await LxdImages.process(host) == [IMAGE_ENTRY]
    result = await LxdImages.process(host)
    result[0]["fingerprint"] = "bogus"
    assert host.lxd_images[0]["fingerprint"] == FP
    assert await LxdImages.process(SimulatedHost(name="h")) == []


# --- LxdImageAliases ---------------------------------------------------------


def test_image_aliases_command_renders_remote_and_project():
    assert LxdImageAliases.command() == (
        "lxc image alias list --format json --project default"
    )
    assert LxdImageAliases.command(remote="prod", project="apps") == (
        "lxc image alias list prod: --format json --project apps"
    )


async def test_image_aliases_real_transport_passes_the_document_through():
    host = CannedShellHost(json.dumps([ALIAS_ENTRY]))
    assert await LxdImageAliases.process(host) == [ALIAS_ENTRY]
    assert host.shells == ["lxc image alias list --format json --project default"]
    assert LxdImageAliases.parse([]) == []


async def test_image_aliases_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_image_aliases=[ALIAS_ENTRY])
    assert await LxdImageAliases.process(host) == [ALIAS_ENTRY]
    result = await LxdImageAliases.process(host)
    result[0]["target"] = "bogus"
    assert host.lxd_image_aliases[0]["target"] == FP
    assert await LxdImageAliases.process(SimulatedHost(name="h")) == []


# --- LxdInstances ------------------------------------------------------------


def test_instances_command_is_the_full_scoped_listing():
    # No --fast: the full listing carries config/devices/profiles and
    # covers VMs alongside containers.
    assert LxdInstances.command() == "lxc list --format json --project default"
    assert LxdInstances.command(remote="prod", project="apps") == (
        "lxc list prod: --format json --project apps"
    )


async def test_instances_real_transport_passes_the_document_through():
    vm = {**INSTANCE_ENTRY, "name": "vm1", "type": "virtual-machine"}
    host = CannedShellHost(json.dumps([INSTANCE_ENTRY, vm]))
    assert await LxdInstances.process(host) == [INSTANCE_ENTRY, vm]
    assert host.shells == ["lxc list --format json --project default"]
    assert LxdInstances.parse([]) == []


async def test_instances_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_instances=[INSTANCE_ENTRY])
    assert await LxdInstances.process(host) == [INSTANCE_ENTRY]
    result = await LxdInstances.process(host)
    result[0]["status"] = "Stopped"
    assert host.lxd_instances[0]["status"] == "Running"
    assert await LxdInstances.process(SimulatedHost(name="h")) == []


# --- LxdInstanceConfig -------------------------------------------------------


def test_instance_config_command_is_guarded_and_quotes_the_target():
    assert LxdInstanceConfig.command("web1") == (
        "lxc config show web1 --expanded --format json --project default"
        " 2>/dev/null || true"
    )
    # The injection case: the instance name is shlex-quoted, never
    # interpolated raw into the shell.
    assert LxdInstanceConfig.command("a; rm -rf /") == (
        "lxc config show 'a; rm -rf /' --expanded --format json"
        " --project default 2>/dev/null || true"
    )
    assert LxdInstanceConfig.command("web1", remote="prod", project="apps") == (
        "lxc config show prod:web1 --expanded --format json --project apps"
        " 2>/dev/null || true"
    )


async def test_instance_config_real_transport_and_the_empty_guard():
    host = CannedShellHost(json.dumps(CONFIG_DOC))
    assert await LxdInstanceConfig.process(host, "web1") == CONFIG_DOC
    assert host.shells == [LxdInstanceConfig.command("web1")]
    # A missing instance produces NO stdout behind the guard: None.
    assert LxdInstanceConfig.parse([]) is None
    assert await LxdInstanceConfig.process(CannedShellHost(""), "gone") is None


async def test_instance_config_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_instance_configs={"web1": CONFIG_DOC})
    assert await LxdInstanceConfig.process(host, "web1") == CONFIG_DOC
    assert await LxdInstanceConfig.process(host, "gone") is None
    result = await LxdInstanceConfig.process(host, "web1")
    # Copy-on-read is per-entry SHALLOW (the house convention): mutate a
    # top-level key, never a nested structure shared with the seam.
    result["name"] = "renamed"
    assert host.lxd_instance_configs["web1"]["name"] == "web1"
    assert await LxdInstanceConfig.process(SimulatedHost(name="h"), "web1") is None


# --- LxdInstanceState --------------------------------------------------------


def test_instance_state_command_is_the_guarded_info_read():
    assert LxdInstanceState.command("web1") == (
        "lxc info web1 --format json --project default 2>/dev/null || true"
    )
    assert LxdInstanceState.command("web1", remote="prod", project="apps") == (
        "lxc info prod:web1 --format json --project apps 2>/dev/null || true"
    )
    assert LxdInstanceState.command("a; rm -rf /") == (
        "lxc info 'a; rm -rf /' --format json --project default 2>/dev/null || true"
    )


async def test_instance_state_real_transport_and_the_empty_guard():
    host = CannedShellHost(json.dumps(STATE_DOC))
    assert await LxdInstanceState.process(host, "web1") == STATE_DOC
    assert host.shells == [LxdInstanceState.command("web1")]
    # A stopped instance still returns its identity document (no runtime
    # keys); only a MISSING instance reads as None behind the guard.
    stopped = {"name": "web1", "status": "Stopped"}
    assert LxdInstanceState.parse([json.dumps(stopped)]) == stopped
    assert LxdInstanceState.parse([]) is None
    assert await LxdInstanceState.process(CannedShellHost(""), "gone") is None


async def test_instance_state_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_instance_states={"web1": STATE_DOC})
    assert await LxdInstanceState.process(host, "web1") == STATE_DOC
    assert await LxdInstanceState.process(host, "gone") is None
    result = await LxdInstanceState.process(host, "web1")
    result["status"] = "Stopped"
    assert host.lxd_instance_states["web1"]["status"] == "Running"
    assert await LxdInstanceState.process(SimulatedHost(name="h"), "web1") is None


# --- LxdInstanceSnapshots ----------------------------------------------------


def test_instance_snapshots_shares_the_state_command():
    # The DnfRepoFile shared-command precedent: both facts read the same
    # lxc info document, the snapshots fact projects its array.
    assert LxdInstanceSnapshots.command("web1") == LxdInstanceState.command("web1")


def test_instance_snapshots_parse_projects_the_snapshots_array():
    assert LxdInstanceSnapshots.parse([json.dumps(STATE_DOC)]) == [SNAPSHOT_ENTRY]
    # A missing instance, an instance without snapshots and a null
    # snapshots key all read as [].
    assert LxdInstanceSnapshots.parse([]) == []
    assert LxdInstanceSnapshots.parse([json.dumps({"name": "web1"})]) == []
    assert LxdInstanceSnapshots.parse([json.dumps({"snapshots": None})]) == []


async def test_instance_snapshots_real_transport_and_seam():
    host = CannedShellHost(json.dumps(STATE_DOC))
    assert await LxdInstanceSnapshots.process(host, "web1") == [SNAPSHOT_ENTRY]
    assert host.shells == [LxdInstanceSnapshots.command("web1")]
    simulated = SimulatedHost(
        name="h", lxd_instance_snapshots={"web1": [SNAPSHOT_ENTRY]}
    )
    assert await LxdInstanceSnapshots.process(simulated, "web1") == [SNAPSHOT_ENTRY]
    assert await LxdInstanceSnapshots.process(simulated, "gone") == []
    result = await LxdInstanceSnapshots.process(simulated, "web1")
    result[0]["name"] = "renamed"
    assert simulated.lxd_instance_snapshots["web1"][0]["name"] == "snap0"
    assert await LxdInstanceSnapshots.process(SimulatedHost(name="h"), "web1") == []


# --- LxdNetworks -------------------------------------------------------------


def test_networks_command_renders_remote_and_project():
    assert LxdNetworks.command() == "lxc network list --format json --project default"
    assert LxdNetworks.command(remote="prod", project="apps") == (
        "lxc network list prod: --format json --project apps"
    )


async def test_networks_real_transport_includes_unmanaged_interfaces():
    host = CannedShellHost(json.dumps([NETWORK_ENTRY, UNMANAGED_NETWORK]))
    assert await LxdNetworks.process(host) == [NETWORK_ENTRY, UNMANAGED_NETWORK]
    assert host.shells == ["lxc network list --format json --project default"]
    assert LxdNetworks.parse([]) == []


async def test_networks_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_networks=[NETWORK_ENTRY])
    assert await LxdNetworks.process(host) == [NETWORK_ENTRY]
    result = await LxdNetworks.process(host)
    result[0]["managed"] = False
    assert host.lxd_networks[0]["managed"] is True
    assert await LxdNetworks.process(SimulatedHost(name="h")) == []


# --- LxdOperations -----------------------------------------------------------


def test_operations_command_is_server_wide():
    # Operations belong to the daemon, not a project: no --project flag.
    assert LxdOperations.command() == "lxc operation list --format json"
    assert LxdOperations.command(remote="prod") == "lxc operation list prod: --format json"


async def test_operations_real_transport_passes_the_class_key_through():
    # ``class`` is a Python keyword, so the TypedDict cannot declare it;
    # the raw passthrough keeps it in the parsed entries untyped.
    entry = {**OPERATION_ENTRY, "class": "task"}
    host = CannedShellHost(json.dumps([entry]))
    assert await LxdOperations.process(host) == [entry]
    assert host.shells == ["lxc operation list --format json"]
    assert LxdOperations.parse([]) == []


async def test_operations_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_operations=[OPERATION_ENTRY])
    assert await LxdOperations.process(host) == [OPERATION_ENTRY]
    result = await LxdOperations.process(host)
    result[0]["status"] = "Success"
    assert host.lxd_operations[0]["status"] == "Running"
    assert await LxdOperations.process(SimulatedHost(name="h")) == []


# --- LxdProfiles -------------------------------------------------------------


def test_profiles_command_renders_remote_and_project():
    assert LxdProfiles.command() == "lxc profile list --format json --project default"
    assert LxdProfiles.command(remote="prod", project="apps") == (
        "lxc profile list prod: --format json --project apps"
    )


async def test_profiles_real_transport_passes_config_and_devices():
    host = CannedShellHost(json.dumps([PROFILE_ENTRY]))
    assert await LxdProfiles.process(host) == [PROFILE_ENTRY]
    assert host.shells == ["lxc profile list --format json --project default"]
    assert LxdProfiles.parse([]) == []


async def test_profiles_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_profiles=[PROFILE_ENTRY])
    assert await LxdProfiles.process(host) == [PROFILE_ENTRY]
    result = await LxdProfiles.process(host)
    result[0]["description"] = "changed"
    assert host.lxd_profiles[0]["description"] == "Default LXD profile"
    assert await LxdProfiles.process(SimulatedHost(name="h")) == []


# --- LxdProjects -------------------------------------------------------------


def test_projects_command_is_server_wide():
    # The project IS the target: no --project flag on the listing.
    assert LxdProjects.command() == "lxc project list --format json"
    assert LxdProjects.command(remote="prod") == "lxc project list prod: --format json"


async def test_projects_real_transport_passes_the_document_through():
    host = CannedShellHost(json.dumps([PROJECT_ENTRY]))
    assert await LxdProjects.process(host) == [PROJECT_ENTRY]
    assert host.shells == ["lxc project list --format json"]
    assert LxdProjects.parse([]) == []


async def test_projects_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_projects=[PROJECT_ENTRY])
    assert await LxdProjects.process(host) == [PROJECT_ENTRY]
    result = await LxdProjects.process(host)
    result[0]["name"] = "renamed"
    assert host.lxd_projects[0]["name"] == "default"
    assert await LxdProjects.process(SimulatedHost(name="h")) == []


# --- LxdProjectConfig --------------------------------------------------------


def test_project_config_command_is_guarded_and_quotes_the_target():
    assert LxdProjectConfig.command("apps") == (
        "lxc project show apps --format json 2>/dev/null || true"
    )
    assert LxdProjectConfig.command("apps", remote="prod") == (
        "lxc project show prod:apps --format json 2>/dev/null || true"
    )
    assert LxdProjectConfig.command("a; rm -rf /") == (
        "lxc project show 'a; rm -rf /' --format json 2>/dev/null || true"
    )


async def test_project_config_real_transport_and_the_empty_guard():
    host = CannedShellHost(json.dumps(PROJECT_ENTRY))
    assert await LxdProjectConfig.process(host, "default") == PROJECT_ENTRY
    assert host.shells == [LxdProjectConfig.command("default")]
    assert LxdProjectConfig.parse([]) is None
    assert await LxdProjectConfig.process(CannedShellHost(""), "gone") is None


async def test_project_config_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_project_configs={"default": PROJECT_ENTRY})
    assert await LxdProjectConfig.process(host, "default") == PROJECT_ENTRY
    assert await LxdProjectConfig.process(host, "gone") is None
    result = await LxdProjectConfig.process(host, "default")
    result["description"] = "changed"
    assert host.lxd_project_configs["default"]["description"] == "Default LXD project"
    assert (
        await LxdProjectConfig.process(SimulatedHost(name="h"), "default") is None
    )


# --- LxdServerInfo -----------------------------------------------------------


def test_server_info_command_is_the_bare_info_read():
    assert LxdServerInfo.command() == "lxc info --format json"
    assert LxdServerInfo.command(remote="prod") == "lxc info prod: --format json"


async def test_server_info_real_transport_and_the_empty_fallback():
    host = CannedShellHost(json.dumps(SERVER_INFO))
    assert await LxdServerInfo.process(host) == SERVER_INFO
    assert host.shells == ["lxc info --format json"]
    # An empty document reads as {} so capability checks never crash on
    # a missing api_extensions key.
    assert LxdServerInfo.parse([]) == {}


async def test_server_info_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_server_info=SERVER_INFO)
    assert await LxdServerInfo.process(host) == SERVER_INFO
    result = await LxdServerInfo.process(host)
    result["server_version"] = "9.9"
    assert host.lxd_server_info["server_version"] == "5.21"
    assert await LxdServerInfo.process(SimulatedHost(name="h")) == {}


# --- LxdServerResources ------------------------------------------------------


def test_server_resources_command_adds_the_resources_flag():
    assert LxdServerResources.command() == "lxc info --resources --format json"
    assert LxdServerResources.command(remote="prod") == (
        "lxc info prod: --resources --format json"
    )


async def test_server_resources_real_transport_and_the_empty_fallback():
    host = CannedShellHost(json.dumps(SERVER_RESOURCES))
    assert await LxdServerResources.process(host) == SERVER_RESOURCES
    assert host.shells == ["lxc info --resources --format json"]
    assert LxdServerResources.parse([]) == {}


async def test_server_resources_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_server_resources=SERVER_RESOURCES)
    assert await LxdServerResources.process(host) == SERVER_RESOURCES
    result = await LxdServerResources.process(host)
    result["cpu"] = {"total": 99}
    assert host.lxd_server_resources["cpu"]["total"] == 8
    assert await LxdServerResources.process(SimulatedHost(name="h")) == {}


# --- LxdStoragePools ---------------------------------------------------------


def test_storage_pools_command_is_server_wide():
    assert LxdStoragePools.command() == "lxc storage list --format json"
    assert LxdStoragePools.command(remote="prod") == "lxc storage list prod: --format json"


async def test_storage_pools_real_transport_passes_the_document_through():
    host = CannedShellHost(json.dumps([POOL_ENTRY]))
    assert await LxdStoragePools.process(host) == [POOL_ENTRY]
    assert host.shells == ["lxc storage list --format json"]
    assert LxdStoragePools.parse([]) == []


async def test_storage_pools_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_storage_pools=[POOL_ENTRY])
    assert await LxdStoragePools.process(host) == [POOL_ENTRY]
    result = await LxdStoragePools.process(host)
    result[0]["driver"] = "zfs"
    assert host.lxd_storage_pools[0]["driver"] == "dir"
    assert await LxdStoragePools.process(SimulatedHost(name="h")) == []


# --- LxdStorageVolumes -------------------------------------------------------


def test_storage_volumes_command_is_guarded_and_quotes_the_pool():
    assert LxdStorageVolumes.command("default") == (
        "lxc storage volume list default --format json --project default"
        " 2>/dev/null || true"
    )
    assert LxdStorageVolumes.command("default", remote="prod", project="apps") == (
        "lxc storage volume list prod:default --format json --project apps"
        " 2>/dev/null || true"
    )
    assert LxdStorageVolumes.command("a; rm -rf /") == (
        "lxc storage volume list 'a; rm -rf /' --format json --project default"
        " 2>/dev/null || true"
    )


async def test_storage_volumes_real_transport_and_the_empty_guard():
    host = CannedShellHost(json.dumps([VOLUME_ENTRY]))
    assert await LxdStorageVolumes.process(host, "default") == [VOLUME_ENTRY]
    assert host.shells == [LxdStorageVolumes.command("default")]
    # A missing pool produces NO stdout behind the guard: [].
    assert LxdStorageVolumes.parse([]) == []
    assert await LxdStorageVolumes.process(CannedShellHost(""), "gone") == []


async def test_storage_volumes_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lxd_storage_volumes={"default": [VOLUME_ENTRY]})
    assert await LxdStorageVolumes.process(host, "default") == [VOLUME_ENTRY]
    assert await LxdStorageVolumes.process(host, "gone") == []
    result = await LxdStorageVolumes.process(host, "default")
    result[0]["name"] = "renamed"
    assert host.lxd_storage_volumes["default"][0]["name"] == "data"
    assert await LxdStorageVolumes.process(SimulatedHost(name="h"), "default") == []


# --- descriptor shells --------------------------------------------------------


def test_launch_command_renders_pyinfras_parity_shells():
    # pyinfra's tests/operations/lxd.container fixtures, verbatim.
    assert LxdLaunchCommand(image="ubuntu:16.04", instance="container1").shell() == (
        "lxc launch ubuntu:16.04 container1 < /dev/null"
    )
    assert LxdLaunchCommand(image="ubuntu:16.04", instance="a; rm -rf /").shell() == (
        "lxc launch ubuntu:16.04 'a; rm -rf /' < /dev/null"
    )


def test_launch_command_renders_vm_profiles_config_and_devices():
    cmd = LxdLaunchCommand(
        image="images:ubuntu/24.04",
        instance="web1",
        instance_type="vm",
        profiles=("default", "web"),
        config=("limits.cpu=2",),
        devices=(("eth0", "nic", ("network=lxdbr0",)),),
        ephemeral=True,
        remote="prod",
        project="apps",
    )
    # shlex leaves the comma-joined device spec and key=value tokens
    # unquoted: they contain only shell-safe characters.
    assert cmd.shell() == (
        "lxc launch images:ubuntu/24.04 prod:web1 --vm --ephemeral"
        " -p default -p web -c limits.cpu=2 -d eth0,nic,network=lxdbr0"
        " --project apps < /dev/null"
    )


def test_state_command_renders_the_five_verbs_and_flags():
    for action in ("start", "stop", "restart", "pause", "resume"):
        assert LxdStateCommand(instance="web1", action=action).shell() == (
            f"lxc {action} web1"
        )
    assert LxdStateCommand(
        instance="web1", action="stop", force=True, timeout=30
    ).shell() == "lxc stop web1 --force --timeout 30"
    assert LxdStateCommand(
        instance="web1", action="start", remote="prod", project="apps"
    ).shell() == "lxc start prod:web1 --project apps"


def test_delete_command_renders_instances_and_snapshots():
    assert LxdDeleteCommand(instance="container1").shell() == "lxc delete container1"
    # The instance/snapshot name form deletes ONE snapshot.
    assert LxdDeleteCommand(instance="web1/snap0").shell() == "lxc delete web1/snap0"
    assert LxdDeleteCommand(
        instance="web1", remote="prod", project="apps"
    ).shell() == "lxc delete prod:web1 --project apps"


def test_image_command_renders_copy_and_delete():
    assert LxdImageCommand(
        image="ubuntu/24.04",
        source="images:ubuntu/24.04",
        alias="ubuntu/24.04",
        project="default",
    ).shell() == (
        "lxc image copy images:ubuntu/24.04 local: --alias ubuntu/24.04"
        " --project default"
    )
    # A fingerprint request carries no --alias, and a remote destination
    # replaces the local: target.
    assert LxdImageCommand(image=FP, source="images:ubuntu/24.04", remote="prod").shell() == (
        "lxc image copy images:ubuntu/24.04 prod:"
    )
    assert LxdImageCommand(image=FP, present=False, remote="prod").shell() == (
        f"lxc image delete prod:{FP}"
    )


def test_image_alias_command_renders_create_and_delete():
    assert LxdImageAliasCommand(alias="ubuntu/24.04", fingerprint=FP).shell() == (
        f"lxc image alias create ubuntu/24.04 {FP}"
    )
    assert LxdImageAliasCommand(alias="ubuntu/24.04", present=False).shell() == (
        "lxc image alias delete ubuntu/24.04"
    )


def test_set_command_renders_set_and_unset_for_every_resource():
    assert LxdSetCommand(
        resource="config", target="web1", key="limits.cpu", value="2"
    ).shell() == "lxc config set web1 limits.cpu 2"
    # value=None renders the unset verb with no value argument.
    assert LxdSetCommand(resource="config", target="web1", key="limits.cpu").shell() == (
        "lxc config unset web1 limits.cpu"
    )
    assert LxdSetCommand(
        resource="profile", target="web", key="limits.memory", value="2GB"
    ).shell() == "lxc profile set web limits.memory 2GB"
    # A value containing whitespace is shlex-quoted.
    assert LxdSetCommand(
        resource="network", target="lxdbr0", key="dns.domain", value="my domain"
    ).shell() == "lxc network set lxdbr0 dns.domain 'my domain'"


def test_device_command_renders_add_and_remove():
    assert LxdDeviceCommand(
        resource="config",
        target="web1",
        device="eth0",
        device_type="nic",
        props=("network=lxdbr0",),
        project="default",
    ).shell() == "lxc config device add web1 eth0 nic network=lxdbr0 --project default"
    assert LxdDeviceCommand(
        resource="profile", target="default", device="eth0", present=False
    ).shell() == "lxc profile device remove default eth0"


def test_profile_assign_command_joins_profiles_in_order():
    assert LxdProfileAssignCommand(
        instance="web1", profiles=("default", "web"), project="default"
    ).shell() == "lxc profile assign web1 default,web --project default"


def test_copy_command_renders_flags_and_the_dest_remote():
    assert LxdCopyCommand(instance="web1", dest_instance="web2").shell() == (
        "lxc copy web1 web2"
    )
    assert LxdCopyCommand(
        instance="web1",
        dest_instance="web2",
        dest_remote="prod",
        instance_only=True,
        stateless=True,
        project="default",
    ).shell() == "lxc copy web1 prod:web2 --instance-only --stateless --project default"


def test_snapshot_and_restore_commands_render_stateful():
    assert LxdSnapshotCommand(instance="web1", snapshot="snap0").shell() == (
        "lxc snapshot web1 snap0"
    )
    assert LxdSnapshotCommand(
        instance="web1", snapshot="snap0", stateful=True, project="default"
    ).shell() == "lxc snapshot web1 snap0 --stateful --project default"
    assert LxdRestoreCommand(instance="web1", snapshot="snap0", stateful=True).shell() == (
        "lxc restore web1 snap0 --stateful"
    )


def test_network_command_renders_type_and_creation_config():
    assert LxdNetworkCommand(
        network="lxdbr0", config=("ipv4.nat=true",), project="default"
    ).shell() == "lxc network create lxdbr0 --type bridge ipv4.nat=true --project default"
    assert LxdNetworkCommand(network="lxdbr0", network_type="macvlan").shell() == (
        "lxc network create lxdbr0 --type macvlan"
    )
    assert LxdNetworkCommand(network="lxdbr0", present=False).shell() == (
        "lxc network delete lxdbr0"
    )


def test_operation_cancel_command_renders_the_delete_verb():
    assert LxdOperationCancelCommand(operation_id=OPERATION_ENTRY["id"]).shell() == (
        f"lxc operation delete {OPERATION_ENTRY['id']}"
    )
    assert LxdOperationCancelCommand(
        operation_id=OPERATION_ENTRY["id"], remote="prod"
    ).shell() == f"lxc operation delete prod:{OPERATION_ENTRY['id']}"


def test_profile_command_renders_create_and_delete():
    assert LxdProfileCommand(profile="web", project="default").shell() == (
        "lxc profile create web --project default"
    )
    assert LxdProfileCommand(profile="web", present=False).shell() == (
        "lxc profile delete web"
    )


def test_project_command_renders_the_c_flags():
    assert LxdProjectCommand(
        project="apps",
        config=("features.images=true", "features.profiles=true"),
    ).shell() == (
        "lxc project create apps -c features.images=true -c features.profiles=true"
    )
    assert LxdProjectCommand(project="apps", present=False, remote="prod").shell() == (
        "lxc project delete prod:apps"
    )


def test_storage_pool_command_renders_the_driver():
    assert LxdStoragePoolCommand(pool="fast", driver="zfs", config=("size=10GiB",)).shell() == (
        "lxc storage create fast zfs size=10GiB"
    )
    assert LxdStoragePoolCommand(pool="fast", present=False).shell() == (
        "lxc storage delete fast"
    )


def test_storage_volume_command_renders_create_and_delete():
    assert LxdStorageVolumeCommand(
        pool="default", volume="data", config=("size=10GiB",), project="default"
    ).shell() == "lxc storage volume create default data size=10GiB --project default"
    assert LxdStorageVolumeCommand(
        pool="default", volume="data", present=False, project="default"
    ).shell() == "lxc storage volume delete default data --project default"


def test_command_shell_elevates_the_lxd_shells():
    # The simulated seam dispatches by TYPE and never renders shell text,
    # so these empirical command_shell probes are the only check that an
    # elevated lxc command reaches the transport as the right string.
    cmd = LxdStateCommand(instance="container1", action="stop")
    assert command_shell(cmd) == "lxc stop container1"
    assert command_shell(cmd, sudo=True) == "sudo lxc stop container1"
    assert command_shell(cmd, sudo=True, sudo_user="lxd") == (
        "sudo -u lxd lxc stop container1"
    )
    assert command_shell(cmd, su=True) == "su -c 'lxc stop container1'"
    assert command_shell(cmd, doas=True) == "doas -n lxc stop container1"


# --- LxdOperationWaitCommand ---------------------------------------------------


def test_operation_wait_command_is_shell_less_and_loggable():
    cmd = LxdOperationWaitCommand(operation_id="op1")
    assert str(cmd) == "wait for lxd operation op1 to finish every 5.0s up to 300.0s"
    # No shell() method: command_shell falls back to the loggable form.
    assert not callable(getattr(cmd, "shell", None))
    assert command_shell(cmd) == str(cmd)


async def test_operation_wait_succeeds_when_the_id_is_gone():
    # An already-finished operation succeeds on the FIRST poll: the
    # deadline is checked after the poll, never before it.
    host = CannedShellHost("[]")
    cmd = LxdOperationWaitCommand(operation_id="op1", timeout=0.0, interval=0.0)
    assert await cmd.wait(host) == "operation op1 finished after 1 poll(s)"
    assert host.shells == ["lxc operation list --format json"]


async def test_operation_wait_times_out_while_the_id_is_registered():
    host = CannedShellHost(json.dumps([OPERATION_ENTRY]))
    cmd = LxdOperationWaitCommand(
        operation_id=OPERATION_ENTRY["id"], timeout=0.0, interval=0.0
    )
    with pytest.raises(WaitTimeoutError, match="operation still registered"):
        await cmd.wait(host)


# --- container operation (pyinfra parity) --------------------------------------


async def test_container_op_creates_with_pyinfras_launch():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, lxd.container, id="container1", image="ubuntu:16.04"
    )
    assert ctx.changed
    assert ctx.reason == "container container1 will be created"
    assert commands == [LxdLaunchCommand(image="ubuntu:16.04", instance="container1")]
    assert commands[0].shell() == "lxc launch ubuntu:16.04 container1 < /dev/null"


async def test_container_op_noop_when_present():
    host = SimulatedHost(name="h", lxd_containers=CONTAINERS_FACT)
    ctx, commands = await materialize(host, lxd.container, id="container1")
    assert not ctx.changed
    assert ctx.reason == "container container1 exists"
    assert commands == []


async def test_container_op_deletes_a_stopped_container_directly():
    host = SimulatedHost(
        name="h", lxd_containers=[{"name": "container1", "status": "Stopped"}]
    )
    ctx, commands = await materialize(host, lxd.container, id="container1", present=False)
    assert ctx.changed
    assert ctx.reason == "container container1 will be deleted"
    assert commands == [LxdDeleteCommand(instance="container1")]


async def test_container_op_stops_a_running_container_before_deleting():
    host = SimulatedHost(name="h", lxd_containers=CONTAINERS_FACT)
    ctx, commands = await materialize(host, lxd.container, id="container1", present=False)
    assert ctx.changed
    # pyinfra's stop-then-delete sequence, in order.
    assert commands == [
        LxdStateCommand(instance="container1", action="stop"),
        LxdDeleteCommand(instance="container1"),
    ]


async def test_container_op_delete_absent_is_a_noop_and_absorbs_kwargs():
    host = SimulatedHost(name="h")
    # **kwargs absorbs pyinfra's global arguments op() does not strip.
    ctx, commands = await materialize(
        host, lxd.container, id="container1", present=False, _global_arg=True
    )
    assert not ctx.changed
    assert ctx.reason == "container container1 does not exist"
    assert commands == []


# --- image operation -----------------------------------------------------------


async def test_image_op_caches_an_absent_alias_from_source():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        lxd.image,
        image="ubuntu/24.04",
        source="images:ubuntu/24.04",
    )
    assert ctx.changed
    assert ctx.reason == "lxd image ubuntu/24.04 will be cached from images:ubuntu/24.04"
    assert commands == [
        LxdImageCommand(
            image="ubuntu/24.04",
            source="images:ubuntu/24.04",
            alias="ubuntu/24.04",
            project="default",
        )
    ]


async def test_image_op_caches_a_fingerprint_without_an_alias_flag():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, lxd.image, image=FP, source="images:ubuntu/24.04"
    )
    assert ctx.changed
    assert commands[0].alias == ""


async def test_image_op_noop_when_present_by_alias_or_fingerprint():
    host = SimulatedHost(name="h", lxd_images=[IMAGE_ENTRY])
    for reference in ("ubuntu/24.04", FP):
        ctx, commands = await materialize(host, lxd.image, image=reference)
        assert not ctx.changed
        assert ctx.reason == f"lxd image {reference} already cached"
        assert commands == []


async def test_image_op_requires_source_for_an_absent_image():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="requires source"):
        await materialize(host, lxd.image, image="ubuntu/24.04")


async def test_image_op_deletes_by_reference_and_noops_when_absent():
    host = SimulatedHost(name="h", lxd_images=[IMAGE_ENTRY])
    ctx, commands = await materialize(host, lxd.image, image=FP, present=False)
    assert ctx.changed
    assert ctx.reason == f"lxd image {FP} will be deleted"
    assert commands == [LxdImageCommand(image=FP, present=False, project="default")]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.image, image=FP, present=False
    )
    assert not ctx.changed
    assert ctx.reason == f"lxd image {FP} does not exist"
    assert commands == []


# --- image_alias operation -----------------------------------------------------


async def test_image_alias_op_creates_an_absent_alias():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, lxd.image_alias, alias="ubuntu/24.04", fingerprint=FP
    )
    assert ctx.changed
    assert ctx.reason == "lxd image alias ubuntu/24.04 will be created"
    assert commands == [
        LxdImageAliasCommand(alias="ubuntu/24.04", fingerprint=FP, project="default")
    ]


async def test_image_alias_op_noop_when_already_targeting_the_fingerprint():
    host = SimulatedHost(name="h", lxd_image_aliases=[ALIAS_ENTRY])
    ctx, commands = await materialize(
        host, lxd.image_alias, alias="ubuntu/24.04", fingerprint=FP
    )
    assert not ctx.changed
    assert ctx.reason == f"lxd image alias ubuntu/24.04 already targets {FP}"
    assert commands == []


async def test_image_alias_op_repoints_by_delete_then_create():
    # The CLI has no alias-repoint verb: a drifted target is deleted
    # and recreated, in that order.
    host = SimulatedHost(name="h", lxd_image_aliases=[ALIAS_ENTRY])
    ctx, commands = await materialize(
        host, lxd.image_alias, alias="ubuntu/24.04", fingerprint=COPY_FP
    )
    assert ctx.changed
    assert ctx.reason == f"lxd image alias ubuntu/24.04 will be repointed to {COPY_FP}"
    assert commands == [
        LxdImageAliasCommand(alias="ubuntu/24.04", present=False, project="default"),
        LxdImageAliasCommand(
            alias="ubuntu/24.04", fingerprint=COPY_FP, project="default"
        ),
    ]


async def test_image_alias_op_requires_a_fingerprint_when_present():
    with pytest.raises(ValueError, match="requires fingerprint"):
        await materialize(SimulatedHost(name="h"), lxd.image_alias, alias="ubuntu/24.04")


async def test_image_alias_op_deletes_only_the_alias():
    host = SimulatedHost(name="h", lxd_image_aliases=[ALIAS_ENTRY])
    ctx, commands = await materialize(
        host, lxd.image_alias, alias="ubuntu/24.04", present=False
    )
    assert ctx.changed
    assert ctx.reason == "lxd image alias ubuntu/24.04 will be deleted"
    assert commands == [
        LxdImageAliasCommand(alias="ubuntu/24.04", present=False, project="default")
    ]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.image_alias, alias="ubuntu/24.04", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "lxd image alias ubuntu/24.04 does not exist"
    assert commands == []


# --- instance operation ---------------------------------------------------------


async def test_instance_op_launches_with_the_full_option_set():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        lxd.instance,
        instance="web1",
        image="images:ubuntu/24.04",
        instance_type="vm",
        profiles=("default", "web"),
        config=("limits.cpu=2",),
        devices=(("eth0", "nic", ("network=lxdbr0",)),),
        ephemeral=True,
    )
    assert ctx.changed
    assert ctx.reason == "lxd instance web1 will be created"
    assert commands == [
        LxdLaunchCommand(
            image="images:ubuntu/24.04",
            instance="web1",
            instance_type="vm",
            profiles=("default", "web"),
            config=("limits.cpu=2",),
            devices=(("eth0", "nic", ("network=lxdbr0",)),),
            ephemeral=True,
            project="default",
        )
    ]


async def test_instance_op_noop_when_already_as_configured():
    # The desired image resolves through the alias to FP, which equals
    # the instance's volatile.base_image: no drift, no commands.
    host = SimulatedHost(
        name="h", lxd_instances=[INSTANCE_ENTRY], lxd_images=[IMAGE_ENTRY]
    )
    ctx, commands = await materialize(
        host,
        lxd.instance,
        instance="web1",
        profiles=("default",),
        config=("limits.cpu=2",),
        devices=(("eth0", "nic", ("network=lxdbr0",)),),
    )
    assert not ctx.changed
    assert ctx.reason == "lxd instance web1 already exists as configured"
    assert commands == []


async def test_instance_op_reconciles_profiles_config_and_devices():
    host = SimulatedHost(
        name="h", lxd_instances=[INSTANCE_ENTRY], lxd_images=[IMAGE_ENTRY]
    )
    ctx, commands = await materialize(
        host,
        lxd.instance,
        instance="web1",
        profiles=("default", "web"),
        config=("limits.cpu=4", "limits.memory=2GB"),
        devices=(("eth0", "nic", ("network=lxdbr1",)),),
    )
    assert ctx.changed
    assert ctx.reason == (
        "lxd instance web1 will be updated (profiles will be assigned; "
        "config keys will be set: limits.cpu, limits.memory; "
        "devices will be attached: eth0)"
    )
    # A drifted device is REMOVED then re-added; the assign replaces the
    # profile list wholesale, in order.
    assert commands == [
        LxdProfileAssignCommand(
            instance="web1", profiles=("default", "web"), project="default"
        ),
        LxdSetCommand(
            resource="config", target="web1", key="limits.cpu", value="4", project="default"
        ),
        LxdSetCommand(
            resource="config",
            target="web1",
            key="limits.memory",
            value="2GB",
            project="default",
        ),
        LxdDeviceCommand(
            resource="config", target="web1", device="eth0", present=False, project="default"
        ),
        LxdDeviceCommand(
            resource="config",
            target="web1",
            device="eth0",
            device_type="nic",
            props=("network=lxdbr1",),
            project="default",
        ),
    ]


async def test_instance_op_reports_image_drift_without_recreating():
    # The developer-approved decision: the alias now resolves to a
    # DIFFERENT fingerprint than volatile.base_image; the mismatch is
    # reported in the reason only -- no commands, not changed.
    drifted_image = {
        **IMAGE_ENTRY,
        "fingerprint": COPY_FP,
    }
    host = SimulatedHost(
        name="h", lxd_instances=[INSTANCE_ENTRY], lxd_images=[drifted_image]
    )
    ctx, commands = await materialize(host, lxd.instance, instance="web1")
    assert not ctx.changed
    assert ctx.reason == (
        "lxd instance web1 exists (base image differs from "
        "images:ubuntu/24.04 (not recreated))"
    )
    assert commands == []


async def test_instance_op_skips_the_image_check_when_unresolvable():
    # An image not cached locally cannot be resolved to a fingerprint:
    # the drift check is skipped silently rather than guessed at.
    host = SimulatedHost(name="h", lxd_instances=[INSTANCE_ENTRY])
    ctx, commands = await materialize(host, lxd.instance, instance="web1")
    assert not ctx.changed
    assert ctx.reason == "lxd instance web1 already exists as configured"
    assert commands == []


async def test_instance_op_refuses_a_type_conversion():
    vm = {**INSTANCE_ENTRY, "type": "virtual-machine"}
    host = SimulatedHost(name="h", lxd_instances=[vm])
    with pytest.raises(OperationError, match="is a virtual-machine, cannot become container"):
        await materialize(host, lxd.instance, instance="web1")


async def test_instance_op_validates_the_instance_type():
    with pytest.raises(ValueError, match="instance_type must be"):
        await materialize(
            SimulatedHost(name="h"), lxd.instance, instance="web1", instance_type="pod"
        )


async def test_instance_op_delete_stops_a_running_instance_first():
    host = SimulatedHost(name="h", lxd_instances=[INSTANCE_ENTRY])
    ctx, commands = await materialize(host, lxd.instance, instance="web1", present=False)
    assert ctx.changed
    assert ctx.reason == "lxd instance web1 will be deleted"
    assert commands == [
        LxdStateCommand(instance="web1", action="stop", project="default"),
        LxdDeleteCommand(instance="web1", project="default"),
    ]
    stopped = {**INSTANCE_ENTRY, "status": "Stopped"}
    ctx, commands = await materialize(
        SimulatedHost(name="h", lxd_instances=[stopped]),
        lxd.instance,
        instance="web1",
        present=False,
    )
    assert commands == [LxdDeleteCommand(instance="web1", project="default")]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.instance, instance="web1", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "lxd instance web1 does not exist"
    assert commands == []


# --- instance_config operation --------------------------------------------------


async def test_instance_config_op_requires_an_existing_instance():
    with pytest.raises(OperationError, match="lxd instance web1 does not exist"):
        await materialize(
            SimulatedHost(name="h"), lxd.instance_config, instance="web1"
        )


async def test_instance_config_op_noop_when_up_to_date():
    host = SimulatedHost(name="h", lxd_instance_configs={"web1": INSTANCE_ENTRY})
    ctx, commands = await materialize(
        host,
        lxd.instance_config,
        instance="web1",
        config=("limits.cpu=2",),
        devices=(("eth0", "nic", ("network=lxdbr0",)),),
        profiles=("default",),
    )
    assert not ctx.changed
    assert ctx.reason == "lxd instance web1 configuration already up to date"
    assert commands == []


async def test_instance_config_op_sets_only_drifted_keys():
    host = SimulatedHost(name="h", lxd_instance_configs={"web1": INSTANCE_ENTRY})
    ctx, commands = await materialize(
        host,
        lxd.instance_config,
        instance="web1",
        config=("limits.cpu=4", "limits.memory=2GB"),
    )
    assert ctx.changed
    assert ctx.reason == (
        "lxd instance web1 will be reconfigured "
        "(config keys will be set: limits.cpu, limits.memory)"
    )
    assert len(commands) == 2
    assert all(isinstance(cmd, LxdSetCommand) for cmd in commands)


async def test_instance_config_op_treats_inherited_values_as_drift():
    # limits.memory lives in expanded_config (a profile value), NOT in
    # the instance's OWN config: setting it as an own key IS a change.
    host = SimulatedHost(name="h", lxd_instance_configs={"web1": CONFIG_DOC})
    ctx, commands = await materialize(
        host, lxd.instance_config, instance="web1", config=("limits.memory=2GB",)
    )
    assert ctx.changed
    assert commands == [
        LxdSetCommand(
            resource="config",
            target="web1",
            key="limits.memory",
            value="2GB",
            project="default",
        )
    ]


async def test_instance_config_op_assigns_profiles_only_when_asked():
    host = SimulatedHost(name="h", lxd_instance_configs={"web1": INSTANCE_ENTRY})
    # profiles=None leaves the list alone even though config drifts.
    ctx, commands = await materialize(
        host, lxd.instance_config, instance="web1", config=("limits.cpu=4",)
    )
    assert not any(isinstance(cmd, LxdProfileAssignCommand) for cmd in commands)
    ctx, commands = await materialize(
        host, lxd.instance_config, instance="web1", profiles=("default", "web")
    )
    assert ctx.changed
    assert ctx.reason == (
        "lxd instance web1 will be reconfigured (profiles will be assigned)"
    )
    assert commands == [
        LxdProfileAssignCommand(
            instance="web1", profiles=("default", "web"), project="default"
        )
    ]


# --- instance_state operation ---------------------------------------------------


async def test_instance_state_op_is_idempotent_for_the_three_target_states():
    running = SimulatedHost(name="h", lxd_instance_states={"web1": STATE_DOC})
    ctx, commands = await materialize(running, lxd.instance_state, instance="web1")
    assert not ctx.changed
    assert ctx.reason == "lxd instance web1 already started"
    assert commands == []
    ctx, commands = await materialize(
        running, lxd.instance_state, instance="web1", state="stopped"
    )
    assert ctx.changed
    assert ctx.reason == "lxd instance web1 will be stopped"
    assert commands == [
        LxdStateCommand(instance="web1", action="stop", project="default")
    ]
    stopped = {**STATE_DOC, "status": "Stopped"}
    frozen = {**STATE_DOC, "status": "Frozen"}
    for doc, state, action in (
        (stopped, "started", "start"),
        (STATE_DOC, "frozen", "pause"),
    ):
        host = SimulatedHost(name="h", lxd_instance_states={"web1": doc})
        ctx, commands = await materialize(
            host, lxd.instance_state, instance="web1", state=state
        )
        assert ctx.changed
        assert commands == [
            LxdStateCommand(instance="web1", action=action, project="default")
        ]
    host = SimulatedHost(name="h", lxd_instance_states={"web1": frozen})
    ctx, commands = await materialize(
        host, lxd.instance_state, instance="web1", state="frozen"
    )
    assert not ctx.changed
    assert ctx.reason == "lxd instance web1 already frozen"


async def test_instance_state_op_restarted_and_unfrozen_always_yield():
    # Stateless actions: a restart is an action, not a desired state,
    # so even a Running instance yields the verb.
    host = SimulatedHost(name="h", lxd_instance_states={"web1": STATE_DOC})
    for state, action in (("restarted", "restart"), ("unfrozen", "resume")):
        ctx, commands = await materialize(
            host, lxd.instance_state, instance="web1", state=state
        )
        assert ctx.changed
        assert ctx.reason == f"lxd instance web1 will be {state}"
        assert commands == [
            LxdStateCommand(instance="web1", action=action, project="default")
        ]


async def test_instance_state_op_forwards_force_and_timeout():
    host = SimulatedHost(name="h", lxd_instance_states={"web1": STATE_DOC})
    _ctx, commands = await materialize(
        host,
        lxd.instance_state,
        instance="web1",
        state="stopped",
        force=True,
        timeout=30,
    )
    assert commands == [
        LxdStateCommand(
            instance="web1", action="stop", force=True, timeout=30, project="default"
        )
    ]


async def test_instance_state_op_validates_state_and_existence():
    host = SimulatedHost(name="h", lxd_instance_states={"web1": STATE_DOC})
    with pytest.raises(ValueError, match="state must be one of"):
        await materialize(host, lxd.instance_state, instance="web1", state="hibernated")
    with pytest.raises(OperationError, match="lxd instance web1 does not exist"):
        await materialize(SimulatedHost(name="h"), lxd.instance_state, instance="web1")


# --- instance_copy operation ----------------------------------------------------


async def test_instance_copy_op_noop_when_the_destination_exists():
    dest = {**INSTANCE_ENTRY, "name": "web2"}
    host = SimulatedHost(name="h", lxd_instances=[INSTANCE_ENTRY, dest])
    ctx, commands = await materialize(
        host, lxd.instance_copy, instance="web1", dest_instance="web2"
    )
    assert not ctx.changed
    assert ctx.reason == "lxd instance web2 already exists"
    assert commands == []


async def test_instance_copy_op_copies_to_an_absent_destination():
    host = SimulatedHost(name="h", lxd_instances=[INSTANCE_ENTRY])
    ctx, commands = await materialize(
        host,
        lxd.instance_copy,
        instance="web1",
        dest_instance="web2",
        instance_only=True,
        stateless=True,
    )
    assert ctx.changed
    assert ctx.reason == "lxd instance web1 will be copied to web2"
    assert commands == [
        LxdCopyCommand(
            instance="web1",
            dest_instance="web2",
            instance_only=True,
            stateless=True,
            project="default",
        )
    ]


async def test_instance_copy_op_cross_remote_always_yields():
    # The destination's fact cannot be read through this host's CLI
    # scope across remotes, so the copy is stateless and always yields.
    dest = {**INSTANCE_ENTRY, "name": "web2"}
    host = SimulatedHost(name="h", lxd_instances=[INSTANCE_ENTRY, dest])
    ctx, commands = await materialize(
        host, lxd.instance_copy, instance="web1", dest_instance="web2", dest_remote="prod"
    )
    assert ctx.changed
    assert commands == [
        LxdCopyCommand(
            instance="web1", dest_instance="web2", dest_remote="prod", project="default"
        )
    ]


async def test_instance_copy_op_requires_a_destination_name():
    with pytest.raises(ValueError, match="requires a dest_instance"):
        await materialize(
            SimulatedHost(name="h"), lxd.instance_copy, instance="web1", dest_instance=""
        )


# --- instance_snapshot operation --------------------------------------------------


async def test_instance_snapshot_op_create_noop_and_delete():
    host = SimulatedHost(name="h", lxd_instance_snapshots={"web1": [SNAPSHOT_ENTRY]})
    ctx, commands = await materialize(
        host, lxd.instance_snapshot, instance="web1", snapshot="snap0"
    )
    assert not ctx.changed
    assert ctx.reason == "lxd snapshot web1/snap0 already exists"
    assert commands == []
    ctx, commands = await materialize(
        host, lxd.instance_snapshot, instance="web1", snapshot="snap1", stateful=True
    )
    assert ctx.changed
    assert ctx.reason == "lxd snapshot web1/snap1 will be created"
    assert commands == [
        LxdSnapshotCommand(
            instance="web1", snapshot="snap1", stateful=True, project="default"
        )
    ]
    ctx, commands = await materialize(
        host, lxd.instance_snapshot, instance="web1", snapshot="snap0", present=False
    )
    assert ctx.changed
    assert ctx.reason == "lxd snapshot web1/snap0 will be deleted"
    # Deletion rides the instance/snapshot name form of lxc delete.
    assert commands == [LxdDeleteCommand(instance="web1/snap0", project="default")]
    ctx, commands = await materialize(
        SimulatedHost(name="h"),
        lxd.instance_snapshot,
        instance="web1",
        snapshot="snap0",
        present=False,
    )
    assert not ctx.changed
    assert ctx.reason == "lxd snapshot web1/snap0 does not exist"
    assert commands == []


async def test_instance_snapshot_op_restore_always_yields():
    host = SimulatedHost(name="h", lxd_instance_snapshots={"web1": [SNAPSHOT_ENTRY]})
    ctx, commands = await materialize(
        host, lxd.instance_snapshot, instance="web1", snapshot="snap0", restore=True
    )
    assert ctx.changed
    assert ctx.reason == "lxd instance web1 will be restored to snap0"
    assert commands == [
        LxdRestoreCommand(instance="web1", snapshot="snap0", project="default")
    ]


async def test_instance_snapshot_op_refuses_restore_with_present_false():
    with pytest.raises(ValueError, match="cannot restore with present=False"):
        await materialize(
            SimulatedHost(name="h"),
            lxd.instance_snapshot,
            instance="web1",
            snapshot="snap0",
            present=False,
            restore=True,
        )


# --- network operation ------------------------------------------------------------


async def test_network_op_creates_with_type_and_config():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        lxd.network,
        network="lxdbr0",
        config=("ipv4.address=10.0.0.1/24", "ipv4.nat=true"),
    )
    assert ctx.changed
    assert ctx.reason == "lxd network lxdbr0 will be created"
    assert commands == [
        LxdNetworkCommand(
            network="lxdbr0",
            config=("ipv4.address=10.0.0.1/24", "ipv4.nat=true"),
            project="default",
        )
    ]


async def test_network_op_noop_when_configured_and_sets_drifted_keys():
    host = SimulatedHost(name="h", lxd_networks=[NETWORK_ENTRY])
    ctx, commands = await materialize(
        host,
        lxd.network,
        network="lxdbr0",
        config=("ipv4.address=10.0.0.1/24", "ipv4.nat=true"),
    )
    assert not ctx.changed
    assert ctx.reason == "lxd network lxdbr0 already exists as configured"
    assert commands == []
    ctx, commands = await materialize(
        host, lxd.network, network="lxdbr0", config=("ipv4.nat=false",)
    )
    assert ctx.changed
    assert ctx.reason == "lxd network lxdbr0 config will be set: ipv4.nat"
    assert commands == [
        LxdSetCommand(
            resource="network",
            target="lxdbr0",
            key="ipv4.nat",
            value="false",
            project="default",
        )
    ]


async def test_network_op_refuses_unmanaged_interfaces_in_both_directions():
    host = SimulatedHost(name="h", lxd_networks=[UNMANAGED_NETWORK])
    with pytest.raises(OperationError, match="refusing to configure it"):
        await materialize(host, lxd.network, network="eth0")
    with pytest.raises(OperationError, match="refusing to delete it"):
        await materialize(host, lxd.network, network="eth0", present=False)


async def test_network_op_refuses_a_type_change():
    host = SimulatedHost(name="h", lxd_networks=[NETWORK_ENTRY])
    with pytest.raises(OperationError, match="is a bridge, cannot become macvlan"):
        await materialize(host, lxd.network, network="lxdbr0", network_type="macvlan")


async def test_network_op_delete_and_absent_noop():
    host = SimulatedHost(name="h", lxd_networks=[NETWORK_ENTRY])
    ctx, commands = await materialize(host, lxd.network, network="lxdbr0", present=False)
    assert ctx.changed
    assert ctx.reason == "lxd network lxdbr0 will be deleted"
    assert commands == [LxdNetworkCommand(network="lxdbr0", present=False, project="default")]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.network, network="lxdbr0", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "lxd network lxdbr0 does not exist"
    assert commands == []


# --- operation primitive -----------------------------------------------------------


async def test_operation_op_wait_always_yields():
    op_id = OPERATION_ENTRY["id"]
    ctx, commands = await materialize(SimulatedHost(name="h"), lxd.operation, operation_id=op_id)
    assert ctx.changed
    assert ctx.reason == f"wait for operation {op_id} to finish"
    assert commands == [LxdOperationWaitCommand(operation_id=op_id)]


async def test_operation_op_cancel_registered_then_wait():
    op_id = OPERATION_ENTRY["id"]
    host = SimulatedHost(name="h", lxd_operations=[OPERATION_ENTRY])
    ctx, commands = await materialize(
        host, lxd.operation, operation_id=op_id, cancel=True, timeout=60.0, interval=1.0
    )
    assert ctx.changed
    assert ctx.reason == (
        f"operation {op_id} will be cancelled; wait for operation {op_id} to finish"
    )
    assert commands == [
        LxdOperationCancelCommand(operation_id=op_id),
        LxdOperationWaitCommand(operation_id=op_id, timeout=60.0, interval=1.0),
    ]


async def test_operation_op_cancel_of_a_pruned_id_is_a_noop_half():
    op_id = OPERATION_ENTRY["id"]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.operation, operation_id=op_id, cancel=True
    )
    assert ctx.changed  # the wait half is stateless and always changes
    assert ctx.reason == (
        f"operation {op_id} is no longer registered; "
        f"wait for operation {op_id} to finish"
    )
    assert commands == [LxdOperationWaitCommand(operation_id=op_id)]


async def test_operation_op_requires_cancel_or_wait():
    with pytest.raises(ValueError, match="requires cancel=True or wait=True"):
        await materialize(
            SimulatedHost(name="h"), lxd.operation, operation_id="op1", wait=False
        )


# --- profile operation ---------------------------------------------------------------


async def test_profile_op_creates_then_sets_and_attaches():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        lxd.profile,
        profile="web",
        config=("limits.cpu=2",),
        devices=(("eth0", "nic", ("network=lxdbr0",)),),
    )
    assert ctx.changed
    assert ctx.reason == (
        "lxd profile web will be updated (profile will be created; "
        "config keys will be set: limits.cpu; devices will be attached: eth0)"
    )
    # The create verb takes no config flags: set/attach follow it.
    assert commands == [
        LxdProfileCommand(profile="web", project="default"),
        LxdSetCommand(
            resource="profile", target="web", key="limits.cpu", value="2", project="default"
        ),
        LxdDeviceCommand(
            resource="profile",
            target="web",
            device="eth0",
            device_type="nic",
            props=("network=lxdbr0",),
            project="default",
        ),
    ]


async def test_profile_op_noop_when_configured_and_readds_drifted_devices():
    host = SimulatedHost(name="h", lxd_profiles=[PROFILE_ENTRY])
    ctx, commands = await materialize(
        host,
        lxd.profile,
        profile="default",
        config=("limits.memory=2GB",),
        devices=(("root", "disk", ("path=/", "pool=default")),),
    )
    assert not ctx.changed
    assert ctx.reason == "lxd profile default already exists as configured"
    assert commands == []
    ctx, commands = await materialize(
        host,
        lxd.profile,
        profile="default",
        devices=(("root", "disk", ("path=/", "pool=fast")),),
    )
    assert ctx.changed
    assert ctx.reason == (
        "lxd profile default will be updated (devices will be attached: root)"
    )
    assert commands == [
        LxdDeviceCommand(
            resource="profile", target="default", device="root", present=False, project="default"
        ),
        LxdDeviceCommand(
            resource="profile",
            target="default",
            device="root",
            device_type="disk",
            props=("path=/", "pool=fast"),
            project="default",
        ),
    ]


async def test_profile_op_delete_and_absent_noop():
    host = SimulatedHost(name="h", lxd_profiles=[PROFILE_ENTRY])
    ctx, commands = await materialize(host, lxd.profile, profile="default", present=False)
    assert ctx.changed
    assert ctx.reason == "lxd profile default will be deleted"
    assert commands == [LxdProfileCommand(profile="default", present=False, project="default")]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.profile, profile="default", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "lxd profile default does not exist"
    assert commands == []


# --- project operation -----------------------------------------------------------------


async def test_project_op_creates_with_the_c_flags():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        lxd.project,
        project="apps",
        config=("features.images=true", "features.profiles=true"),
    )
    assert ctx.changed
    assert ctx.reason == "lxd project apps will be created"
    assert commands == [
        LxdProjectCommand(
            project="apps", config=("features.images=true", "features.profiles=true")
        )
    ]


async def test_project_op_noop_when_configured_and_sets_drifted_keys():
    host = SimulatedHost(
        name="h",
        lxd_projects=[PROJECT_ENTRY],
        lxd_project_configs={"default": PROJECT_ENTRY},
    )
    ctx, commands = await materialize(
        host,
        lxd.project,
        project="default",
        config=("features.images=false", "features.profiles=false"),
    )
    assert not ctx.changed
    assert ctx.reason == "lxd project default already exists as configured"
    assert commands == []
    ctx, commands = await materialize(
        host, lxd.project, project="default", config=("features.images=true",)
    )
    assert ctx.changed
    assert ctx.reason == "lxd project default config will be set: features.images"
    assert commands == [
        LxdSetCommand(
            resource="project", target="default", key="features.images", value="true"
        )
    ]


async def test_project_op_delete_and_absent_noop():
    host = SimulatedHost(name="h", lxd_projects=[PROJECT_ENTRY])
    ctx, commands = await materialize(host, lxd.project, project="default", present=False)
    assert ctx.changed
    assert ctx.reason == "lxd project default will be deleted"
    assert commands == [LxdProjectCommand(project="default", present=False)]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.project, project="default", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "lxd project default does not exist"
    assert commands == []


# --- storage_pool operation ---------------------------------------------------------------


async def test_storage_pool_op_creates_with_driver_and_config():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, lxd.storage_pool, pool="fast", driver="zfs", config=("size=10GiB",)
    )
    assert ctx.changed
    assert ctx.reason == "lxd storage pool fast will be created"
    assert commands == [
        LxdStoragePoolCommand(pool="fast", driver="zfs", config=("size=10GiB",))
    ]


async def test_storage_pool_op_noop_configured_and_sets_drift():
    host = SimulatedHost(name="h", lxd_storage_pools=[POOL_ENTRY])
    ctx, commands = await materialize(
        host,
        lxd.storage_pool,
        pool="default",
        config=("source=/var/lib/lxd/storage-pools/default",),
    )
    assert not ctx.changed
    assert ctx.reason == "lxd storage pool default already exists as configured"
    assert commands == []
    ctx, commands = await materialize(
        host, lxd.storage_pool, pool="default", config=("source=/srv/lxd",)
    )
    assert ctx.changed
    assert ctx.reason == "lxd storage pool default config will be set: source"
    assert commands == [
        LxdSetCommand(resource="storage", target="default", key="source", value="/srv/lxd")
    ]


async def test_storage_pool_op_refuses_a_driver_change():
    host = SimulatedHost(name="h", lxd_storage_pools=[POOL_ENTRY])
    with pytest.raises(OperationError, match="uses dir, cannot become zfs"):
        await materialize(host, lxd.storage_pool, pool="default", driver="zfs")


async def test_storage_pool_op_delete_and_absent_noop():
    host = SimulatedHost(name="h", lxd_storage_pools=[POOL_ENTRY])
    ctx, commands = await materialize(host, lxd.storage_pool, pool="default", present=False)
    assert ctx.changed
    assert ctx.reason == "lxd storage pool default will be deleted"
    assert commands == [LxdStoragePoolCommand(pool="default", present=False)]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.storage_pool, pool="default", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "lxd storage pool default does not exist"
    assert commands == []


# --- storage_volume operation -----------------------------------------------------------------


async def test_storage_volume_op_creates_a_custom_volume():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, lxd.storage_volume, pool="default", volume="data", config=("size=10GiB",)
    )
    assert ctx.changed
    assert ctx.reason == "lxd volume default/data will be created"
    assert commands == [
        LxdStorageVolumeCommand(
            pool="default", volume="data", config=("size=10GiB",), project="default"
        )
    ]


async def test_storage_volume_op_noop_on_existence_config_is_creation_only():
    host = SimulatedHost(name="h", lxd_storage_volumes={"default": [VOLUME_ENTRY]})
    # Even a DIFFERENT requested size is a no-op: config is
    # creation-only (the mysql.table existence precedent).
    ctx, commands = await materialize(
        host, lxd.storage_volume, pool="default", volume="data", config=("size=99GiB",)
    )
    assert not ctx.changed
    assert ctx.reason == "lxd volume default/data already exists"
    assert commands == []


async def test_storage_volume_op_refuses_instance_managed_volumes():
    instance_volume = {**VOLUME_ENTRY, "type": "container"}
    host = SimulatedHost(name="h", lxd_storage_volumes={"default": [instance_volume]})
    with pytest.raises(OperationError, match="managed by LXD"):
        await materialize(host, lxd.storage_volume, pool="default", volume="data")


async def test_storage_volume_op_delete_and_absent_noop():
    host = SimulatedHost(name="h", lxd_storage_volumes={"default": [VOLUME_ENTRY]})
    ctx, commands = await materialize(
        host, lxd.storage_volume, pool="default", volume="data", present=False
    )
    assert ctx.changed
    assert ctx.reason == "lxd volume default/data will be deleted"
    assert commands == [
        LxdStorageVolumeCommand(pool="default", volume="data", present=False, project="default")
    ]
    ctx, commands = await materialize(
        SimulatedHost(name="h"), lxd.storage_volume, pool="default", volume="data", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "lxd volume default/data does not exist"
    assert commands == []


# --- SimulatedHost seam mutations (send_command) --------------------------------


async def test_launch_arm_fills_every_instance_view_then_noops():
    host = SimulatedHost(name="h", lxd_profiles=[PROFILE_ENTRY])
    await send_command(
        host,
        LxdLaunchCommand(
            image="images:ubuntu/24.04",
            instance="web1",
            config=("limits.cpu=2",),
            devices=(("eth0", "nic", ("network=lxdbr0",)),),
            project="default",
        ),
    )
    (entry,) = host.lxd_instances
    assert entry["status"] == "Running"
    assert entry["profiles"] == ["default"]
    # No cached image: the deterministic sha256 of the reference becomes
    # volatile.base_image, exactly what the drift report compares.
    assert entry["config"]["volatile.base_image"] == COPY_FP
    assert host.lxd_containers == [
        {"name": "web1", "status": "Running", "type": "container"}
    ]
    # The expanded views inherit the default profile's config/devices.
    expanded = host.lxd_instance_configs["web1"]
    assert expanded["expanded_config"]["limits.memory"] == "2GB"
    assert expanded["expanded_config"]["limits.cpu"] == "2"
    assert "root" in expanded["expanded_devices"]
    assert "eth0" in expanded["expanded_devices"]
    assert host.lxd_instance_states["web1"]["status"] == "Running"
    assert host.lxd_instance_snapshots["web1"] == []
    # The create-then-noop cycle: re-materializing the same desired
    # state against the mutated seam yields nothing.
    ctx, commands = await materialize(
        host,
        lxd.instance,
        instance="web1",
        config=("limits.cpu=2",),
        devices=(("eth0", "nic", ("network=lxdbr0",)),),
    )
    assert not ctx.changed
    assert commands == []


async def test_state_arm_syncs_every_status_view_and_ephemeral_cascades():
    host = SimulatedHost(name="h")
    await send_command(host, LxdLaunchCommand(image="ubuntu:24.04", instance="web1"))
    for action, status in (
        ("stop", "Stopped"),
        ("start", "Running"),
        ("pause", "Frozen"),
        ("resume", "Running"),
        ("restart", "Running"),
    ):
        await send_command(host, LxdStateCommand(instance="web1", action=action))
        assert host.lxd_instances[0]["status"] == status
        assert host.lxd_containers[0]["status"] == status
        assert host.lxd_instance_states["web1"]["status"] == status
    # Stopping an EPHEMERAL instance deletes it (LXD's own semantics).
    await send_command(
        host, LxdLaunchCommand(image="ubuntu:24.04", instance="tmp", ephemeral=True)
    )
    await send_command(host, LxdStateCommand(instance="tmp", action="stop"))
    assert all(e["name"] != "tmp" for e in host.lxd_instances)
    assert "tmp" not in host.lxd_instance_states
    assert all(c["name"] != "tmp" for c in host.lxd_containers)


async def test_snapshot_restore_and_delete_arms():
    host = SimulatedHost(name="h")
    await send_command(host, LxdLaunchCommand(image="ubuntu:24.04", instance="web1"))
    await send_command(host, LxdSnapshotCommand(instance="web1", snapshot="snap0"))
    assert host.lxd_instance_snapshots["web1"] == [
        {"name": "snap0", "stateful": False, "description": ""}
    ]
    # The state document's snapshots copy stays in sync.
    assert host.lxd_instance_states["web1"]["snapshots"] == [
        {"name": "snap0", "stateful": False, "description": ""}
    ]
    await send_command(host, LxdStateCommand(instance="web1", action="stop"))
    await send_command(
        host, LxdRestoreCommand(instance="web1", snapshot="snap0", stateful=True)
    )
    assert host.lxd_instance_states["web1"]["status"] == "Running"
    await send_command(host, LxdDeleteCommand(instance="web1/snap0"))
    assert host.lxd_instance_snapshots["web1"] == []
    assert host.lxd_instances  # the instance itself survives


async def test_config_device_and_profile_arms_refresh_the_expansion():
    host = SimulatedHost(name="h", lxd_profiles=[PROFILE_ENTRY])
    await send_command(host, LxdLaunchCommand(image="ubuntu:24.04", instance="web1"))
    await send_command(
        host,
        LxdSetCommand(resource="config", target="web1", key="limits.cpu", value="4"),
    )
    assert host.lxd_instance_configs["web1"]["config"]["limits.cpu"] == "4"
    assert host.lxd_instance_configs["web1"]["expanded_config"]["limits.cpu"] == "4"
    # A PROFILE edit re-expands every instance applying it.
    await send_command(
        host,
        LxdSetCommand(resource="profile", target="default", key="limits.memory", value="4GB"),
    )
    assert host.lxd_instance_configs["web1"]["expanded_config"]["limits.memory"] == "4GB"
    await send_command(
        host,
        LxdDeviceCommand(
            resource="config",
            target="web1",
            device="eth0",
            device_type="nic",
            props=("network=lxdbr0",),
        ),
    )
    assert host.lxd_instance_configs["web1"]["devices"]["eth0"] == {
        "type": "nic",
        "network": "lxdbr0",
    }
    await send_command(host, LxdProfileCommand(profile="web"))
    await send_command(
        host, LxdProfileAssignCommand(instance="web1", profiles=("web",))
    )
    assert host.lxd_instances[0]["profiles"] == ["web"]
    # The default profile no longer applies: its memory limit is gone
    # from the expansion, the instance's OWN keys survive.
    expanded = host.lxd_instance_configs["web1"]
    assert "limits.memory" not in expanded["expanded_config"]
    assert expanded["config"]["limits.cpu"] == "4"
    await send_command(
        host, LxdDeviceCommand(resource="config", target="web1", device="eth0", present=False)
    )
    assert "eth0" not in host.lxd_instance_configs["web1"]["devices"]


async def test_copy_arm_clones_config_snapshots_and_volumes():
    host = SimulatedHost(
        name="h",
        lxd_storage_volumes={
            "default": [{"name": "web1", "type": "container", "config": {}}]
        },
    )
    await send_command(
        host,
        LxdLaunchCommand(image="ubuntu:24.04", instance="web1", config=("limits.cpu=2",)),
    )
    await send_command(host, LxdSnapshotCommand(instance="web1", snapshot="snap0"))
    await send_command(host, LxdCopyCommand(instance="web1", dest_instance="web2"))
    copy = next(e for e in host.lxd_instances if e["name"] == "web2")
    assert copy["status"] == "Running"  # the source's status carries over
    assert copy["config"]["limits.cpu"] == "2"
    assert host.lxd_instance_snapshots["web2"] == [
        {"name": "snap0", "stateful": False, "description": ""}
    ]
    assert any(v["name"] == "web2" for v in host.lxd_storage_volumes["default"])
    # Copied, never shared: mutating the copy leaves the source alone.
    copy["config"]["limits.cpu"] = "99"
    assert host.lxd_instances[0]["config"]["limits.cpu"] == "2"
    await send_command(
        host, LxdCopyCommand(instance="web1", dest_instance="web3", instance_only=True)
    )
    assert host.lxd_instance_snapshots["web3"] == []
    await send_command(
        host, LxdCopyCommand(instance="web1", dest_instance="web4", stateless=True)
    )
    assert next(e for e in host.lxd_instances if e["name"] == "web4")["status"] == "Stopped"


async def test_image_and_alias_arms_keep_both_views_consistent():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        LxdImageCommand(
            image="ubuntu/24.04",
            source="images:ubuntu/24.04",
            alias="ubuntu/24.04",
            project="default",
        ),
    )
    (image,) = host.lxd_images
    assert image["fingerprint"] == COPY_FP
    assert host.lxd_image_aliases == [
        {"name": "ubuntu/24.04", "target": COPY_FP, "type": "image", "description": ""}
    ]
    # The cached image makes the operation a no-op on the next read.
    ctx, _commands = await materialize(host, lxd.image, image="ubuntu/24.04")
    assert not ctx.changed
    # Repoint: delete + create keeps the listing and the image's own
    # alias array consistent.
    await send_command(host, LxdImageAliasCommand(alias="ubuntu/24.04", present=False))
    await send_command(
        host, LxdImageAliasCommand(alias="ubuntu/24.04", fingerprint=FP)
    )
    assert host.lxd_image_aliases[0]["target"] == FP
    # Deleting the image cascades ONLY the aliases targeting it: the
    # repointed alias targets FP, so it survives the COPY_FP delete.
    await send_command(host, LxdImageCommand(image=COPY_FP, present=False))
    assert host.lxd_images == []
    assert host.lxd_image_aliases[0]["target"] == FP
    await send_command(host, LxdImageAliasCommand(alias="ubuntu/24.04", present=False))
    assert host.lxd_image_aliases == []


async def test_network_project_pool_and_volume_arms():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        LxdNetworkCommand(network="lxdbr1", config=("ipv4.nat=true",), project="default"),
    )
    assert host.lxd_networks[0]["name"] == "lxdbr1"
    assert host.lxd_networks[0]["managed"] is True
    await send_command(
        host, LxdProjectCommand(project="apps", config=("features.images=true",))
    )
    assert host.lxd_projects[0]["name"] == "apps"
    # The per-project config document is populated alongside the listing.
    assert host.lxd_project_configs["apps"]["config"]["features.images"] == "true"
    await send_command(host, LxdStoragePoolCommand(pool="fast", driver="zfs"))
    assert host.lxd_storage_pools[0]["driver"] == "zfs"
    await send_command(
        host,
        LxdStorageVolumeCommand(pool="fast", volume="data", config=("size=10GiB",)),
    )
    assert host.lxd_storage_volumes["fast"][0]["type"] == "custom"
    await send_command(host, LxdStorageVolumeCommand(pool="fast", volume="data", present=False))
    assert host.lxd_storage_volumes["fast"] == []
    # Deleting a pool drops its volume store entirely.
    await send_command(host, LxdStoragePoolCommand(pool="fast", present=False))
    assert "fast" not in host.lxd_storage_volumes
    await send_command(host, LxdNetworkCommand(network="lxdbr1", present=False))
    assert host.lxd_networks == []
    await send_command(host, LxdProjectCommand(project="apps", present=False))
    assert host.lxd_projects == []
    assert "apps" not in host.lxd_project_configs


async def test_operation_arms_prune_the_id_without_awaiting_wait():
    # send_command dispatches the execute_command seam FIRST, even for a
    # WaitCommand: the wait arm completes the operation synchronously
    # instead of polling, so no real time passes in the test.
    op_id = OPERATION_ENTRY["id"]
    host = SimulatedHost(name="h", lxd_operations=[OPERATION_ENTRY])
    await send_command(host, LxdOperationCancelCommand(operation_id=op_id))
    assert host.lxd_operations == []
    host.lxd_operations = [OPERATION_ENTRY]
    await send_command(host, LxdOperationWaitCommand(operation_id=op_id))
    assert host.lxd_operations == []


async def test_instance_delete_arm_cascades_every_view():
    host = SimulatedHost(
        name="h",
        lxd_storage_volumes={
            "default": [
                {"name": "web1", "type": "container", "config": {}},
                {"name": "data", "type": "custom", "config": {}},
            ]
        },
    )
    await send_command(host, LxdLaunchCommand(image="ubuntu:24.04", instance="web1"))
    await send_command(host, LxdDeleteCommand(instance="web1"))
    assert host.lxd_instances == []
    assert host.lxd_containers == []
    assert "web1" not in host.lxd_instance_configs
    assert "web1" not in host.lxd_instance_states
    assert "web1" not in host.lxd_instance_snapshots
    # The instance volume goes, the custom volume stays.
    assert host.lxd_storage_volumes["default"] == [
        {"name": "data", "type": "custom", "config": {}}
    ]


# --- end to end (run / run_prepare + run_apply) ----------------------------------


async def test_lxd_instance_end_to_end_create_then_noop():
    def deploy(host):
        yield op(lxd.instance, name="Create web1", instance="web1", sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the create

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    assert host.lxd_instances[0]["name"] == "web1"
    assert "$ sudo lxc launch images:ubuntu/24.04 web1 --project default < /dev/null" in (
        format_log(report)
    )

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "lxd instance web1 already exists as configured"


async def test_lxd_dry_run_sends_nothing():
    def deploy(host):
        yield op(lxd.storage_pool, name="Create the fast pool", pool="fast", sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (LxdStoragePoolCommand(pool="fast"),)
    assert host.lxd_storage_pools == []
    assert "changed" in format_plan(plans)


async def test_lxd_create_then_noop_in_one_deploy():
    # Prepare never sends, so both identical creates plan as changes;
    # apply's fresh per-step fact cache sees the first create.
    def deploy(host):
        yield op(lxd.instance, name="create", instance="web1")
        yield op(lxd.instance, name="re-create", instance="web1")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == "lxd instance web1 already exists as configured"


async def test_lxd_determinism_violation_is_a_hard_error():
    def nondeterministic_deploy(host):
        yield op(lxd.instance, name="Create web1", instance="web1")
        if host.lxd_instances:  # mutable-fact branch: forbidden
            yield op(lxd.instance_state, name="stop", instance="web1", state="stopped")
        else:
            yield op(lxd.storage_pool, name="pool", pool="fast")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], nondeterministic_deploy)
    with pytest.raises(DeterminismError, match="diverges"):
        await run_apply(runtimes, plans=plans)


async def test_lxd_provisioning_pipeline_in_lockstep():
    # project -> profile -> pool -> network -> volume -> instance, each
    # step's apply seeing the previous step's mutation through the fresh
    # fact cache (the launch expands the just-created profile).
    def deploy(host):
        yield op(
            lxd.project, name="Create the apps project", project="apps",
            config=("features.images=true", "features.profiles=true"), sudo=True,
        )
        yield op(
            lxd.profile, name="Create the web profile", profile="web",
            config=("limits.memory=2GB",), sudo=True,
        )
        yield op(lxd.storage_pool, name="Create the fast pool", pool="fast", sudo=True)
        yield op(
            lxd.network, name="Create lxdbr1", network="lxdbr1",
            config=("ipv4.nat=true",), sudo=True,
        )
        yield op(
            lxd.storage_volume, name="Create the data volume", pool="fast",
            volume="data", config=("size=10GiB",), sudo=True,
        )
        yield op(
            lxd.instance, name="Create web1", instance="web1",
            profiles=("web",), config=("limits.cpu=2",), sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    assert [p.changed for p in plans["h"]] == [True] * 6
    report = await run_apply(runtimes, plans=plans)
    assert [entry.changed for entry in report.log] == [True] * 6
    assert host.lxd_projects[0]["name"] == "apps"
    assert host.lxd_profiles[0]["config"]["limits.memory"] == "2GB"
    assert host.lxd_storage_pools[0]["name"] == "fast"
    assert host.lxd_networks[0]["name"] == "lxdbr1"
    assert host.lxd_storage_volumes["fast"][0]["name"] == "data"
    (instance,) = host.lxd_instances
    assert instance["profiles"] == ["web"]
    # The launch expanded the profile created earlier in the SAME apply.
    assert host.lxd_instance_configs["web1"]["expanded_config"]["limits.memory"] == "2GB"


async def test_lxd_lifecycle_pipeline_on_a_seeded_instance():
    # instance_state and instance_snapshot require an existing instance
    # in prepare, so the lifecycle steps run against seeded state.
    def deploy(host):
        yield op(
            lxd.instance_state, name="Stop web1", instance="web1",
            state="stopped", sudo=True,
        )
        yield op(
            lxd.instance_snapshot, name="Snapshot web1", instance="web1",
            snapshot="snap1", sudo=True,
        )

    host = SimulatedHost(
        name="h",
        lxd_instances=[INSTANCE_ENTRY],
        lxd_instance_states={"web1": STATE_DOC},
    )
    runtimes, plans = await run_prepare([host], deploy)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [entry.changed for entry in report.log] == [True, True]
    assert host.lxd_instance_states["web1"]["status"] == "Stopped"
    assert host.lxd_instance_snapshots["web1"][0]["name"] == "snap1"


async def test_lxd_operation_end_to_end_cancel_then_wait():
    op_id = OPERATION_ENTRY["id"]

    def deploy(host):
        yield op(lxd.operation, name="Cancel the migration", operation_id=op_id, cancel=True)

    host = SimulatedHost(name="h", lxd_operations=[OPERATION_ENTRY])
    runtimes, plans = await run_prepare([host], deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert report.log[0].changed
    # The cancel pruned the id; the wait arm completed synchronously.
    assert host.lxd_operations == []