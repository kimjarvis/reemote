# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``glusterfs`` role (design doc §17.7).

The role is library code, so it is imported (``from reemote.roles import
glusterfs``) and replayed against ``SimulatedHost`` through the real wave
driver: ``run_waves(inventory, glusterfs(v), host_factory=...)``. There is no
harness of its own -- the single ``checkpoint`` barrier inside the body *is*
the snapshot point the old ``register: glusterfs_ppa_added`` played, and the
driver runs it.

Two assertions carry the migration's claims, exactly as in the
``tests/roles/test_nginx.py`` precedent:

* **convergence** -- run the role, then run it again against the same
  persistent hosts, and the second pass reports no change anywhere. Because the
  rerun re-derives the reinstall gate through the checkpoint, this proves the
  barrier is honest rather than that a frozen plan was reused.
* **the reinstall gate** -- the conditional removal request exists only on the
  pass whose ``checkpoint("setup done")`` found the PPA request changed. The
  converged pass carries no removal line at all.

The structural claim is pinned too: **one checkpoint, two waves**. The barrier
sequence is exactly ``[CheckpointRequest]`` -- no fact, no act, no control --
and the second checkpoint after the service task is deliberately absent,
because the role has no handler that could ever read it.

Seeding choices, none of them a bridge:

* the simulated apt install records package names only, and the role manages
  no files, so a stock Ubuntu host needs just the empty stores plus the
  codename ``apt-add-repository`` writes into the PPA source.
* no between-pass bridge is needed. Every store the role touches (apt's update
  stamp and PPA sources, the package rows, the systemd unit state) is
  maintained by the simulation's own mutators, which is why the second pass
  converges instead of churning.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import GlusterfsVars, glusterfs
from reemote.roles.glusterfs import GLUSTERFS_PACKAGES, PPA_NAME
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

