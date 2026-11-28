# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``minio`` role (design doc section 17.7, T5).

The role is imported (``from reemote.roles import minio``) and replayed against
``SimulatedHost`` through the real wave driver. Two assertions carry the
migration's claims, as in ``test_apache.py``:

* **convergence** -- the second pass reports no change anywhere and its
  checkpoint notifies nobody, so the restart handler and the console-port wait
  disappear. The ``mc`` shells still re-run (D9: an ``act`` result is data,
  exactly like the role's non-idempotent ``shell`` tasks), which is why the
  convergence claim is about the apply log, not about the barrier count.
* **placement** -- the D4 gates: bucket ops run per host only in standalone
  mode, user and sync ops only on the primary, and the RedHat host emits
  nothing at all.

Seeding notes:

* the D5 package arrives as a controller-side temp file (the real tarball
  cannot ship in the repo); the extraction itself rides the ``archive_members``
  seam, so the members ``minio``/``mc`` materialize without a tar parser.
* every ``mc`` shell is seeded under its exact rendered key -- the ``sudo ``
  prefix the elevated Shell action adds, around the ``sudo -u minio -H``
  embedding of D6. The expected scripts are built with the role's own pure
  script helpers, so a rename in the role breaks the test, not the seed.
* the console-port wait reads the ``listening_ports`` seam once (no polling in
  the simulation), so the seed opens it from the start.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.modules.server import PortInfo
from reemote.roles import MinioOutputs, MinioVars, minio
from reemote.roles.minio import (
    ENV_PATH,
    GETPUT_POLICY_JSON,
    PACKAGE_SPOOL,
    UNIT_PATH,
    MinioPlan,
    ResolvedPolicy,
    SyncSpec,
    alias_set_script,
    bucket_script,
    policy_create_script,
    render_sync_units,
    sync_config_script,
    user_script,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

BASE = "/data/minio"
INSTALL_DIR = f"{BASE}/bin"
CONFIG_DIR = f"{BASE}/cfg"
DATA_DIR = f"{BASE}/data"
LOG_DIR = f"{BASE}/log"
BACKUP_DIR = "/opt/minio"
MC = f"{INSTALL_DIR}/mc"
CONSOLE_PORT = 9001

SYNC_UNIT = "minio_sh_bucket01_to_wx_bucket03.service"
SYNC_UNIT_PATH = f"/etc/systemd/system/{SYNC_UNIT}"
POLICY_SPOOL = "/tmp/getput_policy.json"

#: The wave-0 request names, in stream order.
INSTALL_NAMES = (
    "Create group if not exists",
    "Create user if not exists",
    f"Create dirs. {INSTALL_DIR}",
    f"Create dirs. {CONFIG_DIR}",
    f"Create dirs. {DATA_DIR}",
    f"Create dirs. {LOG_DIR}",
    f"Create dirs. {BACKUP_DIR}",
    "Spool minio.tar.gz",
    "Unpack minio.tar.gz",
)
CONFIGURE_NAMES = ("Add environment file", "Add systemd file", "Start minio service")
HANDLER_NAMES = (
    "Restart minio service (handler)",
    f"Waiting for minio console port {CONSOLE_PORT}",
)
#: The request names inside the act-gated feature waves (barriers carry no log
#: entry; these are the requests that ride the same waves as the ``mc`` acts).
FEATURE_NAMES = (
    "Copy policy file getput_policy.json",
    f"Render systemd file {SYNC_UNIT}",
    f"Start synchronize service {SYNC_UNIT}",
)
#: The six ``mc`` act barriers the primary standalone/cluster host runs.
ACT_NAMES = (
    "Add minio config",
    "Create bucket bucket01",
    "Create bucket bucket02",
    "Create policy getput",
    "Create user miniouser",
    "Add synchronize config",
)

# The provisioning specs, as models and as the inventory mappings that bind them.
BUCKET01 = {"name": "bucket01", "expire_days": 365}
BUCKET02 = {"name": "bucket02"}
USER = {"username": "miniouser", "password": "miniouser"}
SYNC = {
    "upload_limit": "10MiB",
    "source": {"area_label": "sh"},
    "target": {
        "area_label": "wx",
        "host": "127.0.0.1",
        "port": 9000,
        "access_key": "minioadmin",
        "secret_key": "minioadmin",
    },
    "bucket_mapping": [{"local": "bucket01", "remote": "bucket03"}],
}


def make_plan(address: str) -> MinioPlan:
    """A plan carrying exactly what the pure script helpers read.

    Built with the role's own :class:`MinioPlan` so the seeded shell keys are
    the byte-for-byte scripts the body will render for that address.
    """
    return MinioPlan(
        sys_user="minio",
        sys_group="minio",
        admin_user="minioadmin",
        admin_pass="minioadmin",
        service_port=9000,
        console_port=CONSOLE_PORT,
        cluster_mode=True,
        install_dir=INSTALL_DIR,
        config_dir=CONFIG_DIR,
        data_dir=DATA_DIR,
        log_dir=LOG_DIR,
        backup_dir=BACKUP_DIR,
        mc=MC,
        alias_url=f"http://{address}:9000",
        env_content="",
        unit_content="",
        package_url=None,
        package_content=None,
        sync_units=(),
        policies=(
            ResolvedPolicy(
                name="getput", file="getput_policy.json", content=GETPUT_POLICY_JSON
            ),
        ),
    )


def expected_scripts(address: str) -> list[str]:
    """Every ``mc`` script the full-feature primary host runs, in stream order."""
    plan = make_plan(address)
    from reemote.roles.minio import BucketSpec, MinioUserSpec

    sync = SyncSpec.model_validate(SYNC)
    return [
        alias_set_script(plan),
        bucket_script(plan, BucketSpec.model_validate(BUCKET01)),
        bucket_script(plan, BucketSpec.model_validate(BUCKET02)),
        policy_create_script(plan, plan.policies[0]),
        user_script(plan, MinioUserSpec.model_validate(USER), ("getput",)),
        sync_config_script(plan, sync),
    ]


def minio_entry_vars(package_path: str, address: str, **overrides: Any) -> dict[str, Any]:
    """The ``roles.minio`` mapping: full features, this host's node address."""
    entry: dict[str, Any] = {
        "cluster_mode": True,
        "node_address": address,
        "package_path": package_path,
        "buckets": [BUCKET01, BUCKET02],
        "users": [USER],
        "sync": [SYNC],
    }
    entry.update(overrides)
    return entry


def make_inventory(package_path: str, **overrides: Any) -> list[dict[str, Any]]:
    """A three-host inventory: primary, non-primary peer, off-family host.

    ``ubuntu`` is the play's primary and carries every feature; ``node2``
    carries the same feature lists but ``primary: False``, so the D4 gates are
    what silence it; ``web1`` is RedHat material for the family guard.
    """
    return [
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {
                "address": "192.0.2.10",
                "username": "supervisor",
                "key_path": "~/.ssh/id_ed25519",
            },
            "group": "ubuntu",
            "roles": {"minio": minio_entry_vars(package_path, "192.0.2.10", **overrides)},
        },
        {
            "host": "node2",
            "family": "Ubuntu",
            "connection": {"address": "192.0.2.20", "username": "supervisor"},
            "group": "ubuntu",
            "roles": {
                "minio": minio_entry_vars(
                    package_path, "192.0.2.20", primary=False, **overrides
                )
            },
        },
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def seed(host: SimulatedHost, *, shells: bool = True, address: str = "192.0.2.10") -> None:
    """Pre-state: a stock Ubuntu machine with nothing minio-related installed.

    The package members, the open console port and every seeded ``mc`` script
    (under its elevated rendering: ``sudo `` + the verbatim script) are the
    only bridges -- the stores the role mutates itself converge on their own.
    """
    host.archive_members = {PACKAGE_SPOOL: ("minio", "mc")}
    host.listening_ports = {
        ("tcp", CONSOLE_PORT): PortInfo(listening=True, process="minio", pid=4242)
    }
    host.login_path_visible = lambda path: True
    if shells:
        for script in expected_scripts(address):
            host.shell_results[f"sudo {script}"] = {
                "exit_status": 0,
                "stdout": "Done.\n",
                "stderr": "",
            }


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    shells: bool = True,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts.

    ``node2`` is seeded without shells when the cluster gates should keep it
    silent: an act on the peer would raise ``UnseededShellResult``.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(
                host,
                shells=shells and entry["host"] == "ubuntu",
                address=str(entry["connection"]["address"]),
            )
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts."""
    v = resolve_vars(MinioVars, "minio", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, minio(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "ubuntu") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


# --- the first pass deploys the whole role ------------------------------------


async def test_first_pass_installs_and_provisions(tmp_path):
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The service account and the five directories.
    assert "minio" in host.os_groups
    assert host.os_users["minio"]["home"] == "/home/minio"
    for directory in (INSTALL_DIR, CONFIG_DIR, DATA_DIR, LOG_DIR, BACKUP_DIR):
        assert directory in host.directory_stats

    # D5: the package spooled, then unpacked through the archive_members seam.
    assert host.remote_files[PACKAGE_SPOOL] == b"fake-tarball"
    assert f"{INSTALL_DIR}/minio" in host.existing_files
    assert f"{INSTALL_DIR}/mc" in host.existing_files

    # D3: the cluster env lists every node_address, sorted, endpoint per node.
    env = host.remote_files[ENV_PATH].decode("utf-8")
    assert env.startswith("MINIO_ROOT_USER=minioadmin\nMINIO_ROOT_PASSWORD=minioadmin\n")
    assert (
        f"MINIO_OPTS=\"--config-dir {CONFIG_DIR} --address ':9000'"
        f" --console-address ':9001'"
        f" http://192.0.2.10:9000{DATA_DIR}/ http://192.0.2.20:9000{DATA_DIR}/\"" in env
    )

    # The unit template and the service state.
    unit = host.remote_files[UNIT_PATH].decode("utf-8")
    assert f"ExecStart={MC.replace('/mc', '')}/minio server $MINIO_OPTS" in unit
    assert "User=minio" in unit
    assert host.services["minio.service"] == "active"

    # The feature requests rode the act waves: policy copy, sync unit + start.
    assert host.remote_files[POLICY_SPOOL].decode("utf-8") == GETPUT_POLICY_JSON
    sync_unit = host.remote_files[SYNC_UNIT_PATH].decode("utf-8")
    assert f"ExecStart={MC} mirror --limit-upload 10MiB --remove --overwrite" in sync_unit
    assert "--watch miniosh/bucket01 miniowx/bucket03" in sync_unit
    assert host.services[SYNC_UNIT] == "active"

    # The apply-log sequence for the primary host: wave 0, handler, features.
    assert [e.name for e in changed_entries(report) if e.host == "ubuntu"] == [
        *INSTALL_NAMES,
        *CONFIGURE_NAMES,
        *HANDLER_NAMES,
        *FEATURE_NAMES,
    ]

    # Barriers: the checkpoint, then one action per mc script; waves 0..7.
    assert [type(b).__name__ for b in barriers(report)] == [
        "CheckpointRequest",
        *["ActionRequest"] * len(ACT_NAMES),
    ]
    assert waves(report) == [0, 1, 2, 3, 4, 5, 6, 7]


async def test_non_primary_cluster_host_runs_no_mc(tmp_path):
    """D4: the run_once stand-in -- buckets/users/sync only on the primary."""
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    peer = hosts["node2"]

    # The peer installed and started minio, but touched no mc state and no sync unit.
    assert peer.services["minio.service"] == "active"
    assert peer.ran_shells == []
    assert SYNC_UNIT_PATH not in peer.remote_files
    assert POLICY_SPOOL not in peer.remote_files
    assert barriers(report, "node2") and len(barriers(report, "node2")) == 1
    assert waves(report, "node2") == [0, 1]
    # Its env still carries the cluster endpoints (D3 is not a primary gate).
    env = peer.remote_files[ENV_PATH].decode("utf-8")
    assert f"http://192.0.2.10:9000{DATA_DIR}/ http://192.0.2.20:9000{DATA_DIR}/" in env


async def test_standalone_bucket_ops_run_on_every_host(tmp_path):
    """The role's standalone branch has no ``run_once``: peers provision too."""
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"), cluster_mode=False)
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    # The peer now needs its own alias script seeded -- it runs one.
    seed(hosts["node2"], address="192.0.2.20")
    report = await run_role(inventory, hosts)
    peer = hosts["node2"]

    env = peer.remote_files[ENV_PATH].decode("utf-8")
    assert f"':9001' {DATA_DIR}/\"" in env  # the standalone data-dir tail, no endpoints
    assert "http://192.0.2.10" not in env
    # Objects ran on the peer; users/sync stay primary-only (run_once there).
    alias_scripts = [s for s in peer.ran_shells if "alias set" in s]
    bucket_scripts = [s for s in peer.ran_shells if "/mc mb " in s]
    assert len(alias_scripts) == 1
    assert len(bucket_scripts) == 2
    assert not [s for s in peer.ran_shells if "admin policy" in s]
    barrier_names = [b.name for b in barriers(report, "node2")]
    assert "Add minio config" in barrier_names


# --- convergence and the handler chain -----------------------------------------


async def test_second_pass_converges_but_reruns_the_mc_shells(tmp_path):
    """No change anywhere; the handler wave disappears; the acts still run (D9)."""
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The chained handler was notified by nothing: no restart, no console wait.
    for name in HANDLER_NAMES:
        assert [e for e in second.merged().log if e.name == name] == []
    # D9: the mc shells re-run on the converged pass -- results are data.
    assert len(barriers(second, "ubuntu")) == len(ACT_NAMES) + 1


async def test_start_change_alone_still_waits_for_the_port(tmp_path):
    """The role's ``Start minio service`` notifies the wait directly (D8).

    With both files already installed but the unit stopped, only the start
    changes: the restart must not fire, the console-port wait must.
    """
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)
    hosts["ubuntu"].services["minio.service"] = "inactive"

    report = await run_role(inventory, hosts)
    names = [e.name for e in changed_entries(report) if e.host == "ubuntu"]
    assert "Start minio service" in names
    assert "Restart minio service (handler)" not in names
    assert f"Waiting for minio console port {CONSOLE_PORT}" in names


# --- the act failure gates ------------------------------------------------------


async def test_failed_alias_set_raises(tmp_path):
    """A non-zero ``mc alias set`` fails the body -- the role had no gate either."""
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    key = f"sudo {expected_scripts('192.0.2.10')[0]}"
    hosts["ubuntu"].shell_results[key] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "connection refused",
    }
    with pytest.raises(ValueError, match="mc alias set"):
        await run_role(inventory, hosts)


