# Module examples: lxd

Scripts under `examples/modules/lxd/` — one per fact or operation of
the [`lxd`](../../modules/lxd.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

Every script reads the shared project-root `inventory.json` and guards on
the `"centos"` group (`is_centos` on every operation request, a host-list
filter before each `gather_fact` fan-out), targeting the two hosts
web1/web2, which the scripts assume run an initialized LXD daemon. The
operation requests ride
under `sudo=True`, so the `lxc` client talks to the daemon socket as root
— no password travels through operation arguments, plans or logs. The
fact-gathering scripts read as the login user, who needs access to the
daemon socket (member of the `lxd` group) for the reads to succeed.

The read-only fact-gathering examples (`lxd_lxd_containers.py` reading
`LxdContainers`, `lxd_lxd_image_aliases.py` reading `LxdImageAliases`,
`lxd_lxd_images.py` reading `LxdImages`, `lxd_lxd_instance_config.py`
reading `LxdInstanceConfig`, `lxd_lxd_instance_snapshots.py` reading
`LxdInstanceSnapshots`, `lxd_lxd_instance_state.py` reading
`LxdInstanceState`, `lxd_lxd_instances.py` reading `LxdInstances`,
`lxd_lxd_networks.py` reading `LxdNetworks`, `lxd_lxd_operations.py`
reading `LxdOperations`, `lxd_lxd_profiles.py` reading `LxdProfiles`,
`lxd_lxd_project_config.py` reading `LxdProjectConfig`,
`lxd_lxd_projects.py` reading `LxdProjects`, `lxd_lxd_server_info.py`
reading `LxdServerInfo`, `lxd_lxd_server_resources.py` reading
`LxdServerResources`, `lxd_lxd_storage_pools.py` reading
`LxdStoragePools`, and `lxd_lxd_storage_volumes.py` reading
`LxdStorageVolumes`, each over `gather_fact`) are not rendered here —
they read a single fact, and the facts themselves are documented on the
[`lxd` module page](../../modules/lxd.md). The per-instance reads target
an instance named `web1` (create it with `lxd_instance.py` first), the
project read targets `default`, and the volume read targets the `fast`
pool (create it with `lxd_storage_pool.py` first); a missing target
reads as `None`/`[]` rather than failing the gather.

**Host prerequisite.** None of these examples can run until LXD is
installed and initialized on the target: the `lxc` CLI must be present,
the daemon running (`lxd init` having created a storage pool and network),
and the login user a member of the `lxd` group for the fact reads. The
demo CentOS Stream 9 hosts (web1/web2) have **no LXD at all** — every
script currently fails with `lxc: command not found` (exit 127). LXD is
not packaged for EL9/EPEL; the only `lxc`-providing route is the snap
(`sudo dnf install -y snapd`, `sudo systemctl enable --now snapd.socket`,
`sudo ln -s /var/lib/snapd/snap /snap`, `sudo snap install lxd`, then
`sudo lxd init --auto` and `sudo usermod -aG lxd <user>`). On the demo
hosts that recipe is blocked by the same SELinux `staff_t`-confined sudo
the `selinux`, `sysctl`, `systemd`, `dnf.key` and `gpg` examples document:
snapd installs and its socket activates, but the confined sudo can
neither create `/snap` (Permission denied) nor connect to
`/run/snapd.socket` (`snap install lxd` fails with "dial unix
/run/snapd.socket: connect: permission denied"). The scripts themselves
are correct — `sudo=True` on every operation, the `lxc --format json` CLI
throughout — so they are compile-checked and exercised by the offline
example harness rather than run against the shared hosts. Relax the demo
hosts' sudo to an unconfined domain (or set SELinux permissive) and
install + initialize LXD, or point each script's `INVENTORY_PATH` at your
own file of LXD-enabled machines, to execute them for real.

## Managing a container (baseline parity)

The container operation: `lxd.container` mirrors the baseline's
`lxd.container` 1:1 — same `id`/`present`/`image` options, same
[`LxdContainers`][reemote5.modules.lxd.lxd.LxdContainers] fact read, same
`lxc launch <image> <id> < /dev/null` create shell and the same
stop-then-delete sequence for a running container. Upstream's documented
limitation stands for THIS operation: it does not check an existing
container's base image — `lxd.instance` below is the comprehensive
successor. The script yields the same create request twice — the second
is a guaranteed no-op in the apply phase, because the first launch is
visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes).

::: lxd_container
    options:
      members: []

## Managing an instance

The instance operation: `lxd.instance` is the reemote5-original
successor to `lxd.container`. It reads
[`LxdInstances`][reemote5.modules.lxd.lxd.LxdInstances] to decide: an
absent instance yields one `lxc launch` carrying the type
(container/VM), profiles, config, devices and the ephemeral flag; an
existing one is reconciled NON-DESTRUCTIVELY — profiles assigned,
drifted config keys set, drifted devices removed and re-added. Base-image
drift is resolved through
[`LxdImages`][reemote5.modules.lxd.lxd.LxdImages] and REPORTED in the
reason only, never recreated (destructive recreation stays an explicit
`present=False` choice); a container/VM type mismatch raises
[`OperationError`][reemote5.core.OperationError]. The script yields the
same create request twice — the second is a guaranteed no-op in the
apply phase, because the first launch is visible to it through the fresh
per-step fact cache (in the prepare phase, where nothing is sent, both
plan as changes).

::: lxd_instance
    options:
      members: []

## Driving an instance's lifecycle state

The state operation: `lxd.instance_state` drives
started/stopped/frozen idempotently against
[`LxdInstanceState`][reemote5.modules.lxd.lxd.LxdInstanceState] — the
CLI verb is yielded only when the status differs from the target.
`restarted` and `unfrozen` are STATELESS actions that always yield (a
restart is an action, not a desired state). The instance must already
exist — a missing one raises
[`OperationError`][reemote5.core.OperationError] in prepare, so create it
with `lxd_instance.py` first. The script yields the same stop request
twice: on a running instance the second is a guaranteed no-op in the
apply phase, because the first stop is visible to it through the fresh
per-step fact cache; on an already-stopped instance both plan as no-ops.

::: lxd_instance_state
    options:
      members: []

## Updating an instance's configuration

The config operation: `lxd.instance_config` is the dedicated
reconciliation half of `lxd.instance` for an instance that must already
exist. It reads
[`LxdInstanceConfig`][reemote5.modules.lxd.lxd.LxdInstanceConfig] and
compares desired keys against the instance's OWN config — an inherited
profile value is not an own key, so setting it IS a change; drifted
devices are removed and re-added, and a `profiles` tuple is assigned
wholesale when it differs (`None` leaves the current list alone). Config
keys are only SET, never unset. A missing instance raises
[`OperationError`][reemote5.core.OperationError] in prepare, so create it
with `lxd_instance.py` first. The script yields the same set request
twice — the second is a guaranteed no-op in the apply phase, because the
first set is visible to it through the fresh per-step fact cache (in the
prepare phase, where nothing is sent, both plan as changes).

::: lxd_instance_config
    options:
      members: []

## Managing an instance snapshot

The snapshot operation: `lxd.instance_snapshot` reads
[`LxdInstanceSnapshots`][reemote5.modules.lxd.lxd.LxdInstanceSnapshots]:
an absent snapshot is created (`stateful=True` includes the runtime
state), an unwanted one deleted through the `instance/snapshot` name
form. `restore=True` is STATELESS and always yields `lxc restore` — stop
the instance first for a non-stateful restore. The instance must already
exist (the CLI fails the snapshot at apply time otherwise), so create it
with `lxd_instance.py` first. The script yields the same create request
twice — the second is a guaranteed no-op in the apply phase, because the
first snapshot is visible to it through the fresh per-step fact cache
(in the prepare phase, where nothing is sent, both plan as changes).

::: lxd_instance_snapshot
    options:
      members: []

## Copying an instance

The copy operation: `lxd.instance_copy` copies an instance to a new
name, locally or to another configured remote. On the SAME daemon it is
idempotent on the DESTINATION's existence — an existing destination is a
no-op (no refresh semantics; delete it first to re-copy). A CROSS-REMOTE
copy cannot read the destination's fact through this host's CLI scope,
so it is STATELESS and always yields; the CLI itself fails if the
destination name is taken. `instance_only=True` drops the snapshots,
`stateless=True` drops the runtime state. The source must already exist
(the CLI fails the copy at apply time otherwise), so create it with
`lxd_instance.py` first. The script yields the same local copy request
twice — the second is a guaranteed no-op in the apply phase, because the
first copy's destination is visible to it through the fresh per-step
fact cache (in the prepare phase, where nothing is sent, both plan as
changes).

::: lxd_instance_copy
    options:
      members: []

## Caching an image

The image operation: `lxd.image` reads
[`LxdImages`][reemote5.modules.lxd.lxd.LxdImages]: an image already
cached by fingerprint OR alias is a no-op; an absent one is copied from
`source` (e.g. `images:ubuntu/24.04` — the CLI waits on the asynchronous
download natively), adding `--alias` when the reference is an alias
rather than a fingerprint. `present=False` deletes by the given
reference. The script yields the same cache request twice — the second
is a guaranteed no-op in the apply phase, because the first copy is
visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes).

