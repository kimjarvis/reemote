# lxd module

## lxd Facts

::: reemote5.modules.lxd.lxd
    options:
      show_root_heading: false
      members:
        - LxdContainers
        - LxdImageAliases
        - LxdImages
        - LxdInstanceConfig
        - LxdInstanceSnapshots
        - LxdInstanceState
        - LxdInstances
        - LxdNetworks
        - LxdOperations
        - LxdProfiles
        - LxdProjectConfig
        - LxdProjects
        - LxdServerInfo
        - LxdServerResources
        - LxdStoragePools
        - LxdStorageVolumes

## lxd Returns

::: reemote5.modules.lxd.lxd
    options:
      show_root_heading: false
      members:
        - LxdContainer
        - LxdImage
        - LxdImageAlias
        - LxdInstance
        - LxdInstanceStateData
        - LxdNetwork
        - LxdOperation
        - LxdProfile
        - LxdProject
        - LxdServerInfoData
        - LxdServerResourcesData
        - LxdSnapshot
        - LxdStoragePool
        - LxdStorageVolume

## lxd Operations

::: reemote5.modules.lxd.lxd
    options:
      show_root_heading: false
      members:
        - container
        - image
        - image_alias
        - instance
        - instance_config
        - instance_copy
        - instance_snapshot
        - instance_state
        - network
        - operation
        - profile
        - project
        - storage_pool
        - storage_volume




<!---

`reemote5.modules.lxd.lxd` combines the LXD facts and operations in a
single source, extending pyinfra's one-fact/one-operation `lxd` namespace
(`lxd.LxdContainers` / `lxd.container`) toward complete control over and
visibility into the LXD daemon. Every fact and operation runs through the
`lxc` client on the target host with `--format json`, so the daemon's
asynchronous mutations (launch, state changes, copies, image downloads)
are waited on by the CLI itself — no REST polling is modelled in the
library. Collections are project-scoped through an explicit `project`
option (default `"default"`), and a `remote` option addresses any remote
configured in the host's `lxc remote` list; the parity pair
(`LxdContainers` / `container`) keeps upstream's unscoped shells.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get]. The server-wide facts
(`LxdServerInfo`, `LxdServerResources`, `LxdProjects`, `LxdProjectConfig`,
`LxdStoragePools`, `LxdOperations`) take only `remote`; the
project-scoped listings (`LxdImages`, `LxdImageAliases`, `LxdInstances`,
`LxdNetworks`, `LxdProfiles`, `LxdStorageVolumes`) and the per-instance
reads (`LxdInstanceConfig`, `LxdInstanceState`, `LxdInstanceSnapshots`)
also take `project`.

`LxdContainers` mirrors pyinfra's `lxd.LxdContainers` verbatim: the same
`lxc list --format json --fast` command and the same passed-through
entries, with empty output hardened to `[]` instead of upstream's
assert-on-one-line crash.

