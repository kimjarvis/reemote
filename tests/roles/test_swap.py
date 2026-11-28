# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``swap`` role (design doc §17.7).

The role is library code, so it is imported (``from reemote.roles import swap``)
and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, swap(v), host_factory=...)``. The one ``fact(files.File)``
barrier -- ``check-size.yml``'s ``stat`` register -- is what the whole branch
structure hangs on, so it is exercised rather than mocked: the same body runs on
a fresh host (missing file), a wrongly sized host (existing file), and an
``absent`` host.

Two assertions carry the migration's claims:

* **convergence** -- run the role, then run it again against the same persistent
  hosts, and the second pass reports no change anywhere and re-runs *no* command
  at all (``ran_shells`` stops growing), which is the swap-specific form of the
  claim: ``dd``/``mkswap``/``swapon`` must not repeat on a host whose file is
  already the wanted size.
* **the four commands' gates** -- ``swapoff`` runs only when the
  ``fact(hardware.SwapEnabled)`` barrier says swap is active and
  :attr:`SwapVars.test_mode` is off; ``dd``/``mkswap`` run only when the size
  differs; ``swapon`` alone of the three is what ``test_mode`` suppresses on the
  build path.

One simulation note, sanctioned by the ``postgresql``/``certbot`` precedent: a
raw ``Shell`` action mutates no store, so :class:`SwapSimulatedHost` plants what
the commands would have left behind -- ``dd``/``fallocate`` their file,
``swapon``/``swapoff`` the ``swap_enabled`` flag. The *answers* still come from
``shell_results`` through ``super()``, so an unseeded or unexpected command still
raises: nothing about the role's own command sequence is faked.
"""

import re
from typing import Any

import pytest
from pydantic import ValidationError

from reemote import CommandError, run_waves
from reemote.roles import swap
from reemote.roles.swap import (
    BYTES_PER_MB,
    SWAPPINESS_KEY,
    SwapVars,
    dd_command,
    existing_size_mb,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

SWAP_PATH = "/swapfile"
SYSCTL_PATH = "/etc/sysctl.d/90-swap.conf"
SYSCTL_DIR = "/etc/sysctl.d"
SIZE_MB = 512

# The request names are the upstream task names, the barrier names the reads the
# task files' ``register``/``removes:``/``when:`` clauses stood for.
FSTAB_NAME = "Manage swap file entry in fstab."
STAT_NAME = "Check if swap file exists"
ACTIVE_NAME = "Is swap active?"
SWAPOFF_NAME = "Disable swap (if configured)."
REMOVE_NAME = "Ensure swap file doesn't exist (if configured)."
DD_NAME = "Ensure swap file exists."
MODE_NAME = "Set permissions on swap file."
MKSWAP_NAME = "Make swap file if necessary."
SWAPON_NAME = "Run swapon on the swap file."
DIR_NAME = "Ensure sysctl config directory exists."
SWAPPINESS_NAME = "Set swappiness."

# The exact shells the four act barriers render (the ``shell_results`` keys): a
# passwordless inventory renders ``sudo`` as a bare prefix, and the paths carry
# no character ``shlex`` would quote.
DD_SHELL = f"sudo {dd_command(SWAP_PATH, SIZE_MB)}"
MKSWAP_SHELL = f"sudo mkswap {SWAP_PATH}"
SWAPON_SHELL = f"sudo swapon {SWAP_PATH}"
SWAPOFF_SHELL = f"sudo swapoff {SWAP_PATH}"
FALLOCATE_SHELL = "sudo fallocate -l 512M /swapfile"

OK = {"exit_status": 0, "stdout": "", "stderr": ""}

#: The two spellings the planted ``dd``/``fallocate`` effect recognises: the
#: role's generated command and the common override.
_DD_SHAPE = re.compile(r"dd if=/dev/zero of=(\S+) bs=1M count=(\d+)")
_FALLOCATE_SHAPE = re.compile(r"fallocate -l (\d+)M (\S+)")

BASE_FSTAB = (
    "# /etc/fstab: static file system information.\n"
    "/dev/sda1 / ext4 errors=remount-ro 0 1\n"
)

#: The default ``present`` path's full log sequence on a fresh host: the fstab
#: write, the disable branch's no-op removal, then the enable branch's three
#: requests. The four commands are barriers, not log lines.
FRESH_NAMES = [
    FSTAB_NAME,
    REMOVE_NAME,
    MODE_NAME,
    DIR_NAME,
    SWAPPINESS_NAME,
]


class SwapSimulatedHost(SimulatedHost):
    """``SimulatedHost`` with the four elevated commands' in-stream effects.

    ``dd``/``fallocate`` leave a root-owned file of the requested size behind at
    mode ``644`` (what the umask gives), which is what the *next* pass's
    ``files.File`` barrier reads and what makes ``swapon``/``swapoff`` flip the
    ``swap_enabled`` flag the :class:`~reemote.modules.hardware.SwapEnabled`
    barrier reads. ``mkswap`` writes a signature no migrated fact reads back, so
    it plants nothing. The content bytes are an empty string rather than
    ``count`` megabytes of zeros: no fact in this role reads the file back, and
    only its ``file_stats`` size matters.
    """

    async def _run_shell(self, shell: str) -> Any:
        result = await super()._run_shell(shell)
        if result.exit_status != 0:
            return result
        dd = _DD_SHAPE.search(shell)
        if dd:
            self._plant_swap_file(dd.group(1), int(dd.group(2)))
            return result
        fallocate = _FALLOCATE_SHAPE.search(shell)
        if fallocate:
            self._plant_swap_file(fallocate.group(2), int(fallocate.group(1)))
        elif " swapon " in shell:
            self.swap_enabled = True
        elif " swapoff " in shell:
            self.swap_enabled = False
        return result

    def _plant_swap_file(self, path: str, size_mb: int) -> None:
        """Record the file ``dd``/``fallocate`` just wrote, at the umask's mode."""
        self.remote_files[path] = b""
        self.existing_files.add(path)
        self.file_stats[path] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": size_mb * BYTES_PER_MB,
        }