async def test_already_in_effect_is_tolerated(tmp_path):
    """The role's ``failed_when`` on the user task, reproduced on the act."""
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    key = f"sudo {expected_scripts('192.0.2.10')[4]}"
    hosts["ubuntu"].shell_results[key] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "The specified policy change is already in effect.",
    }
    report = await run_role(inventory, hosts)  # must not raise
    assert entry_named(report, "Copy policy file getput_policy.json").changed


# --- the family guard and outputs -----------------------------------------------


async def test_non_ubuntu_host_emits_nothing(tmp_path):
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.ran_shells == []
    assert skipped.services == {}


def test_guard_returns_empty_outputs(tmp_path):
    """The family guard's early return still publishes ``MinioOutputs``."""
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    v = resolve_vars(MinioVars, "minio", inventory)
    host = SimulatedHost.from_inventory(inventory[2])  # the RedHat entry

    generator = minio(v)(host)
    with pytest.raises(StopIteration) as stop:
        next(generator)
    assert stop.value.value == MinioOutputs(service="", install_dir="", endpoint="")


async def test_missing_package_source_raises_before_any_request(tmp_path):
    """D5: exactly one of package_url/package_path must be set, and it fails early."""
    inventory = make_inventory(None)  # type: ignore[arg-type]
    hosts = make_hosts(inventory)
    with pytest.raises(ValueError, match="package source"):
        await run_role(inventory, hosts)
    assert hosts["ubuntu"].ran_shells == []
    assert hosts["ubuntu"].existing_files == set()


