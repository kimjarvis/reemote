"""Tests for reemote/structure.py -- the lockstep design-pattern demo.

The demonstration command tree (``demo_script`` and its reusable ``Sequence``
factories) lives HERE rather than in ``reemote/structure.py``: structure.py
provides the engine (Context, Shell/Callback/Sequence, traversal, Blackboard,
lockstep orchestration and the ``execute()`` facade), and this module builds a
demo tree on top of it and asserts the results via pytest.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Annotated, Any, List, Optional

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from reemote.structure import (
    Blackboard,
    Callback,
    Method,
    Sequence,
    Shell,
    SimulatedHost,
    execute,
)


# ---------------------------------------------------------------------------
# Callback callables -- imperative result-processing, defined at MODULE scope
# ---------------------------------------------------------------------------


def uname_a_check(host: SimulatedHost, board: Blackboard) -> Any:
    """Callback callable for the ``uname -a`` result (path ``outer.detect-os-full``).

    Defined OUTSIDE ``demo_script`` on purpose: a module-level callable cannot
    close over ``board``, so the board is a parameter, bound at the Callback
    site inside ``demo_script`` via ``lambda host: uname_a_check(host, board)``.
    It runs once per host (in lockstep), reads that host's ``outer.detect-os-full``
    result off the shared board -- the ``uname -a`` Shell nested just above it in
    the ``outer`` Sequence -- and derives a small summary: imperative
    result-processing living in a node.
    """
    resp = board.results["outer.detect-os-full"].get(host.name)
    if resp is None:  # NULL OBJECT: host was group-filtered for detect-os-full
        return None
    full = resp.value["stdout"]
    return {"full": full, "words": len(full.split())}


# ---------------------------------------------------------------------------
# Reusable Sequence factories -- PROGRAM COMPOSITION
# ---------------------------------------------------------------------------


def make_system_probe(name: str, board: Blackboard) -> Sequence:
    """FACTORY: returns a reusable Sequence that probes system info.

    The Sequence runs ``whoami`` (current user) and then a Callback that
    reads the result off the blackboard and derives a summary.

    Parameterized by ``name`` so it can be reused multiple times in one tree
    (each instance gets a unique blackboard path: ``{name}.whoami``, etc.).

    Parameterized by ``board`` because the Callback needs to read earlier
    results off the blackboard, and ``board`` doesn't exist at module scope.
    """

    def user_check(host: SimulatedHost) -> dict[str, Any]:
        """Callback: reads this host's ``whoami`` result and derives a summary."""
        resp = board.results[f"{name}.whoami"].get(host.name)
        if resp is None:  # NULL OBJECT: host was group-filtered
            return None
        user = resp.value["stdout"].strip()
        return {"user": user, "length": len(user)}

    return Sequence(
        Shell("whoami", group="linux", name="whoami"),
        Callback(user_check, group="linux", name="user-check"),
        name=name,
    )


# ---------------------------------------------------------------------------
# Pydantic request model for the apt-install probe -- mirrors reemote's
# CommonOperationRequest (operation.py) and Install.Request (apt/install.py):
# a shared base carries cross-cutting options (sudo) and a subclass adds the
# operation-specific parameter set.  ``extra="forbid"`` makes typos fail fast.
# ---------------------------------------------------------------------------