DEPS_NAME = "Ensure dependencies are installed"
#: The stable ``register: glusterfs_ppa_added`` channel the checkpoint selects on.
REINSTALL_NAME = "Ensure GlusterFS will reinstall if the PPA was just added"
INSTALL_NAME = "Ensure GlusterFS is installed"
SERVICE_NAME = "Ensure GlusterFS is started and enabled at boot"
PPA_SRC = "ppa:gluster/glusterfs-7"
PPA_URL = "http://ppa.launchpad.net/gluster/glusterfs-7/ubuntu"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under
    ``"glusterfs"``, written with the model's bare field names, which is what
    ``resolve_vars(GlusterfsVars, "glusterfs", inventory)`` binds, so a test
    changes a variable exactly the way an operator does. The base entry
    exercises the non-default branch: ``ppa_use: true`` -- the PPA and its
    reinstall split.
    """
    entry: dict[str, Any] = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "roles": {
            "glusterfs": {
                "ppa_use": True,
            },
        },
    }
    entry["roles"]["glusterfs"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def seed(host: SimulatedHost, *, gluster_preinstalled: bool = False) -> None:
    """Pre-state: a stock Ubuntu machine, GlusterFS not yet installed.

    ``gluster_preinstalled`` models the distro-packaged case the role's
    reinstall trick exists for: both packages already present from the Ubuntu
    archive, before this play ever added the PPA.
    """
    host.apt_packages = (
        {name: [] for name in GLUSTERFS_PACKAGES} if gluster_preinstalled else {}
    )
    host.apt_update_stamp = None
    host.apt_sources = []
    host.lsb_release = {"Codename": "noble"}
    host.services = {}
    host.enabled_services = set()


def make_hosts(
    inventory: list[dict[str, Any]], *, gluster_preinstalled: bool = False
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host, gluster_preinstalled=gluster_preinstalled)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts.

    The same ``Host`` objects come back for every entry, so state survives from
    one pass to the next -- which is the whole point of the second run.
    """
    v = resolve_vars(GlusterfsVars, "glusterfs", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, glusterfs(v), host_factory=factory, dry=dry
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


def has_entry(report: Any, name: str, host: str = "ubuntu") -> bool:
    """Whether any log entry with *name* exists on *host*."""
    return any(
        entry.name == name and entry.host == host for entry in report.merged().log
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


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_and_enables_glusterfs():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The dependencies (plus software-properties-common for add-apt-repository,
    # D4), the PPA with its index refresh, and both GlusterFS packages.
    assert "dirmngr" in host.apt_packages
    assert "software-properties-common" in host.apt_packages
    assert "glusterfs-server" in host.apt_packages
    assert "glusterfs-client" in host.apt_packages
    assert any(source["url"] == PPA_URL for source in host.apt_sources)
    assert host.apt_update_stamp is not None

    # The reinstall split materialized (a no-op here -- the packages were not
    # installed yet -- but the request exists because the setup checkpoint
    # named the host).
    assert has_entry(report, REINSTALL_NAME)
    assert not entry_named(report, REINSTALL_NAME).changed

    # The unit is running and enabled under the default daemon name.
    assert host.services["glusterd.service"] == "active"
    assert "glusterd.service" in host.enabled_services

    # The whole changed sequence, in wave order: everything changes except the
    # gate-selected removal with nothing installed yet.
    assert [e.name for e in changed_entries(report)] == [
        DEPS_NAME,
        PPA_NAME,
        INSTALL_NAME,
        SERVICE_NAME,
    ]


async def test_reinstall_removal_rides_before_the_install():
    """The distro-packaged case: the trick removes first so the PPA version wins.

    With both packages preinstalled from the Ubuntu archive, the pass that adds
    the PPA must *change* on the removal and then reinstall -- the role's
    ``state: absent`` + ``state: present`` pair, in task order.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, gluster_preinstalled=True)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    removal = entry_named(report, REINSTALL_NAME)
    install = entry_named(report, INSTALL_NAME)
    assert removal.changed
    assert install.changed
    assert "2 package(s) to remove" in removal.reason
    # Still installed at the end -- removed and reinstalled from the PPA in one
    # wave, in request order.
    assert "glusterfs-server" in host.apt_packages
    assert "glusterfs-client" in host.apt_packages
    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered.index(REINSTALL_NAME) < ordered.index(INSTALL_NAME)


async def test_one_checkpoint_two_waves():
    """The shape: exactly one checkpoint barrier, two waves, no handler stage.

    The role has no handlers, so the second checkpoint that the handler roles
    carry is deliberately absent -- nothing would ever read that report.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(barrier).__name__ for barrier in barriers(report)] == [
        "CheckpointRequest"
    ]
    assert waves(report) == [0, 1]
    assert "setup done → 1 host notified" in report.tree()

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered == [
        DEPS_NAME,
        PPA_NAME,
        REINSTALL_NAME,
        INSTALL_NAME,
        SERVICE_NAME,
    ]


# --- the PPA gate -------------------------------------------------------------


async def test_reinstall_removal_only_rides_the_pass_that_added_the_ppa():
    """The gate is the role's ``register`` + ``when: glusterfs_ppa_added is changed``.

    On the first pass the PPA is added, so the setup checkpoint names the host
    and the install wave carries the conditional removal. On the converged pass
    the PPA already exists, the checkpoint names nobody, and the removal
    request is not emitted at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert has_entry(first, REINSTALL_NAME)

    second = await run_role(inventory, hosts)
    assert not has_entry(second, REINSTALL_NAME)
    assert "setup done → 0 hosts notified" in second.tree()


async def test_ppa_use_false_skips_the_ppa_and_the_removal():
    """``glusterfs_ppa_use: false``: no PPA, no reinstall trick, plain install.

    The dependency request narrows to the role's own ``dirmngr`` (D4), and the
    default path still converges on the rerun.
    """
    inventory = make_inventory(ppa_use=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert not has_entry(report, PPA_NAME)
    assert not has_entry(report, REINSTALL_NAME)
    assert request_kwargs(report, DEPS_NAME)["packages"] == ("dirmngr",)
    assert host.apt_sources == []
    assert "glusterfs-server" in host.apt_packages

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the variables ------------------------------------------------------------


async def test_default_release_pins_only_the_install():
    """``glusterfs_default_release`` is apt's ``-t`` on the install, never the removal.

    The empty default -- the role's ``glusterfs_default_release: ""`` -- means
    no pin and maps to ``None``.
    """
    inventory = make_inventory(default_release="jammy")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, INSTALL_NAME)["default_release"] == "jammy"

    plain = make_inventory()
    plain_hosts = make_hosts(plain)
    plain_report = await run_role(plain, plain_hosts)
    assert request_kwargs(plain_report, INSTALL_NAME)["default_release"] is None


async def test_daemon_variable_names_the_unit():
    """The 14.04 spelling is reachable as a variable, not a dropped ``set_fact`` (D2)."""
    inventory = make_inventory(daemon="glusterfs-server")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, SERVICE_NAME)["service"] == "glusterfs-server"
    assert hosts["ubuntu"].services["glusterfs-server.service"] == "active"


async def test_ppa_version_interpolates_the_src():
    inventory = make_inventory(ppa_version="11")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, PPA_NAME)["src"] == "ppa:gluster/glusterfs-11"


# --- the family guard ---------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before it yields anything,
    so it records no request and no barrier -- the RedHat branch of the role
    (``setup-RedHat.yml``) is not migrated (D1).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.apt_packages == {}
    assert skipped.apt_sources == []
    assert skipped.services == {}


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The checkpoint still closes wave 0 (it is a read of the wave's real
    # outcomes), but the reinstall request is gone and the second wave carries
    # only no-change lines.
    assert second.tree() == CONVERGED_TREE


async def test_dry_preview_stops_at_the_setup_checkpoint():
    """``dry`` is a one-wave preview: it plans wave 0, parks, and applies nothing.

    The setup requests get plan lines, the checkpoint is ``unknown until run``,
    and the body stops there -- no log entry, no command, nothing mutated. A
    converged rerun is the honest preview (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.apt_packages == {}
    assert host.apt_sources == []
    assert host.apt_update_stamp is None
    assert host.services == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the variables model ------------------------------------------------------


def test_vars_are_frozen_and_reject_unknown_fields():
    v = GlusterfsVars()
    with pytest.raises(ValidationError):
        v.daemon = "glusterfs-server"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        GlusterfsVars(glusterfs_daemon="glusterd")


def test_debian_knobs_are_carried_inert():
    """``glusterfs_gpg_key_version`` / ``glusterfs_deb_version`` bind but do nothing (D3)."""
    v = resolve_vars(
        GlusterfsVars,
        "glusterfs",
        make_inventory(gpg_key_version="11", deb_version="LATEST"),
    )["ubuntu"]
    assert v.gpg_key_version == "11"
    assert v.deb_version == "LATEST"


async def test_inert_knobs_change_no_request():
    """Pinning the Debian knobs changes the resolved vars, never the tree."""
    plain = make_inventory()
    plain_hosts = make_hosts(plain)
    baseline = await run_role(plain, plain_hosts)

    pinned = make_inventory(gpg_key_version="11", deb_version="10.5")
    pinned_hosts = make_hosts(pinned)
    second = await run_role(pinned, pinned_hosts)

    assert second.tree() == baseline.tree()


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(GlusterfsVars, "glusterfs", make_inventory(ppa_version="9"))

    assert v["ubuntu"].ppa_version == "9"
    # The other role's host keeps the defaults: no glusterfs keys, nothing bound.
    assert v["web1"] == GlusterfsVars()


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(ppa_versoin="9")
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="ppa_versoin"):
        resolve_vars(GlusterfsVars, "glusterfs", inventory)
    # Nothing ran: the failure is at resolve time, before any host is
    # contacted, so both hosts keep their seeded stores untouched.
    assert hosts["ubuntu"].apt_packages == {}
    assert hosts["web1"].apt_packages == {}


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_glusterfs.py -k
# snapshot`` prints the diff against them). The two checkpoint-free waves are
# the whole stream: setup, then install-and-service; the checkpoint line is the
# single barrier the body parks on.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Ensure dependencies are installed [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install dirmngr software-properties-common
    ubuntu: Add PPA for GlusterFS    [changed: ppa ppa:gluster/glusterfs-7 will be added; apt update]
        $ sudo apt-add-repository -y ppa:gluster/glusterfs-7
        $ sudo apt-get update
    barrier: setup done → 1 host notified
  wave 1
    ubuntu: Ensure GlusterFS will reinstall if the PPA was just added [no change: no packages to remove]
    ubuntu: Ensure GlusterFS is installed [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install glusterfs-server glusterfs-client
    ubuntu: Ensure GlusterFS is started and enabled at boot [changed: glusterd.service not running (None), will start; glusterd.service not enabled, will enable]
        $ sudo systemctl start glusterd.service
        $ sudo systemctl enable glusterd.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Ensure dependencies are installed [no change: all packages present]
    ubuntu: Add PPA for GlusterFS    [no change: ppa ppa:gluster/glusterfs-7 exists]
    barrier: setup done → 0 hosts notified
  wave 1
    ubuntu: Ensure GlusterFS is installed [no change: all packages present]
    ubuntu: Ensure GlusterFS is started and enabled at boot [no change: glusterd.service already running; glusterd.service already enabled]"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Ensure dependencies are installed [changed: 2 package(s) to install]
    ubuntu: Add PPA for GlusterFS    [changed: ppa ppa:gluster/glusterfs-7 will be added; apt update]
    barrier: setup done → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree: setup, checkpoint, reinstall, install, service.

    The second tree is what a rerun prints -- still two waves, but with no
    reinstall line and nothing changed -- and it is the preview ``dry``
    deliberately refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