# --- variable validation ----------------------------------------------------------


def test_unknown_key_is_rejected(tmp_path):
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"), nope=True)
    with pytest.raises(RoleVarsError, match="nope"):
        resolve_vars(MinioVars, "minio", inventory)


def test_sync_target_needs_credentials(tmp_path):
    broken = dict(SYNC)
    broken["target"] = {**SYNC["target"], "access_key": ""}
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"), sync=[broken])
    with pytest.raises(ValidationError, match="access_key"):
        resolve_vars(MinioVars, "minio", inventory)


def test_bucket_needs_a_name():
    with pytest.raises(ValidationError, match="name"):
        MinioVars.model_validate({"buckets": [{"name": ""}]})


# --- pure helper units -------------------------------------------------------------


def test_render_sync_units_names_and_content():
    hv = MinioVars.model_validate({"sync": [SYNC]})
    units = render_sync_units(hv, INSTALL_DIR)
    assert [u.name for u in units] == [SYNC_UNIT]
    content = units[0].content
    assert "User=minio" in content
    assert "Group=minio" in content
    assert (
        f"ExecStart={MC} mirror --limit-upload 10MiB --remove --overwrite"
        " --watch miniosh/bucket01 miniowx/bucket03" in content
    )


def test_bucket_script_carries_the_ilm_rule_only_when_set():
    from reemote.roles.minio import BucketSpec

    plan = make_plan("192.0.2.10")
    with_rule = bucket_script(plan, BucketSpec(name="a", expire_days=30))
    without = bucket_script(plan, BucketSpec(name="a"))
    assert "mc mb ansible_minio/a | true" in with_rule
    assert "mc ilm rule add --expire-days 30 ansible_minio/a" in with_rule
    assert "ilm" not in without