def seed(
    host: SwapSimulatedHost,
    *,
    existing_mb: int | None = None,
    swap_enabled: bool = False,
    swappiness: int | None = None,
    mode: int = 644,
) -> None:
    """Pre-state: a stock Ubuntu machine with an fstab and nothing else.

    ``existing_mb`` plants a swap file of that size at ``mode`` (the default
    ``644`` is the drift the permissions request exists to converge);
    ``swap_enabled`` is the :class:`~reemote.modules.hardware.SwapEnabled` seam;
    ``swappiness`` seeds the live kernel value. All four command answers are
    seeded, so a command the role should not run would still be recorded rather
    than raise -- the assertions on ``ran_shells`` are what catch a stray one.
    """
    host.remote_files = {"/etc/fstab": BASE_FSTAB.encode()}
    host.existing_files = {"/etc/fstab"}
    host.file_stats = {
        "/etc/fstab": {"user": "root", "group": "root", "mode": 644, "size": 100},
    }
    if existing_mb is not None:
        host.remote_files[SWAP_PATH] = b""
        host.existing_files.add(SWAP_PATH)
        host.file_stats[SWAP_PATH] = {
            "user": "root",
            "group": "root",
            "mode": mode,
            "size": existing_mb * BYTES_PER_MB,
        }
    host.directory_stats = {}
    host.swap_enabled = swap_enabled
    host.sysctls = {} if swappiness is None else {SWAPPINESS_KEY: swappiness}
    host.shell_results = {
        DD_SHELL: dict(OK),
        MKSWAP_SHELL: dict(OK),
        SWAPON_SHELL: dict(OK),
        SWAPOFF_SHELL: dict(OK),
    }
    host.ran_shells = []


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one RedHat host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under ``"swap"``,
    written with the model's bare field names, which is what
    ``resolve_vars(SwapVars, "swap", inventory)`` binds, so a test changes a
    variable exactly the way an operator does. The base entry exercises the
    role's default: ``state: present`` at 512 MB.
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
        "roles": {"swap": {}},
    }
    entry["roles"]["swap"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(
    inventory: list[dict[str, Any]], **state: Any
) -> dict[str, SwapSimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions.
    """
    hosts: dict[str, SwapSimulatedHost] = {}
    for entry in inventory:
        host = SwapSimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host, **state)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SwapSimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts.

    The same ``Host`` objects come back for every entry, so state survives from
    one pass to the next -- which is the whole point of the second run.
    """
    v = resolve_vars(SwapVars, "swap", inventory)

    def factory(entry: dict[str, Any]) -> SwapSimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, swap(v), host_factory=factory, dry=dry
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


def names(report: Any, host: str = "ubuntu") -> list[str]:
    """The pass's log-entry names for one host, in wave order."""
    return [entry.name for entry in report.merged().log if entry.host == host]


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


def fstab_text(host: SwapSimulatedHost) -> str:
    """The simulated ``/etc/fstab``, as text."""
    return host.remote_files["/etc/fstab"].decode("utf-8")


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


def barrier_kinds(report: Any, host: str = "ubuntu") -> list[str]:
    """The kinds of barrier *host* parked on, in wave order."""
    return [type(barrier).__name__ for barrier in barriers(report, host)]


def barrier_names(report: Any, host: str = "ubuntu") -> list[str]:
    """The names of the barriers *host* parked on, in wave order."""
    return [barrier.name for barrier in barriers(report, host)]


# --- the default present path --------------------------------------------------


async def test_first_pass_builds_and_activates_the_swap_file():
    """A missing file: ``dd``, ``mkswap``, ``swapon``, and the mode request.

    The whole ``enable.yml`` chain runs, and the permissions request converges
    the ``644`` the planted ``dd`` left behind to the ``0600`` root:root the task
    asks for.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert host.file_stats[SWAP_PATH]["size"] == SIZE_MB * BYTES_PER_MB
    assert host.file_stats[SWAP_PATH]["mode"] == 600
    assert (host.file_stats[SWAP_PATH]["user"], host.file_stats[SWAP_PATH]["group"]) == (
        "root",
        "root",
    )
    assert host.swap_enabled is True
    assert host.ran_shells == [DD_SHELL, MKSWAP_SHELL, SWAPON_SHELL]
    assert names(report) == FRESH_NAMES
    assert [entry.name for entry in changed_entries(report)] == [
        FSTAB_NAME,
        MODE_NAME,
        DIR_NAME,
        SWAPPINESS_NAME,
    ]
    # The disable branch's removal is present but a no-op: nothing was there.
    assert not entry_named(report, REMOVE_NAME).changed


async def test_fstab_entry_is_the_role_declaration():
    """``mount: name=none src=… fstype=swap opts=sw`` becomes one fstab line."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    assert "/swapfile none swap sw 0 0" in fstab_text(hosts["ubuntu"])
    # The pre-existing lines survive verbatim -- the write is spool-and-install
    # of the whole file, keyed-edit like server.etc_hosts.
    assert BASE_FSTAB in fstab_text(hosts["ubuntu"])


async def test_swappiness_is_applied_and_persisted():
    """``sysctl: name=vm.swappiness sysctl_set=true sysctl_file=…`` both halves."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, SWAPPINESS_NAME) == {
        "key": SWAPPINESS_KEY,
        "value": 60,
        "sysctl_file": SYSCTL_PATH,
        "sysctl_set": True,
    }
    assert host.sysctls[SWAPPINESS_KEY] == 60
    assert f"{SWAPPINESS_KEY} = 60" in host.remote_files[SYSCTL_PATH].decode()
    # The directory request is upstream's own task, kept even though the sysctl
    # install mkdirs the parent: it is the 0755 root:root guarantee.
    assert host.directory_stats[SYSCTL_DIR]["mode"] == 755


async def test_four_barriers_five_waves_on_a_fresh_host():
    """The shape: the stat fact, then the three build commands, five waves.

    Each barrier closes its own wave, and no ``checkpoint`` exists -- the role
    has no handlers and no later decision reads an earlier request's outcome.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(barrier).__name__ for barrier in barriers(report)] == [
        "FactRequest",
        "ActionRequest",
        "ActionRequest",
        "ActionRequest",
    ]
    assert waves(report) == [0, 1, 2, 3, 4]


# --- convergence ---------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    assert changed_entries(second) == []
    # The size barrier now says "already the wanted size", so no command runs at
    # all: the build chain is not merely idempotent, it is absent.
    assert host.ran_shells == [DD_SHELL, MKSWAP_SHELL, SWAPON_SHELL]
    assert waves(second) == [0, 1]
    assert second.tree() == CONVERGED_TREE


async def test_existing_correctly_sized_file_is_never_touched():
    """A host that already matches runs no command and reports no change.

    The file is there at the wanted size and mode, the fstab line and the
    sysctl row are already written: this is the converged state reached without
    the role having built anything.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, existing_mb=SIZE_MB, swappiness=60)
    host = hosts["ubuntu"]
    host.file_stats[SWAP_PATH]["mode"] = 600
    host.remote_files["/etc/fstab"] = (BASE_FSTAB + "/swapfile none swap sw 0 0\n").encode()
    host.directory_stats[SYSCTL_DIR] = {
        "user": "root",
        "group": "root",
        "mode": 755,
    }
    host.remote_files[SYSCTL_PATH] = b"vm.swappiness = 60\n"
    host.existing_files.add(SYSCTL_PATH)

    report = await run_role(inventory, hosts)
    assert changed_entries(report) == []
    assert host.ran_shells == []


# --- the resize path -----------------------------------------------------------


async def test_wrong_size_deactivates_removes_and_rebuilds():
    """``check-size`` differs: ``disable.yml`` runs before ``enable.yml``.

    The existing 256 MB file is active, so ``swapoff`` runs, the file is removed,
    and ``dd`` recreates it at the wanted size -- the role's resize chain, in
    task order.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, existing_mb=256, swap_enabled=True)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert host.ran_shells == [
        SWAPOFF_SHELL,
        DD_SHELL,
        MKSWAP_SHELL,
        SWAPON_SHELL,
    ]
    assert host.file_stats[SWAP_PATH]["size"] == SIZE_MB * BYTES_PER_MB
    # Two reads (the size stat, then "is any swap active?") and four commands.
    assert barrier_kinds(report) == [
        "FactRequest",
        "FactRequest",
        "ActionRequest",
        "ActionRequest",
        "ActionRequest",
        "ActionRequest",
    ]
    assert barrier_names(report) == [
        STAT_NAME,
        ACTIVE_NAME,
        SWAPOFF_NAME,
        DD_NAME,
        MKSWAP_NAME,
        SWAPON_NAME,
    ]
    assert entry_named(report, REMOVE_NAME).changed
    assert host.swap_enabled is True