class CommonOptions(BaseModel):
    """Options shared by every request model.  ``sudo`` lives HERE and is
    inherited by subclasses -- the analogue of reemote's
    ``CommonOperationRequest`` (which also defines ``sudo``).

    NOTE: in the real framework ``sudo`` is NOT a command-string prefix; it
    flows through the Context into ``run_operation``'s SSH elevation.  The demo
    ``Shell`` has no elevation field, so ``make_apt_install_probe`` reflects it
    in the command string purely to make the option visible.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    sudo: bool = Field(default=False, description="Run the command with sudo.")


# A reusable, validated package-name ELEMENT type (Approach A, reused as a field
# type inside the model below): an apt-safe token with no shell metacharacters,
# so joining it into a command string cannot inject.
PackageName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    ),
]


class AptInstallProbeRequest(CommonOptions):
    """Parameter SET for ``make_apt_install_probe``.

    Inherits ``sudo`` from ``CommonOptions`` (so the chain is
    ``AptInstallProbeRequest -> CommonOptions -> BaseModel``) and adds the
    probe-specific options: required ``packages``, optional ``update`` and
    ``version``.
    """

    packages: list[PackageName] = Field(
        ..., min_length=1, description="Package names to install."
    )
    update: bool = Field(
        default=False, description="Run `apt-get update` before installing."
    )
    version: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9.+~:-]+$",
        description="Optional version pin (single package only).",
    )

    @model_validator(mode="after")
    def _version_needs_one_package(self) -> "AptInstallProbeRequest":
        # A version pin applies to one package; pinning several is ambiguous.
        if self.version is not None and len(self.packages) != 1:
            raise ValueError("version pin requires exactly one package")
        return self


def make_apt_install_probe(name: str, board: Blackboard, **params) -> Sequence:
    """FACTORY: returns a reusable Sequence that installs packages and checks
    if the installed package list changed.

    The parameter SET is validated as a whole by ``AptInstallProbeRequest``
    (pydantic), mirroring reemote's ``Request.model_validate(self.kwargs)``:
    ``packages`` (required, non-empty, apt-safe names) plus the inherited
    ``sudo`` and the optional ``update`` / ``version``.  Any violation raises
    ``pydantic.ValidationError`` at BUILD time, before any host runs.

    The Sequence:
    1. (option ``update``) runs ``apt-get update``.
    2. runs ``apt list --installed`` to capture the initial state.
    3. runs ``apt-get install -y <packages>`` (with optional sudo / version pin).
    4. runs ``apt list --installed`` again to capture the final state.
    5. runs a Callback that compares the two lists and reports if they changed.
    """
    req = AptInstallProbeRequest.model_validate(params)

    # `sudo` shown as a command prefix here; see the CommonOptions note.
    sudo = "sudo " if req.sudo else ""
    pin = f"={req.version}" if req.version else ""
    pkg_args = " ".join(f"{p}{pin}" for p in req.packages)
    install_command = f"{sudo}apt-get install -y {pkg_args}"

    def check_changed(host: SimulatedHost) -> dict[str, Any]:
        """Callback: compares the two ``apt list --installed`` results."""
        before_resp = board.results[f"{name}.list-before"].get(host.name)
        after_resp = board.results[f"{name}.list-after"].get(host.name)
        if before_resp is None or after_resp is None:
            return None  # NULL OBJECT: host was group-filtered
        before = before_resp.value["stdout"]
        after = after_resp.value["stdout"]
        changed = before != after
        return {
            "changed": changed,
            "before_lines": len(before.strip().splitlines()),
            "after_lines": len(after.strip().splitlines()),
        }

    children: List[Any] = []
    if req.update:  # option -> an extra leaf (adds a round + a blackboard path)
        children.append(
            Shell(f"{sudo}apt-get update", group="linux", name="update")
        )
    children += [
        Shell("apt list --installed", group="linux", name="list-before"),
        Shell(install_command, group="linux", name="install"),
        Shell("apt list --installed", group="linux", name="list-after"),
        Callback(check_changed, group="linux", name="check"),
    ]
    return Sequence(*children, name=name)


def make_inventory_report(name: str, inventory: List[SimulatedHost]) -> Sequence:
    """FACTORY: returns a Sequence that reports inventory information.

    The Sequence has a single Callback that examines the FULL inventory and
    reports which hosts belong to which groups.  This demonstrates how a
    Callback can access inventory information for ANY host, not just the
    current one being processed.

    The Callback runs ONLY on the control host (``group="control"``) because
    the inventory is the same for all hosts — running it once is sufficient.
    Other hosts skip this round (NULL OBJECT) but still consume it (lockstep).
    The result is stored under the control host's key in the blackboard.

    Parameterized by ``name`` (for blackboard path uniqueness) and ``inventory``
    (so the Callback can examine all hosts, not just the current one).
    """

    def report(host: SimulatedHost) -> dict[str, Any]:
        """Callback: examines the inventory and reports group membership."""
        linux_hosts = [h for h in inventory if "linux" in h.groups]
        webservers = [h for h in inventory if "webservers" in h.groups]
        windows_hosts = [h for h in inventory if "windows" in h.groups]

        return {
            "total_hosts": len(inventory),
            "linux_count": len(linux_hosts),
            "linux_hosts": [h.name for h in linux_hosts],
            "webservers_count": len(webservers),
            "webservers": [h.name for h in webservers],
            "windows_count": len(windows_hosts),
            "windows_hosts": [h.name for h in windows_hosts],
        }

    return Sequence(
        # Runs ONLY on the control host (group='control') because the inventory
        # is the same for all hosts.  Other hosts skip (NULL OBJECT) but still
        # consume the round (lockstep preserved).
        Callback(report, group="control", name="report"),
        name=name,
    )


def make_file_distribution(
    name: str, board: Blackboard, inventory: List[SimulatedHost]
) -> Sequence:
    """FACTORY: prepares a file on the control host, then distributes it to all.

    This demonstrates the GROUP FILTERING pattern for "run once, then run on all":
    1. A Callback with ``group="control"`` runs ONLY on the designated control
       host (the host in the "control" group).  It prepares a file and stores
       the file path in the blackboard.
    2. A Callback with no group runs on ALL hosts.  It reads the file path from
       the control host's result (via the blackboard) and simulates copying the
       file from the control host.

    The control host's result is stored under ``{name}.prepare``, and all hosts
    can read it (cross-host blackboard read).  Non-control hosts get NULL OBJECT
    (None) for the prepare step, but still consume the round (lockstep).

    Parameterized by ``name`` (for blackboard path uniqueness), ``board`` (for
    cross-host reads), and ``inventory`` (to find the control host).
    """

    def prepare_file(host: SimulatedHost) -> dict[str, Any]:
        """Callback: prepares a file on the control host (group='control')."""
        # Simulate file preparation on the control host
        file_path = f"/tmp/prepared-by-{host.name}"
        return {"file_path": file_path, "prepared_by": host.name}

    def copy_file(host: SimulatedHost) -> dict[str, Any]:
        """Callback: copies the file from the control host to this host."""
        # Find the control host from the inventory
        control_host = next((h for h in inventory if "control" in h.groups), None)
        if control_host is None:
            return None  # No control host in inventory
        # Read the prepare result from the control host (cross-host read)
        prepare_resp = board.results[f"{name}.prepare"].get(control_host.name)
        if prepare_resp is None:
            return None  # Control host didn't prepare (shouldn't happen)
        file_path = prepare_resp.value["file_path"]
        # Simulate copying the file from the control host
        return {
            "copied_from": file_path,
            "copied_from_host": control_host.name,
            "copied_to_host": host.name,
        }

    return Sequence(
        # Preparation: runs ONLY on the control host (group='control').
        # Other hosts skip this (NULL OBJECT) but still consume the round.
        Callback(prepare_file, group="control", name="prepare"),
        # Distribution: runs on ALL hosts.  Each host reads the file path
        # from the control host's result (cross-host blackboard read).
        Callback(copy_file, name="copy"),
        name=name,
    )


# ---------------------------------------------------------------------------
# The root command generator (built by the FACTORY, once per host)
# ---------------------------------------------------------------------------


async def demo_script(board: Blackboard, inventory: List[SimulatedHost]):
    """Root of the composite tree -- analogous to a reemote endpoint command.

    A plain async generator FUNCTION: each call returns a fresh generator, so
    the FACTORY still builds a new root per host, and ``board`` and ``inventory``
    are simple parameters rather than instance state.  The only state shared
    between hosts is the Blackboard, which the orchestrator fills with every
    host's result after each lockstep round.  The inventory is the full list
    of hosts, enabling Callbacks to examine inventory information for ANY host.

    Three node types exist: ``Shell`` (a remote OPERATION leaf), ``Callback``
    (a leaf that runs imperative local processing) and ``Sequence`` (a
    COMPOSITE that runs its children in order).
    """
    # OPERATION leaf, targeted at the "linux" group only.  On non-matching
    # hosts run_operation returns None (NULL OBJECT).
    yield Shell("uname -s", group="linux", name="detect-os")

    # Imperative result-processing, now IN A NODE.  A Callback runs local
    # Python for each host (in lockstep) and records its return value to the
    # blackboard under its own tree path -- exactly like a Shell -- but it
    # performs NO remote OPERATION.  This one reads its host's round-1
    # detect-os result off the shared board (safe: the barrier guarantees
    # every host recorded before this round began).
    def uname_of(host):
        resp = board.results["detect-os"].get(host.name)
        return resp.value["stdout"] if resp is not None else None

    yield Callback(uname_of, group="linux", name="uname")

    # Like every node, the Callback's per-host results are on the board under
    # its path; roll them up here (a Callback runs per host, so cross-host
    # aggregation is a read of the board, indexed by host name).
    uname_by_host = {
        host: response.value
        for host, response in board.results["uname"].items()
        if response is not None
    }
    logging.info(f"uname results by host: {uname_by_host}")

    # A PUT operation mutates host state, so its response reports
    # `changed` -- the honest home for that flag (an OPERATION actually
    # runs on the host).  Collect which hosts changed.
    yield Shell(
        "touch /tmp/reemote-demo", method=Method.PUT, group="linux", name="mark"
    )
    changed_hosts = [
        host
        for host, response in board.results["mark"].items()
        if response is not None and response.changed
    ]
    logging.info(
        f"operation changed any host: {bool(changed_hosts)} "
        f"(changed hosts: {changed_hosts})"
    )

    # PROGRAM COMPOSITION: a factory returns a reusable Sequence.  The factory
    # takes ``name`` (for blackboard path uniqueness) and ``board`` (because the
    # Callback inside needs to read results).  This demonstrates how to build
    # reusable Sequence components that can be composed into larger trees.
    yield make_system_probe("probe", board)

    # PROGRAM COMPOSITION: another factory-produced Sequence, this one capturing
    # the installed-package list before/after an install, with a Callback
    # comparing them.  The Callback reads results from rounds N and N+2 (skipping
    # the install round), demonstrating cross-round blackboard reads.
    yield make_apt_install_probe("apt-install", board, packages=["cowsay"])

    # PROGRAM COMPOSITION: a factory-produced Sequence that examines the FULL
    # inventory (not just the current host).  The Callback inside can access
    # inventory information for ANY host, enabling cross-host analysis like
    # "how many linux hosts are there?" or "which hosts are webservers?".
    yield make_inventory_report("inventory-report", inventory)

    # PROGRAM COMPOSITION: prepare a file on the control host, then distribute
    # it to all hosts.  The preparation runs ONLY on the control host (via group
    # filtering), and the distribution runs on ALL hosts (reading the file path
    # from the control host's blackboard result).  This demonstrates the "run
    # once, then run on all" pattern using existing group filtering mechanisms.
    yield make_file_distribution("file-dist", board, inventory)

    # ERROR HANDLING: demonstrate the "catch and record" pattern.  A Sequence
    # with three Shells: one succeeds, one simulates an SSH error (the command
    # "ssh-error" triggers a simulated exception in run_operation), and one
    # succeeds after the error -- showing that execution continues for other
    # commands even when one fails.  The error is recorded in the blackboard
    # as an error result, not propagated (so the run doesn't crash).
    yield Sequence(
        Shell("echo before-error", name="before-error"),
        Shell("ssh-error", name="ssh-error"),  # simulates SSH connection error
        Shell("echo after-error", name="after-error"),
        name="error-demo",
    )

    # A Callback that prints whether the apt-install changed the package list.
    # It reads the apt-install.check result (from the round just above) and
    # prints "changed" or "not changed" per host.
    def print_apt_changed(host):
        check_resp = board.results["apt-install.check"].get(host.name)
        if check_resp is None:
            return  # NULL OBJECT: host was group-filtered
        changed = check_resp.value["changed"]
        print(f"{host.name}: {'changed' if changed else 'not changed'}")

    yield Callback(print_apt_changed, group="linux", name="print-apt-changed")

    # Nested COMPOSITEs: a Sequence accepts Shell leaves or other Sequence
    # nodes to any depth.  Below is 3 levels deep (root -> outer -> inner
    # -> innermost).
    yield Sequence(
        Shell("echo step-1", name="step-1"),
        Sequence(
            Shell("echo step-2", name="step-2"),
            Sequence(
                Shell("echo step-3", name="step-3"),
                Shell("echo step-4", name="step-4"),
                name="innermost",
            ),
            name="inner",
        ),
        # OPERATION leaf moved INSIDE the composite so `uname -a` runs here --
        # between step-4 (the end of inner) and step-5 -- immediately before the
        # Callback that consumes it (a Callback can only read a result already
        # on the board).  Its blackboard key is now the dot-joined path
        # "outer.detect-os-full" (it was the top-level "detect-os-full").
        Shell("uname -a", group="linux", name="detect-os-full"),
        # CALLBACK leaf nested in the composite: runs the module-level
        # uname_a_check per host, reading this host's detect-os-full result
        # recorded the round just above.  Bound to `board` via the lambda (a
        # module-level function cannot close over it).  Blackboard key becomes
        # "outer.uname-a-check".
        Callback(
            lambda host: uname_a_check(host, board),
            group="linux",
            name="uname-a-check",
        ),
        Shell("echo step-5", name="step-5"),
        name="outer",
    )
    # Note: the root's return value is discarded by the driver; results are
    # read from the blackboard, indexed by tree path and host.  The blackboard
    # is inspected by the test after execute() returns, giving the caller
    # access to all results for post-run analysis.


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# The demo tree's 23 leaf paths in EXECUTION order.  board.results is a dict, so
# its key order is the insertion (= round = execution) order; the ordering test
# below compares against this list, making the order a tested property.
EXPECTED_ORDER = [
    "detect-os",
    "uname",
    "mark",
    "probe.whoami",
    "probe.user-check",
    "apt-install.list-before",
    "apt-install.install",
    "apt-install.list-after",
    "apt-install.check",
    "inventory-report.report",
    "file-dist.prepare",
    "file-dist.copy",
    "error-demo.before-error",
    "error-demo.ssh-error",
    "error-demo.after-error",
    "print-apt-changed",
    "outer.step-1",
    "outer.inner.step-2",
    "outer.inner.innermost.step-3",
    "outer.inner.innermost.step-4",
    "outer.detect-os-full",
    "outer.uname-a-check",
    "outer.step-5",
]


def _demo_inventory() -> List[SimulatedHost]:
    """The standard mixed inventory the demo tests run over."""
    return [
        SimulatedHost("web-1", ("linux", "webservers", "control"), latency=0.0),
        SimulatedHost("web-2", ("linux", "webservers"), latency=0.0),
        SimulatedHost("win-1", ("windows",), latency=0.0),
    ]


@pytest.fixture
def demo_run():
    """Run the full ``demo_script`` once and expose its results.

    Follows the conftest.py convention: a SYNC fixture that drives the async
    ``execute()`` via ``asyncio.run``, so the tests below are plain sync
    functions.  Function-scoped -- each test gets a fresh run (cheap: latency=0).
    """
    inventory = _demo_inventory()
    board = Blackboard()
    success = asyncio.run(
        execute(lambda b: demo_script(b, inventory), inventory, board)
    )
    return SimpleNamespace(success=success, board=board, inventory=inventory)


@pytest.mark.asyncio
async def test_shell_and_callback():
    """A Shell records its result; a Callback reads it from the blackboard.

    Mirrors the demo_script ``detect-os`` Shell followed by the ``uname``
    Callback that extracts stdout from the Shell's blackboard entry.
    """
    inventory = [
        SimulatedHost("host-a", ("linux",), latency=0.0),
        SimulatedHost("host-b", ("linux",), latency=0.0),
    ]
    board = Blackboard()

    async def script(b: Blackboard, inv):
        # Shell leaf: simulated remote command, group-filtered to "linux".
        yield Shell("uname -s", group="linux", name="detect-os")

        # Callback leaf: reads the Shell's result from the blackboard.
        def uname_of(host):
            resp = b.results["detect-os"].get(host.name)
            return resp.value["stdout"] if resp is not None else None

        yield Callback(uname_of, group="linux", name="uname")

    success = await execute(lambda b: script(b, inventory), inventory, board)

    assert success is True

    # Shell results recorded for both hosts.
    assert "detect-os" in board.results
    assert set(board.results["detect-os"]) == {"host-a", "host-b"}
    for resp in board.results["detect-os"].values():
        assert resp.error is False
        assert "uname -s" in resp.value["command"]

    # Callback results recorded for both hosts.
    assert "uname" in board.results
    for host_name in ("host-a", "host-b"):
        resp = board.results["uname"][host_name]
        assert resp.value is not None
        assert host_name in resp.value  # simulated stdout contains host name


@pytest.mark.asyncio
async def test_group_filtering():
    """Hosts not matching the group record None (Null Object pattern)."""
    inventory = [
        SimulatedHost("linux-1", ("linux",), latency=0.0),
        SimulatedHost("win-1", ("windows",), latency=0.0),
    ]
    board = Blackboard()

    async def script(b: Blackboard, inv):
        yield Shell("uname -s", group="linux", name="detect-os")

        def uname_of(host):
            resp = b.results["detect-os"].get(host.name)
            return resp.value["stdout"] if resp is not None else None

        yield Callback(uname_of, group="linux", name="uname")

    success = await execute(lambda b: script(b, inventory), inventory, board)

    assert success is True

    # linux-1 gets a real result, win-1 gets None (group-filtered).
    assert board.results["detect-os"]["linux-1"] is not None
    assert board.results["detect-os"]["win-1"] is None
    assert board.results["uname"]["linux-1"] is not None
    assert board.results["uname"]["win-1"] is None


# ---------------------------------------------------------------------------
# Group A -- whole-tree integration
# ---------------------------------------------------------------------------


def test_demo_completes(demo_run):
    """execute() runs the full tree to completion and reports success."""
    assert demo_run.success is True


def test_demo_execution_order(demo_run):
    """Leaves execute in tree/lockstep order (composite-traversal spine)."""
    assert list(demo_run.board.results) == EXPECTED_ORDER


# ---------------------------------------------------------------------------
# Group B -- one concern per test, against the demo blackboard
# ---------------------------------------------------------------------------


def test_demo_null_object_group_filtering(demo_run):
    """The windows host is filtered out of linux-only leaves (Null Object)."""
    board = demo_run.board
    # win-1 (windows) records None on the linux-only leaves ...
    assert board.results["detect-os"]["win-1"] is None
    assert board.results["uname"]["win-1"] is None
    assert board.results["mark"]["win-1"] is None
    # ... while the linux hosts record real results.
    assert board.results["detect-os"]["web-1"] is not None
    assert board.results["uname"]["web-2"].value is not None


def test_demo_put_reports_changed(demo_run):
    """A successful PUT (idempotent) operation reports ``changed``."""
    board = demo_run.board
    assert board.results["mark"]["web-1"].changed is True
    assert board.results["mark"]["web-2"].changed is True
    # The filtered windows host never ran the PUT.
    assert board.results["mark"]["win-1"] is None


def test_demo_catch_and_record_error(demo_run):
    """A simulated SSH error is recorded, not propagated; the run continues."""
    board = demo_run.board
    assert board.results["error-demo.ssh-error"]["web-1"].error
    # The Shell AFTER the error still ran cleanly (execution was not aborted).
    after = board.results["error-demo.after-error"]["web-1"]
    assert not after.error
    assert after.value is not None


def test_demo_run_once_control_group(demo_run):
    """Run-once steps (group="control") record a real result only on web-1."""
    board = demo_run.board
    for path in ("inventory-report.report", "file-dist.prepare"):
        assert board.results[path]["web-1"] is not None
        assert board.results[path]["web-2"] is None
        assert board.results[path]["win-1"] is None


def test_demo_cross_host_read(demo_run):
    """Every host's copy step reads the control host's prepared path."""
    board = demo_run.board
    for host_name in ("web-1", "web-2", "win-1"):
        copy_resp = board.results["file-dist.copy"][host_name]
        assert copy_resp is not None
        assert copy_resp.value["copied_from_host"] == "web-1"


def test_demo_cross_round_callback_reads(demo_run):
    """Callbacks read earlier rounds off the board (cross-round / cross-depth)."""
    board = demo_run.board
    for host_name in ("web-1", "web-2"):
        # `uname` extracted stdout from the round-1 `detect-os` Shell.
        detect = board.results["detect-os"][host_name].value["stdout"]
        assert board.results["uname"][host_name].value == detect
        # `outer.uname-a-check` (module-level callable bound via lambda)
        # summarised `outer.detect-os-full`, recorded the round just above it.
        full = board.results["outer.detect-os-full"][host_name].value["stdout"]
        assert board.results["outer.uname-a-check"][host_name].value == {
            "full": full,
            "words": len(full.split()),
        }


def test_demo_print_apt_changed(capsys):
    """The status Callback prints per linux host; the filtered host is silent.

    Runs the demo inline (rather than via ``demo_run``) so the prints happen
    while ``capsys`` capture is active.
    """
    inventory = _demo_inventory()
    board = Blackboard()
    asyncio.run(execute(lambda b: demo_script(b, inventory), inventory, board))

    captured = capsys.readouterr()
    assert "web-1: not changed" in captured.out
    assert "web-2: not changed" in captured.out
    assert "win-1" not in captured.out


def test_apt_install_probe_validation():
    """make_apt_install_probe validates its whole parameter SET via pydantic.

    Mirrors reemote: an invalid set raises ``ValidationError`` at BUILD time
    (before any host runs) -- a bad package name, an empty list, a version pin
    with multiple packages, or an unknown option (``extra="forbid"``).
    """
    board = Blackboard()

    # A shell-metacharacter package name violates the PackageName pattern.
    with pytest.raises(ValidationError):
        make_apt_install_probe("x", board, packages=["cowsay; rm -rf /"])

    # An empty package list violates min_length=1.
    with pytest.raises(ValidationError):
        make_apt_install_probe("x", board, packages=[])

    # A version pin with more than one package violates the model_validator.
    with pytest.raises(ValidationError):
        make_apt_install_probe("x", board, packages=["a", "b"], version="1.0")

    # An unknown option violates extra="forbid" (here a typo'd `sudoo`).
    with pytest.raises(ValidationError):
        make_apt_install_probe("x", board, packages=["a"], sudoo=True)

    # A valid set builds a Sequence; sudo/update are accepted and update=True
    # prepends an `apt-install.update` leaf (5 children total).
    seq = make_apt_install_probe(
        "apt-install", board, packages=["cowsay"], sudo=True, update=True
    )
    assert isinstance(seq, Sequence)
    assert [child.name for child in seq.children] == [
        "update",
        "list-before",
        "install",
        "list-after",
        "check",
    ]
    # sudo prefixes the commands; the update leaf carries it too.
    assert seq.children[0].command == "sudo apt-get update"
    assert seq.children[2].command == "sudo apt-get install -y cowsay"

    # A version pin with exactly ONE package is valid and hits the command.
    pinned = make_apt_install_probe(
        "apt-install", board, packages=["cowsay"], version="1.0"
    )
    assert pinned.children[1].command == "apt-get install -y cowsay=1.0"