The fifteen reemote5-original facts answer the visibility gaps pyinfra
leaves: `LxdServerInfo` (daemon metadata and the `api_extensions`
capability list a deploy can branch on before using extension-dependent
features), `LxdServerResources` (CPU/memory/GPU/storage/network hardware
from `lxc info --resources`), `LxdProjects` and `LxdProjectConfig`
(project listings and one project's configuration document),
`LxdInstances` (the FULL `lxc list` — config, devices, profiles and the
`volatile.base_image` fingerprint, VMs alongside containers),
`LxdInstanceConfig` (`lxc config show --expanded`: own plus
profile-inherited configuration), `LxdInstanceState` (runtime status,
PID, memory and network from `lxc info <instance>`),
`LxdInstanceSnapshots` (the snapshots array projected from the same
`lxc info` document), `LxdImages` and `LxdImageAliases` (the image store
and its alias-to-fingerprint mappings), `LxdNetworks` (managed and
unmanaged networks), `LxdProfiles` (profiles with config and devices),
`LxdStoragePools` and `LxdStorageVolumes` (pools and their custom plus
instance-managed volumes), and `LxdOperations` (the background operations
currently registered on the daemon). The guarded single-target reads
(`2>/dev/null || true`) report a missing instance, project or pool as
`None`/`[]` rather than failing the deploy.

## Operations

An operation is an async generator: it reads facts through
[`FactCache.get`][reemote5.core.FactCache.get], decides whether a change
is needed, records `ctx.changed`/`ctx.reason`, and yields one typed
command descriptor per mutation — never touching the host itself. Reach
operations through the namespace (`lxd.instance`), not the top-level
package.

`lxd.container` keeps pyinfra's name, options and shells 1:1 (including
the stop-then-delete sequence for a running container and its documented
limitation of not checking an existing container's base image).

The thirteen reemote5-original operations close the control gaps:
`lxd.instance` is the comprehensive successor — create containers or VMs
with profiles/config/devices/ephemeral, reconcile an existing instance
NON-DESTRUCTIVELY (assign profiles, set drifted config keys, remove and
re-add drifted devices), and REPORT base-image drift in the reason only
(the developer-approved decision: a mismatch never recreates the
instance; a container/VM type mismatch raises
[`OperationError`][reemote5.core.OperationError]).
`lxd.instance_state` drives started/stopped/frozen idempotently against
`LxdInstanceState` (restarted/unfrozen are stateless actions that always
yield), `lxd.instance_config` reconciles an existing instance's own
config/devices/profiles, `lxd.instance_snapshot` creates/deletes
snapshots and restores statelessly, `lxd.instance_copy` copies locally
(idempotent on the destination's existence) or cross-remote (stateless).
`lxd.image` caches an image from a source remote or deletes it by
fingerprint/alias, `lxd.image_alias` points an alias at a fingerprint
(repointing deletes and recreates — the CLI has no repoint verb).
`lxd.project`, `lxd.network`, `lxd.storage_pool` and `lxd.storage_volume`
manage their resources idempotently (creation-time config, then per-key
drift sets; immutable type/driver changes raise `OperationError`, an
unmanaged network interface is never adopted or deleted, and only CUSTOM
volumes are managed). `lxd.profile` creates a profile then sets/attaches
the requested config and devices, reconciling only the drifted entries.
`lxd.operation` is the lower-level primitive for out-of-band mutations:
cancel a registered background operation and/or wait for its id to
disappear from `LxdOperations` (the wait always yields — a wait is an
action, not a desired state).

## Example

The read-only fact-gathering scripts `lxd_lxd_containers.py`,
`lxd_lxd_image_aliases.py`, `lxd_lxd_images.py`,
`lxd_lxd_instance_config.py`, `lxd_lxd_instance_snapshots.py`,
`lxd_lxd_instance_state.py`, `lxd_lxd_instances.py`,
`lxd_lxd_networks.py`, `lxd_lxd_operations.py`, `lxd_lxd_profiles.py`,
`lxd_lxd_project_config.py`, `lxd_lxd_projects.py`,
`lxd_lxd_server_info.py`, `lxd_lxd_server_resources.py`,
`lxd_lxd_storage_pools.py` and `lxd_lxd_storage_volumes.py` under
[`examples/modules/lxd/`](../examples/modules/lxd.md) gather the sixteen
facts respectively across an inventory with
[`gather_fact`][reemote5.drivers.gather_fact] and print each host's
result as JSON.

The operation scripts (`lxd_container.py`, `lxd_image.py`,
`lxd_image_alias.py`, `lxd_instance.py`, `lxd_instance_config.py`,
`lxd_instance_copy.py`, `lxd_instance_snapshot.py`,
`lxd_instance_state.py`, `lxd_network.py`, `lxd_operation.py`,
`lxd_profile.py`, `lxd_project.py`, `lxd_storage_pool.py`,
`lxd_storage_volume.py`) under the same directory drive each operation
through the two-phase [`run`][reemote5.drivers.run] — a dry-run plan then
a real apply — over an inventory, yielding the same request twice so the
second is a guaranteed apply-phase no-op where the operation is
idempotent.

--->