async def test_swapoff_is_skipped_when_no_swap_is_active():
    """The ``SwapEnabled`` gate: a wrong-sized but inactive file needs no ``swapoff``.

    The barrier still runs (it is the only way to answer), but the command does
    not -- Ansible asked the same question of
    ``ansible_facts.memory_mb['swap']['total']`` (D2).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, existing_mb=256, swap_enabled=False)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert SWAPOFF_SHELL not in host.ran_shells
    assert barrier_kinds(report) == [
        "FactRequest",
        "FactRequest",
        "ActionRequest",
        "ActionRequest",
        "ActionRequest",
    ]
    assert [entry.name for entry in changed_entries(report)] == FRESH_NAMES
    assert host.file_stats[SWAP_PATH]["size"] == SIZE_MB * BYTES_PER_MB


# --- swap_test_mode ------------------------------------------------------------


async def test_test_mode_skips_swapon_but_builds_the_file():
    """``swap_test_mode`` gates the activation commands only.

    ``dd``, ``mkswap``, the mode and the sysctl pair all still run -- upstream
    spells the flag on the ``swapon`` task alone (and on ``swapoff``).
    """
    inventory = make_inventory(test_mode=True)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    assert host.ran_shells == [DD_SHELL, MKSWAP_SHELL]
    assert host.swap_enabled is False


async def test_test_mode_skips_swapoff():
    """The same flag on the ``absent`` path: the file is removed, never deactivated."""
    inventory = make_inventory(state="absent", test_mode=True)
    hosts = make_hosts(inventory, existing_mb=SIZE_MB, swap_enabled=True)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    assert SWAPOFF_SHELL not in host.ran_shells
    assert SWAP_PATH not in host.file_stats
    assert host.swap_enabled is True


# --- the absent state ----------------------------------------------------------


async def test_absent_state_removes_file_entry_and_swap():
    """``swap_file_state: absent``: deactivate, delete, and drop the fstab line.

    The enable branch never runs, so no ``dd``/``mkswap``/``swapon``, no mode
    request and no swappiness request -- and the sysctl row the earlier pass
    wrote is deliberately left alone, exactly as upstream leaves it.
    """
    inventory = make_inventory(state="absent")
    hosts = make_hosts(inventory, existing_mb=SIZE_MB, swap_enabled=True, swappiness=60)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert host.ran_shells == [SWAPOFF_SHELL]
    assert SWAP_PATH not in host.file_stats
    assert SWAP_PATH not in host.existing_files
    assert host.swap_enabled is False
    assert "/swapfile none swap sw 0 0" not in fstab_text(host)
    assert names(report) == [FSTAB_NAME, REMOVE_NAME]
    assert not has_entry(report, MODE_NAME)
    assert not has_entry(report, DIR_NAME)
    assert not has_entry(report, SWAPPINESS_NAME)
    # Upstream tears down the file but never the swappiness line it wrote: the
    # seeded kernel value and the absence of any drop-in both survive the pass.
    assert host.sysctls == {SWAPPINESS_KEY: 60}
    assert SYSCTL_PATH not in host.remote_files


async def test_absent_path_converges_in_two_waves():
    """Already gone: the fstab no-op, the stat barrier, the removal no-op."""
    inventory = make_inventory(state="absent")
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first) == []
    assert waves(first) == [0, 1]
    assert barrier_kinds(first) == ["FactRequest"]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the variables -------------------------------------------------------------


async def test_custom_create_command_runs_verbatim():
    """``swap_file_create_command`` is used as written, not rebuilt (D5).

    The common override is ``fallocate``, so the simulated plant recognises both
    spellings and the pass still converges.
    """
    inventory = make_inventory(create_command="fallocate -l 512M /swapfile")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.shell_results[FALLOCATE_SHELL] = dict(OK)

    await run_role(inventory, hosts)
    assert host.ran_shells[0] == FALLOCATE_SHELL
    assert DD_SHELL not in host.ran_shells
    assert host.file_stats[SWAP_PATH]["size"] == SIZE_MB * BYTES_PER_MB

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert host.ran_shells == [FALLOCATE_SHELL, MKSWAP_SHELL, SWAPON_SHELL]


async def test_swappiness_variable_reaches_both_halves():
    inventory = make_inventory(swappiness=10)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, SWAPPINESS_NAME)["value"] == 10
    assert hosts["ubuntu"].sysctls[SWAPPINESS_KEY] == 10


async def test_sysctl_file_variable_moves_the_drop_in():
    """``swap_sysctl_file`` sets the persistence file *and* the directory task."""
    inventory = make_inventory(sysctl_file="/etc/sysctl.conf")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, SWAPPINESS_NAME)["sysctl_file"] == "/etc/sysctl.conf"
    assert request_kwargs(report, DIR_NAME)["path"] == "/etc"
    assert hosts["ubuntu"].remote_files["/etc/sysctl.conf"].decode().endswith(
        "vm.swappiness = 60\n"
    )


async def test_file_path_variable_moves_every_read_and_request():
    inventory = make_inventory(file_path="/var/swap-custom")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    path = "/var/swap-custom"
    host.shell_results = {
        f"sudo {dd_command(path, SIZE_MB)}": dict(OK),
        f"sudo mkswap {path}": dict(OK),
        f"sudo swapon {path}": dict(OK),
        f"sudo swapoff {path}": dict(OK),
    }
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, FSTAB_NAME)["device"] == path
    assert request_kwargs(report, MODE_NAME)["path"] == path
    assert f"{path} none swap sw 0 0" in fstab_text(host)
    assert host.file_stats[path]["mode"] == 600


# --- the family guard ----------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before it yields anything, so
    it records no request and no barrier (D1) -- even though the upstream role
    itself is family-agnostic.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.file_stats == {}
    assert skipped.swap_enabled is False
    assert skipped.ran_shells == []


# --- the dry preview -----------------------------------------------------------


async def test_dry_preview_stops_at_the_stat_barrier():
    """``dry`` is a one-wave preview: the fstab request is planned, the read parks.

    Nothing is applied and no command runs, so the branch the body would take on
    the strength of the ``files.File`` read is simply unknown (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.ran_shells == []
    assert host.swap_enabled is False
    assert SWAP_PATH not in host.file_stats
    assert fstab_text(host) == BASE_FSTAB
    assert report.tree() == DRY_PREVIEW_TREE


# --- the commands are fail-fast ------------------------------------------------


async def test_failed_dd_raises_command_error():
    """A barrier result is data -- the body turns a non-zero exit into the error.

    ``dd`` filling the filesystem is the failure this role must not swallow:
    building a truncated swap file and ``mkswap``ing it would leave a broken
    system.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].shell_results[DD_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "dd: failed to open '/swapfile': No space left on device\n",
    }

    with pytest.raises(CommandError, match="dd if=/dev/zero"):
        await run_role(inventory, hosts)
    # Nothing after the failure ran.
    assert hosts["ubuntu"].ran_shells == [DD_SHELL]


async def test_failed_mkswap_raises_command_error():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].shell_results[MKSWAP_SHELL] = {
        "exit_status": 4,
        "stdout": "",
        "stderr": "mkswap: no swap space signature\n",
    }

    with pytest.raises(CommandError, match="mkswap"):
        await run_role(inventory, hosts)
    assert SWAPON_SHELL not in hosts["ubuntu"].ran_shells


# --- the variables model -------------------------------------------------------


def test_defaults_are_the_role_defaults():
    """``defaults/main.yml``, verbatim (its strings are typed here, D6)."""
    v = SwapVars()
    assert v.file_path == "/swapfile"
    assert v.size_mb == 512
    assert v.swappiness == 60
    assert v.sysctl_file == "/etc/sysctl.d/90-swap.conf"
    assert v.state == "present"
    assert v.create_command is None
    assert v.test_mode is False


def test_vars_are_frozen_and_reject_unknown_fields():
    v = SwapVars()
    with pytest.raises(ValidationError):
        v.size_mb = 1024  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        SwapVars(swap_file_path="/swapfile")


def test_upstream_string_numbers_coerce():
    """The role's ``'512'``/``'60'`` defaults arrive as strings (D6)."""
    v = resolve_vars(SwapVars, "swap", make_inventory(size_mb="1024", swappiness="10"))
    assert v["ubuntu"].size_mb == 1024
    assert v["ubuntu"].swappiness == 10


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"file_path": "swapfile"}, "absolute"),
        ({"sysctl_file": "90-swap.conf"}, "absolute"),
        ({"size_mb": 0}, "greater than"),
        ({"size_mb": -1}, "greater than"),
        ({"swappiness": 201}, "le"),
        ({"create_command": ""}, "non-empty"),
        ({"state": "mounted"}, "present"),
    ],
)
def test_invalid_role_values_fail_fast(overrides: dict[str, Any], match: str):
    inventory = make_inventory(**overrides)
    with pytest.raises(ValidationError, match=match):
        resolve_vars(SwapVars, "swap", inventory)


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(size="1024")
    with pytest.raises(RoleVarsError, match="size"):
        resolve_vars(SwapVars, "swap", inventory)


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(SwapVars, "swap", make_inventory(state="absent"))

    assert v["ubuntu"].state == "absent"
    # The other role's host keeps the defaults: no swap keys, nothing bound.
    assert v["web1"] == SwapVars()