async def test_dry_preview_parks_at_the_checkpoint(tmp_path):
    """Dry is one wave deep: the stream stops at ``install done``, applies nothing."""
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    # No spool seeding here: issue #73 landed, so the dry preview plans the
    # extraction of an archive this same deploy only spools on the apply.
    report = await run_role(inventory, hosts, dry=True)

    assert [type(b).__name__ for b in barriers(report, "ubuntu")] == ["CheckpointRequest"]
    assert hosts["ubuntu"].ran_shells == []
    assert hosts["ubuntu"].services == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_minio.py -k
# snapshot`` prints the diff against them). The install spool directories are
# content-addressed, so the hashes are stable; each ``mc`` barrier line carries
# the raw ``Result`` dict, which is why they are long single lines. Both
# Ubuntu hosts share every install wave; only ``web1``'s silence is invisible
# here -- an off-family host contributes no rows at all.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Create group if not exists [changed: group minio absent, will create]
        $ sudo groupadd minio
    node2: Create group if not exists [changed: group minio absent, will create]
        $ sudo groupadd minio
    ubuntu: Create user if not exists [changed: user minio absent, will create; home /home/minio missing, will create]
        $ sudo useradd -d /home/minio -g minio -m minio
        $ sudo mkdir -p /home/minio
        $ sudo chown minio:minio /home/minio
    node2: Create user if not exists [changed: user minio absent, will create; home /home/minio missing, will create]
        $ sudo useradd -d /home/minio -g minio -m minio
        $ sudo mkdir -p /home/minio
        $ sudo chown minio:minio /home/minio
    ubuntu: Create dirs. /data/minio/bin [changed: /data/minio/bin missing, will create]
        $ sudo mkdir -p /data/minio/bin
        $ sudo chmod 755 /data/minio/bin
        $ sudo chown minio:minio /data/minio/bin
    node2: Create dirs. /data/minio/bin [changed: /data/minio/bin missing, will create]
        $ sudo mkdir -p /data/minio/bin
        $ sudo chmod 755 /data/minio/bin
        $ sudo chown minio:minio /data/minio/bin
    ubuntu: Create dirs. /data/minio/cfg [changed: /data/minio/cfg missing, will create]
        $ sudo mkdir -p /data/minio/cfg
        $ sudo chmod 755 /data/minio/cfg
        $ sudo chown minio:minio /data/minio/cfg
    node2: Create dirs. /data/minio/cfg [changed: /data/minio/cfg missing, will create]
        $ sudo mkdir -p /data/minio/cfg
        $ sudo chmod 755 /data/minio/cfg
        $ sudo chown minio:minio /data/minio/cfg
    ubuntu: Create dirs. /data/minio/data [changed: /data/minio/data missing, will create]
        $ sudo mkdir -p /data/minio/data
        $ sudo chmod 755 /data/minio/data
        $ sudo chown minio:minio /data/minio/data
    node2: Create dirs. /data/minio/data [changed: /data/minio/data missing, will create]
        $ sudo mkdir -p /data/minio/data
        $ sudo chmod 755 /data/minio/data
        $ sudo chown minio:minio /data/minio/data
    ubuntu: Create dirs. /data/minio/log [changed: /data/minio/log missing, will create]
        $ sudo mkdir -p /data/minio/log
        $ sudo chmod 755 /data/minio/log
        $ sudo chown minio:minio /data/minio/log
    node2: Create dirs. /data/minio/log [changed: /data/minio/log missing, will create]
        $ sudo mkdir -p /data/minio/log
        $ sudo chmod 755 /data/minio/log
        $ sudo chown minio:minio /data/minio/log
    ubuntu: Create dirs. /opt/minio  [changed: /opt/minio missing, will create]
        $ sudo mkdir -p /opt/minio
        $ sudo chmod 755 /opt/minio
        $ sudo chown minio:minio /opt/minio
    node2: Create dirs. /opt/minio  [changed: /opt/minio missing, will create]
        $ sudo mkdir -p /opt/minio
        $ sudo chmod 755 /opt/minio
        $ sudo chown minio:minio /opt/minio
    ubuntu: Spool minio.tar.gz       [changed: content /tmp/minio.tar.gz is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-4af0cf7e63e3b0fb/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-4af0cf7e63e3b0fb/content /tmp/minio.tar.gz && chmod 644 /tmp/minio.tar.gz && rm -rf /tmp/reemote-install-4af0cf7e63e3b0fb'
    node2: Spool minio.tar.gz       [changed: content /tmp/minio.tar.gz is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-4af0cf7e63e3b0fb/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-4af0cf7e63e3b0fb/content /tmp/minio.tar.gz && chmod 644 /tmp/minio.tar.gz && rm -rf /tmp/reemote-install-4af0cf7e63e3b0fb'
    ubuntu: Unpack minio.tar.gz      [changed: will extract /tmp/minio.tar.gz into /data/minio/bin; will set ownership of /data/minio/bin recursively; will set mode 755 of /data/minio/bin recursively]
        $ sudo tar -xz -f /tmp/minio.tar.gz -C /data/minio/bin
        $ sudo chown -R minio:minio /data/minio/bin
        $ sudo chmod -R 755 /data/minio/bin
    node2: Unpack minio.tar.gz      [changed: will extract /tmp/minio.tar.gz into /data/minio/bin; will set ownership of /data/minio/bin recursively; will set mode 755 of /data/minio/bin recursively]
        $ sudo tar -xz -f /tmp/minio.tar.gz -C /data/minio/bin
        $ sudo chown -R minio:minio /data/minio/bin
        $ sudo chmod -R 755 /data/minio/bin
    ubuntu: Add environment file     [changed: content /etc/default/minio is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f80bd6bbc1628408/content
        $ sudo sh -c 'mkdir -p /etc/default && cp /tmp/reemote-install-f80bd6bbc1628408/content /etc/default/minio && chown root:root /etc/default/minio && chmod 644 /etc/default/minio && rm -rf /tmp/reemote-install-f80bd6bbc1628408'
    node2: Add environment file     [changed: content /etc/default/minio is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f80bd6bbc1628408/content
        $ sudo sh -c 'mkdir -p /etc/default && cp /tmp/reemote-install-f80bd6bbc1628408/content /etc/default/minio && chown root:root /etc/default/minio && chmod 644 /etc/default/minio && rm -rf /tmp/reemote-install-f80bd6bbc1628408'
    ubuntu: Add systemd file         [changed: content /etc/systemd/system/minio.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f50f26939896d813/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-f50f26939896d813/content /etc/systemd/system/minio.service && chown root:root /etc/systemd/system/minio.service && chmod 644 /etc/systemd/system/minio.service && rm -rf /tmp/reemote-install-f50f26939896d813'
    node2: Add systemd file         [changed: content /etc/systemd/system/minio.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f50f26939896d813/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-f50f26939896d813/content /etc/systemd/system/minio.service && chown root:root /etc/systemd/system/minio.service && chmod 644 /etc/systemd/system/minio.service && rm -rf /tmp/reemote-install-f50f26939896d813'
    ubuntu: Start minio service      [changed: daemon-reload; minio.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start minio.service
    node2: Start minio service      [changed: daemon-reload; minio.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start minio.service
    barrier: install done → 2 hosts notified
    barrier: install done → 2 hosts notified
  wave 1
    ubuntu: Restart minio service (handler) [changed: minio.service already running; minio.service will restart]
        $ sudo systemctl restart minio.service
    node2: Restart minio service (handler) [changed: minio.service already running; minio.service will restart]
        $ sudo systemctl restart minio.service
    ubuntu: Waiting for minio console port 9001 [changed: wait for tcp port 9001 to be listening]
        $ sudo wait for Port(9001, 'tcp') <is_listening> every 1.0s up to 60s
    node2: Waiting for minio console port 9001 [changed: wait for tcp port 9001 to be listening]
        $ sudo wait for Port(9001, 'tcp') <is_listening> every 1.0s up to 60s
    barrier: Add minio config → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 2
    barrier: Create bucket bucket01 → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 3
    barrier: Create bucket bucket02 → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 4
    ubuntu: Copy policy file getput_policy.json [changed: content /tmp/getput_policy.json is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-cadfc84b7481addd/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-cadfc84b7481addd/content /tmp/getput_policy.json && chown minio:minio /tmp/getput_policy.json && chmod 644 /tmp/getput_policy.json && rm -rf /tmp/reemote-install-cadfc84b7481addd'
    barrier: Create policy getput → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 5
    barrier: Create user miniouser → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 6
    barrier: Add synchronize config → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 7
    ubuntu: Render systemd file minio_sh_bucket01_to_wx_bucket03.service [changed: content /etc/systemd/system/minio_sh_bucket01_to_wx_bucket03.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-1420513970e6bbde/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-1420513970e6bbde/content /etc/systemd/system/minio_sh_bucket01_to_wx_bucket03.service && chown root:root /etc/systemd/system/minio_sh_bucket01_to_wx_bucket03.service && chmod 644 /etc/systemd/system/minio_sh_bucket01_to_wx_bucket03.service && rm -rf /tmp/reemote-install-1420513970e6bbde'
    ubuntu: Start synchronize service minio_sh_bucket01_to_wx_bucket03.service [changed: daemon-reload; minio_sh_bucket01_to_wx_bucket03.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start minio_sh_bucket01_to_wx_bucket03.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Create group if not exists [no change: group minio already present]
    node2: Create group if not exists [no change: group minio already present]
    ubuntu: Create user if not exists [no change: user minio already as requested; directory /home/minio already exists]
    node2: Create user if not exists [no change: user minio already as requested; directory /home/minio already exists]
    ubuntu: Create dirs. /data/minio/bin [no change: directory /data/minio/bin already exists]
    node2: Create dirs. /data/minio/bin [no change: directory /data/minio/bin already exists]
    ubuntu: Create dirs. /data/minio/cfg [no change: directory /data/minio/cfg already exists]
    node2: Create dirs. /data/minio/cfg [no change: directory /data/minio/cfg already exists]
    ubuntu: Create dirs. /data/minio/data [no change: directory /data/minio/data already exists]
    node2: Create dirs. /data/minio/data [no change: directory /data/minio/data already exists]
    ubuntu: Create dirs. /data/minio/log [no change: directory /data/minio/log already exists]
    node2: Create dirs. /data/minio/log [no change: directory /data/minio/log already exists]
    ubuntu: Create dirs. /opt/minio  [no change: directory /opt/minio already exists]
    node2: Create dirs. /opt/minio  [no change: directory /opt/minio already exists]
    ubuntu: Spool minio.tar.gz       [no change: content already up to date (/tmp/minio.tar.gz)]
    node2: Spool minio.tar.gz       [no change: content already up to date (/tmp/minio.tar.gz)]
    ubuntu: Unpack minio.tar.gz      [no change: archive already extracted (/data/minio/bin/minio exists)]
    node2: Unpack minio.tar.gz      [no change: archive already extracted (/data/minio/bin/minio exists)]
    ubuntu: Add environment file     [no change: content already up to date (/etc/default/minio)]
    node2: Add environment file     [no change: content already up to date (/etc/default/minio)]
    ubuntu: Add systemd file         [no change: content already up to date (/etc/systemd/system/minio.service)]
    node2: Add systemd file         [no change: content already up to date (/etc/systemd/system/minio.service)]
    ubuntu: Start minio service      [no change: minio.service already running]
    node2: Start minio service      [no change: minio.service already running]
    barrier: install done → 0 hosts notified
    barrier: install done → 0 hosts notified
  wave 1
    barrier: Add minio config → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 2
    barrier: Create bucket bucket01 → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 3
    barrier: Create bucket bucket02 → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 4
    ubuntu: Copy policy file getput_policy.json [no change: content already up to date (/tmp/getput_policy.json)]
    barrier: Create policy getput → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 5
    barrier: Create user miniouser → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 6
    barrier: Add synchronize config → {'exit_status': 0, 'stdout': 'Done.\\n', 'stderr': ''}
  wave 7
    ubuntu: Render systemd file minio_sh_bucket01_to_wx_bucket03.service [no change: content already up to date (/etc/systemd/system/minio_sh_bucket01_to_wx_bucket03.service)]
    ubuntu: Start synchronize service minio_sh_bucket01_to_wx_bucket03.service [no change: minio_sh_bucket01_to_wx_bucket03.service already running]"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Create group if not exists [changed: group minio absent, will create]
    node2: Create group if not exists [changed: group minio absent, will create]
    ubuntu: Create user if not exists [changed: user minio absent, will create; home /home/minio missing, will create]
    node2: Create user if not exists [changed: user minio absent, will create; home /home/minio missing, will create]
    ubuntu: Create dirs. /data/minio/bin [changed: /data/minio/bin missing, will create]
    node2: Create dirs. /data/minio/bin [changed: /data/minio/bin missing, will create]
    ubuntu: Create dirs. /data/minio/cfg [changed: /data/minio/cfg missing, will create]
    node2: Create dirs. /data/minio/cfg [changed: /data/minio/cfg missing, will create]
    ubuntu: Create dirs. /data/minio/data [changed: /data/minio/data missing, will create]
    node2: Create dirs. /data/minio/data [changed: /data/minio/data missing, will create]
    ubuntu: Create dirs. /data/minio/log [changed: /data/minio/log missing, will create]
    node2: Create dirs. /data/minio/log [changed: /data/minio/log missing, will create]
    ubuntu: Create dirs. /opt/minio  [changed: /opt/minio missing, will create]
    node2: Create dirs. /opt/minio  [changed: /opt/minio missing, will create]
    ubuntu: Spool minio.tar.gz       [changed: content /tmp/minio.tar.gz is absent, will install]
    node2: Spool minio.tar.gz       [changed: content /tmp/minio.tar.gz is absent, will install]
    ubuntu: Unpack minio.tar.gz      [changed: src /tmp/minio.tar.gz absent at plan time; extraction planned once it is staged]
    node2: Unpack minio.tar.gz      [changed: src /tmp/minio.tar.gz absent at plan time; extraction planned once it is staged]
    ubuntu: Add environment file     [changed: content /etc/default/minio is absent, will install]
    node2: Add environment file     [changed: content /etc/default/minio is absent, will install]
    ubuntu: Add systemd file         [changed: content /etc/systemd/system/minio.service is absent, will install]
    node2: Add systemd file         [changed: content /etc/systemd/system/minio.service is absent, will install]
    ubuntu: Start minio service      [changed: minio.service not running (None), will start]
    node2: Start minio service      [changed: minio.service not running (None), will start]
    barrier: install done → unknown until run
    barrier: install done → unknown until run"""


async def test_tree_snapshot(tmp_path):
    """The whole run as one tree: install, checkpoint, handler, feature acts.

    The converged tree is the proof of the migration's shape: every request
    re-plans to *no change*, the checkpoint notifies nobody, so the handler
    wave collapses into the six ``mc`` acts re-running (D9). The dry tree is
    the one-wave preview the checkpoint refuses to fake (section 16.6).
    """
    inventory = make_inventory(str(tmp_path / "minio.tar.gz"))
    (tmp_path / "minio.tar.gz").write_bytes(b"fake-tarball")
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