::: lxd_image
    options:
      members: []

## Managing an image alias

The alias operation: `lxd.image_alias` reads
[`LxdImageAliases`][reemote5.modules.lxd.lxd.LxdImageAliases]: an alias
already targeting the desired fingerprint is a no-op; one targeting a
DIFFERENT fingerprint is deleted and recreated (the CLI has no
alias-repoint verb). `present=False` removes the alias only — the image
itself stays cached. The script's `FINGERPRINT` constant is a
placeholder: replace it with a cached image's fingerprint (inspect the
store with `lxd_lxd_images.py` first). It yields the same create request
twice — the second is a guaranteed no-op in the apply phase, because the
first alias is visible to it through the fresh per-step fact cache (in
the prepare phase, where nothing is sent, both plan as changes).

::: lxd_image_alias
    options:
      members: []

## Managing a project

The project operation: `lxd.project` reads
[`LxdProjects`][reemote5.modules.lxd.lxd.LxdProjects] for existence and
[`LxdProjectConfig`][reemote5.modules.lxd.lxd.LxdProjectConfig] for
config drift: an absent project is created with its `-c` config flags,
an existing one gets per-key sets (features and limits are mutable after
creation). `present=False` deletes the project — the CLI fails while it
is still in use, and the `default` project cannot be deleted. The script
yields the same create request twice — the second is a guaranteed no-op
in the apply phase, because the first create is visible to it through
the fresh per-step fact cache (in the prepare phase, where nothing is
sent, both plan as changes).