# --- the pure helpers ----------------------------------------------------------


def test_dd_command_is_the_role_default_with_the_path_quoted():
    assert dd_command("/swapfile", 512) == "dd if=/dev/zero of=/swapfile bs=1M count=512"
    # The one place the generated command differs from upstream's bare Jinja
    # interpolation (D5).
    assert dd_command("/var/my swapfile", 64) == (
        "dd if=/dev/zero of='/var/my swapfile' bs=1M count=64"
    )


def test_existing_size_mb_is_the_two_set_fact_tasks():
    row = {"size": 512 * BYTES_PER_MB}
    assert existing_size_mb(row) == 512
    # A partial megabyte truncates, like ``| int``.
    assert existing_size_mb({"size": 512 * BYTES_PER_MB - 1}) == 511
    # ``check-size.yml``'s else-branch: absent (None) and not-a-file (False).
    assert existing_size_mb(None) == 0
    assert existing_size_mb(False) == 0


# --- the wave tree (design doc section 7.2) ------------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_swap.py -k
# snapshot`` prints the diff against them). A fresh host is the five-wave case:
# the fstab write closed by the stat barrier, then each build command closing its
# own wave. The command lines are the barriers' ``$`` lines, rendered with the
# bare ``sudo`` prefix the passwordless inventory produces.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Manage swap file entry in fstab. [changed: /swapfile entry will be written to /etc/fstab]
        $ scp <fstab file> -> /tmp/reemote-server-fstab-451675132d5b96cc/fstab
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-server-fstab-451675132d5b96cc/fstab /etc/fstab && chmod 0644 /etc/fstab && rm -rf /tmp/reemote-server-fstab-451675132d5b96cc'
    barrier: Check if swap file exists → None
  wave 1
    ubuntu: Ensure swap file doesn't exist (if configured). [no change: file /swapfile does not exist]
    barrier: Ensure swap file exists. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    ubuntu: Set permissions on swap file. [changed: mode 644 differs, will chmod 600]
        $ sudo chmod 600 /swapfile
    barrier: Make swap file if necessary. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 3
    barrier: Run swapon on the swap file. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 4
    ubuntu: Ensure sysctl config directory exists. [changed: /etc/sysctl.d missing, will create]
        $ sudo mkdir -p /etc/sysctl.d
        $ sudo chmod 755 /etc/sysctl.d
        $ sudo chown root:root /etc/sysctl.d
    ubuntu: Set swappiness.          [changed: vm.swappiness not set to 60 at runtime, will apply; /etc/sysctl.d/90-swap.conf missing or differs for vm.swappiness, will write]
        $ sudo sysctl -w vm.swappiness=60
        $ scp <sysctl conf> -> /tmp/reemote-sysctl-4e728f06e70ce371/sysctl.conf
        $ sudo sh -c 'mkdir -p /etc/sysctl.d && cp /tmp/reemote-sysctl-4e728f06e70ce371/sysctl.conf /etc/sysctl.d/90-swap.conf && chmod 0644 /etc/sysctl.d/90-swap.conf && rm -rf /tmp/reemote-sysctl-4e728f06e70ce371'
        $ sudo sysctl -p /etc/sysctl.d/90-swap.conf"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Manage swap file entry in fstab. [no change: /swapfile entry already present in /etc/fstab]
    barrier: Check if swap file exists → {'user': 'root', 'group': 'root', 'mode': 600, 'size': 536870912}
  wave 1
    ubuntu: Set permissions on swap file. [no change: file /swapfile already exists]
    ubuntu: Ensure sysctl config directory exists. [no change: directory /etc/sysctl.d already exists]
    ubuntu: Set swappiness.          [no change: vm.swappiness already set to 60 at runtime; /etc/sysctl.d/90-swap.conf already up to date for vm.swappiness]"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Manage swap file entry in fstab. [changed: /swapfile entry will be written to /etc/fstab]
    barrier: Check if swap file exists → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree, then the rerun's two no-change waves."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
