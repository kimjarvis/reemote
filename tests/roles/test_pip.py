# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``pip`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, pip(v), host_factory=...)``. There is no gather ->
snapshot -> factory harness and no ``importlib`` loader -- the ``PythonVersion``
barrier inside the role does that work, and the driver runs it.

The load-bearing assertion is the migration's two claims: run the role, then run
it *again* against the same persistent hosts, and the second pass must report no
change anywhere (convergence); and the family guard means a host outside
``"Ubuntu"`` records nothing.

One simulation limit is bridged explicitly, because the in-memory host models no
real subsystem (it is a *simulation* limit, not a library bug):

* ``python_versions`` seeds the interpreter the ``PythonVersion`` barrier reads
  (and that ``pip.venv`` copies into a fresh tree). The marker path the role
  removes is interpolated from the seeded ``minor``, so the test seeds the
  ``EXTERNALLY-MANAGED`` file at exactly that path. The simulated ``apt-get
  install`` of the pip package does not itself install an interpreter, so the
  version is a fixture, not a derived state.

The apt install (``install_apt_packages`` -> ``apt_packages``), the pip installs
(``install_pip_packages`` -> the normalized ``pip_packages`` store) and the
virtualenv create (``create_virtualenv`` -> ``virtualenv_python_versions``) all
mutate the seams the operations read back, so the rerun observes the first pass.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import PipPackage, PipVars, pip
from reemote.roles.pip import (
    PipPlan,
    derive_executable,
    pip_executable_for,
    pip_plan,
    pip_spec,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

MINOR = 12
MICRO = 3
MARKER = f"/usr/lib/python3.{MINOR}/EXTERNALLY-MANAGED"
PYTHON_VERSION = {"major": 3, "minor": MINOR, "micro": MICRO}

# The Ubuntu target's install_packages, the same shape the inventory carries as a
# ``pip_install_packages`` key: a version pin, a bare name and a virtualenv entry.
INSTALL_PACKAGES = [
    {"name": "ipaddress", "version": "1.0.18"},
    "colorama",
    {"name": "six", "virtualenv": "/srv/app/venv"},
]
VENV = "/srv/app/venv"

# The role's whole request wave, in stream order (D3: one request per loop item).
REQUEST_NAMES = (
    "Ensure Pip is installed.",
    "Remove EXTERNALLY-MANAGED.",
    "Ensure pip package ipaddress is installed.",
    "Ensure pip package colorama is installed.",
    f"Ensure virtualenv {VENV} exists.",
    "Ensure pip package six is installed.",
)


def _regular_file(host: SimulatedHost, path: str, data: bytes = b"", mode: int = 644) -> None:
    """Seed an existing regular file: content, existence and stat metadata."""
    host.remote_files[path] = data
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": mode,
        "size": len(data),
    }


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one RedHat host to be skipped.

    The role variables ride on the Ubuntu entry's ``roles`` mapping under
    ``"pip"``, written with the model's bare field names, which is what
    ``resolve_vars(PipVars, "pip", inventory)`` binds, so a test
    changes a variable exactly the way an operator does.
    """
    entry: dict[str, Any] = {
        "host": "web1",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "roles": {
            "pip": {
                "install_packages": INSTALL_PACKAGES,
            },
        },
    }
    entry["roles"]["pip"].update(overrides)
    return [
        entry,
        {
            "host": "centos",
            "family": "RedHat",
            "connection": {"address": "192.0.2.12", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    pip_package_installed: bool = False,
    marker_present: bool = True,
    packages_installed: bool = False,
    venv_present: bool = False,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets.

    Only the Ubuntu target gets the pip seeding: the RedHat entry keeps its stock
    simulated state, so a role that reached it would be caught by the
    untouched-store assertions. The interpreter version is seeded on the target
    (the barrier reads it); the marker, the apt pip package, the pip packages and
    the virtualenv seed the converged state the rerun must observe.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            host.python_versions = {"python3": dict(PYTHON_VERSION)}
            if marker_present:
                _regular_file(host, MARKER, b"[externally-managed]\n")
            if pip_package_installed:
                host.apt_packages["python3-pip"] = [""]
            if packages_installed:
                host.pip_packages = {"ipaddress": "1.0.18", "colorama": "", "six": ""}
            if venv_present:
                host.virtualenv_python_versions[VENV] = dict(PYTHON_VERSION)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts."""
    v = resolve_vars(PipVars, "pip", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, pip(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "web1") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry for entry in report.merged().log if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


# --- the first pass really installs pip ---------------------------------------


async def test_first_pass_installs_pip():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["web1"]

    # Every request ran and reported a change on a fresh host.
    for name in REQUEST_NAMES:
        assert entry_named(report, name).changed, name

    # The apt pip package, the pip packages and the virtualenv all landed.
    assert "python3-pip" in host.apt_packages
    assert host.pip_packages["ipaddress"] == "1.0.18"
    assert "colorama" in host.pip_packages
    assert "six" in host.pip_packages
    assert VENV in host.virtualenv_python_versions
    # The PEP 668 marker is gone.
    assert MARKER not in host.remote_files
    assert MARKER not in host.existing_files


async def test_one_barrier_then_one_request_wave():
    """The PythonVersion barrier closes wave 0; every request lands in wave 1."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    barriers = [
        record.barrier for record in host_records(report, "web1") if record.barrier
    ]
    assert len(barriers) == 1
    assert type(barriers[0]).__name__ == "FactRequest"

    ordered = [entry.name for entry in report.merged().log if entry.host == "web1"]
    assert ordered == list(REQUEST_NAMES)


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["centos"]
    assert host_records(report, "centos") == []
    assert [e for e in report.merged().log if e.host == "centos"] == []
    assert skipped.pip_packages == {}
    assert skipped.apt_packages.get("python3-pip") is None
    assert skipped.virtualenv_python_versions == {}


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The marker stays gone and nothing re-installs.
    assert MARKER not in hosts["web1"].existing_files


async def test_converged_host_is_a_full_noop():
    """Package + marker + pip packages + venv all already in place."""
    inventory = make_inventory()
    hosts = make_hosts(
        inventory,
        pip_package_installed=True,
        marker_present=False,
        packages_installed=True,
        venv_present=True,
    )
    report = await run_role(inventory, hosts)

    assert changed_entries(report) == []
    assert [e.name for e in report.merged().log if e.host == "web1"] == list(
        REQUEST_NAMES
    )


async def test_dry_preview_parks_at_the_barrier():
    """``dry`` stops at the first barrier: no version read, nothing applied."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["web1"]
    before = dict(host.remote_files)

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.pip_packages == {}
    assert host.apt_packages.get("python3-pip") is None
    assert host.remote_files == before  # the seeded marker untouched, nothing removed
    assert "barrier: PythonVersion → unknown until run" in report.tree()


# --- the marker path follows the interpreter barrier --------------------------


async def test_marker_path_follows_the_interpreter_minor():
    """A different interpreter minor changes the removed path (D2)."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, marker_present=False)
    other = "/usr/lib/python3.10/EXTERNALLY-MANAGED"
    hosts["web1"].python_versions = {"python3": {"major": 3, "minor": 10, "micro": 12}}
    _regular_file(hosts["web1"], other, b"[externally-managed]\n")

    report = await run_role(inventory, hosts)

    assert entry_named(report, "Remove EXTERNALLY-MANAGED.").changed
    assert other not in hosts["web1"].existing_files
    assert MARKER not in hosts["web1"].remote_files  # the 3.12 path was never touched


async def test_removing_an_absent_marker_is_a_noop():
    inventory = make_inventory()
    hosts = make_hosts(
        inventory, pip_package_installed=True, marker_present=False, packages_installed=True, venv_present=True
    )
    report = await run_role(inventory, hosts)

    assert not entry_named(report, "Remove EXTERNALLY-MANAGED.").changed


# --- the loop's per-item knobs ------------------------------------------------


async def test_state_partitions_into_present_latest_absent():
    """Each item's ``state`` maps onto one request with the right flags (D3)."""
    inventory = make_inventory(
        install_packages=[
            {"name": "kept"},
            {"name": "fresh", "state": "latest"},
            {"name": "dropped", "state": "absent"},
        ]
    )
    hosts = make_hosts(inventory, pip_package_installed=True, marker_present=False)
    hosts["web1"].pip_packages = {"kept": "", "fresh": "", "dropped": ""}

    report = await run_role(inventory, hosts)

    assert entry_named(report, "Ensure pip package kept is installed.").changed is False
    assert entry_named(report, "Ensure pip package fresh is up to date.").changed
    assert entry_named(report, "Ensure pip package dropped is removed.").changed
    assert "dropped" not in hosts["web1"].pip_packages


async def test_virtualenv_item_creates_the_tree_and_targets_its_pip():
    """A virtualenv item is a create request then an install via ``<venv>/bin/pip`` (D4)."""
    inventory = make_inventory(install_packages=[{"name": "six", "virtualenv": VENV}])
    hosts = make_hosts(inventory, pip_package_installed=True, marker_present=False)

    report = await run_role(inventory, hosts)

    create = entry_named(report, f"Ensure virtualenv {VENV} exists.")
    install = entry_named(report, "Ensure pip package six is installed.")
    assert create.changed
    assert install.changed
    assert VENV in hosts["web1"].virtualenv_python_versions
    # The create command names the venv path; the install names its bin/pip.
    assert VENV in create.commands[0].shell()
    assert f"{VENV}/bin/pip" in install.commands[0].shell()


# --- the pure half, unit-tested directly --------------------------------------


def test_derive_executable():
    assert derive_executable("python3-pip") == "pip3"
    assert derive_executable("python-pip") == "pip"


def test_pip_spec_and_executable_for():
    assert pip_spec(PipPackage(name="six")) == "six"
    assert pip_spec(PipPackage(name="six", version="1.16")) == "six==1.16"
    assert pip_executable_for(PipPackage(name="six"), "pip3") == "pip3"
    assert (
        pip_executable_for(PipPackage(name="six", virtualenv="/srv/app/venv"), "pip3")
        == "/srv/app/venv/bin/pip"
    )


def test_pip_plan_defaults():
    plan = pip_plan(PipVars(), MINOR)

    assert plan == PipPlan(
        package="python3-pip",
        executable="pip3",
        externally_managed=MARKER,
        items=(),
    )
    assert hash(plan)  # frozen and hashable, as a deploy body's local must be


def test_inventory_binds_the_prefixed_variables():
    inventory = make_inventory(package="python-pip", executable="pip2.7")
    v = resolve_vars(PipVars, "pip", inventory)

    assert v["web1"].package == "python-pip"
    assert v["web1"].executable == "pip2.7"
    # The other role's host keeps the defaults: no pip_ keys, nothing bound.
    assert v["centos"] == PipVars()


def test_vars_reject_bad_values():
    with pytest.raises(ValidationError, match="non-empty"):
        PipVars(package="")
    with pytest.raises(ValidationError, match="non-empty"):
        PipVars(executable="")
    with pytest.raises(ValidationError, match="non-empty"):
        PipPackage(name="")
    with pytest.raises(ValidationError, match="non-empty"):
        PipPackage(name="six", version="")


def test_package_state_rejects_forcereinstall():
    """D6: a state beyond present/absent/latest is rejected up front."""
    with pytest.raises(ValidationError, match="state"):
        PipPackage(name="six", state="forcereinstall")


def test_bare_string_item_is_coerced_to_a_name():
    v = PipVars.model_validate({"install_packages": ["colorama", {"name": "six"}]})

    assert v.install_packages == (
        PipPackage(name="colorama"),
        PipPackage(name="six"),
    )


def test_vars_are_frozen_and_reject_unknown_fields():
    v = PipVars()
    with pytest.raises(ValidationError):
        v.package = "python-pip"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PipVars(pip_package="python-pip")  # the Ansible name, not the field


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(packge="python3-pip")
    make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="packge"):
        resolve_vars(PipVars, "pip", inventory)


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_pip.py -k
# snapshot`` prints the diff against them). One barrier wave, then the whole
# request wave; the rerun keeps both waves but reports no change anywhere.
FIRST_PASS_TREE = """(root)
  wave 0
    barrier: PythonVersion → {'major': 3, 'minor': 12, 'micro': 3}
  wave 1
    web1: Ensure Pip is installed. [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install python3-pip
    web1: Remove EXTERNALLY-MANAGED. [changed: /usr/lib/python3.12/EXTERNALLY-MANAGED present, will remove]
        $ sudo rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED
    web1: Ensure pip package ipaddress is installed. [changed: 1 package(s) to install]
        $ sudo pip3 install ipaddress==1.0.18
    web1: Ensure pip package colorama is installed. [changed: 1 package(s) to install]
        $ sudo pip3 install colorama
    web1: Ensure virtualenv /srv/app/venv exists. [changed: virtualenv /srv/app/venv missing, will create]
        $ sudo python -m venv /srv/app/venv
    web1: Ensure pip package six is installed. [changed: 1 package(s) to install]
        $ sudo /srv/app/venv/bin/pip install six"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: PythonVersion → {'major': 3, 'minor': 12, 'micro': 3}
  wave 1
    web1: Ensure Pip is installed. [no change: all packages present]
    web1: Remove EXTERNALLY-MANAGED. [no change: file /usr/lib/python3.12/EXTERNALLY-MANAGED does not exist]
    web1: Ensure pip package ipaddress is installed. [no change: all packages present]
    web1: Ensure pip package colorama is installed. [no change: all packages present]
    web1: Ensure virtualenv /srv/app/venv exists. [no change: virtualenv /srv/app/venv already exists]
    web1: Ensure pip package six is installed. [no change: all packages present]"""


async def test_tree_snapshot():
    """The whole run as one tree: interpreter barrier, then the request wave.

    The second tree is what a rerun prints -- the same two waves, every line a
    no-change -- and the RedHat host never appears, because the family guard
    returns before its first ``yield``.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