::: lxd_project
    options:
      members: []

## Managing a profile

The profile operation: `lxd.profile` reads
[`LxdProfiles`][reemote5.modules.lxd.lxd.LxdProfiles]: an absent profile
is created empty, then the requested config keys and devices are
attached; an existing one gets only the drifted entries set or
re-attached. `present=False` deletes the profile. The script yields the
same create request twice — the second is a guaranteed no-op in the
apply phase, because the first create and its config/device sets are
visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes).

::: lxd_profile
    options:
      members: []

## Managing a network

The network operation: `lxd.network` reads
[`LxdNetworks`][reemote5.modules.lxd.lxd.LxdNetworks]: an absent network
is created with its `--type` and creation-time config, an existing one
gets per-key config sets. A type mismatch raises
[`OperationError`][reemote5.core.OperationError] (LXD cannot change a
network's type), and an UNMANAGED interface (a host NIC LXD merely knows
about) raises rather than being adopted or deleted. `present=False`
deletes the managed network. The script yields the same bridge create
request twice — the second is a guaranteed no-op in the apply phase,
because the first create is visible to it through the fresh per-step
fact cache (in the prepare phase, where nothing is sent, both plan as
changes).

::: lxd_network
    options:
      members: []

## Managing a storage pool

The pool operation: `lxd.storage_pool` reads
[`LxdStoragePools`][reemote5.modules.lxd.lxd.LxdStoragePools]: an absent
pool is created with its driver and creation-time config, an existing
one gets per-key config sets. A driver mismatch raises
[`OperationError`][reemote5.core.OperationError] (LXD cannot change a
pool's driver). `present=False` deletes the pool — the CLI fails while
volumes still live there. Pools are server-wide: no project option. The
script yields the same `dir` pool create request twice — the second is a
guaranteed no-op in the apply phase, because the first create is visible
to it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

::: lxd_storage_pool
    options:
      members: []

## Managing a storage volume

The volume operation: `lxd.storage_volume` reads
[`LxdStorageVolumes`][reemote5.modules.lxd.lxd.LxdStorageVolumes] and is
idempotent on EXISTENCE: an absent custom volume is created with its
config; an existing one is left alone — config is creation-only. Only
CUSTOM volumes are managed: a name collision with an instance/image
volume raises [`OperationError`][reemote5.core.OperationError].
`present=False` deletes the custom volume (the CLI fails while it is
still attached). The pool must already exist, so create it with
`lxd_storage_pool.py` first. The script yields the same create request
twice — the second is a guaranteed no-op in the apply phase, because the
first create is visible to it through the fresh per-step fact cache (in
the prepare phase, where nothing is sent, both plan as changes).

::: lxd_storage_volume
    options:
      members: []

## Waiting on a background operation

The operation primitive: `lxd.operation` is the lower-level handle for
out-of-band mutations (image downloads, migrations started elsewhere).
The cancel half is idempotent against
[`LxdOperations`][reemote5.modules.lxd.lxd.LxdOperations] — a registered
id yields `lxc operation delete`, an already-pruned one is a no-op. The
wait half is STATELESS and always yields (a wait is an action, not a
desired state): it polls until the id disappears from the operation
list, and an already-finished operation succeeds on the first poll. The
script's `OPERATION_ID` constant is a placeholder: replace it with a
background operation's UUID (inspect the list with
`lxd_lxd_operations.py` first) — or leave it, since waiting on an id
that is no longer registered succeeds at once. Both yielded requests
wait; neither is a no-op, because a wait always runs.

::: lxd_operation
    options:
      members: []