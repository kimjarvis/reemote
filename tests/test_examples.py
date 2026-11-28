# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Hygiene checks for the runnable scripts under ``examples/``.

Operation examples drive *real* SSH hosts, so the suite never executes them end
to end: the demo inventory is shared and the operations mutate it. Their
arguments still have to satisfy the request hashability rule --
``compose(dedupe=True)`` hashes every ``args``/``kwargs`` value, so
``OperationRequest.__post_init__`` rejects an unhashable one -- and only a few
examples are reachable from any other pytest. So the examples are covered two
ways, both of which stay offline:

* **statically** -- every ``op(...)`` call in every example, and in the
  migrated role modules under ``src/reemote/roles/`` once they land (§17), is
  scanned for a mutable container: a ``list``/``dict``/``set`` or comprehension,
  a call that returns one, or a name bound to either (including values unpacked
  with ``**``);
* **dynamically** -- every example module is *imported* (module-level code
  builds the constants its deploys use and opens no connection, the same
  import the docs build already performs) and each deploy-shaped entrypoint is
  drained against a lazily built host, so the requests really are constructed
  and really are hashed. ``op()`` construction sends nothing, so no transport
  is ever reached. A deploy that yields a *barrier* is drained only up to that
  first barrier (``drain(..., until_barrier=True)``) -- resuming past it would
  inject ``None`` where the driver's fact value, action ``Result``, wave report
  or control return belongs -- so a barrier-bearing stream cannot masquerade as
  a lost request (§11.8). No barrier-free example is affected.

A dozen deliberately network-free scripts -- the in-memory role demonstration,
the in-memory proxied apt demonstration, the in-deploy action guard, and the
nine in-memory wave demonstrations under ``examples/demonstrations/waves/`` --
are additionally run end to end as subprocesses, because they can be executed
without touching the demo hosts at all.
"""

import ast
import importlib.util
import pathlib
import subprocess
import sys
import types

import pytest

from reemote import Host, Result
from reemote.core import OperationRequest
from reemote.stages import Barrier, CheckpointRequest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES_ROOT = REPO_ROOT / "examples"
ROLES_ROOT = REPO_ROOT / "src" / "reemote" / "roles"

# Containers and calls that produce one: both are unhashable, so both are
# rejected at request construction. ``tuple(...)``/``frozenset(...)`` are the
# sanctioned way to build an immutable value, so they stop the search.
MUTABLE_CONTAINERS = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
MUTABLE_CONSTRUCTORS = {"list", "dict", "set", "sorted"}
MUTABLE_METHODS = {"split", "splitlines", "items", "keys", "values", "copy"}
IMMUTABLE_WRAPPERS = {"tuple", "frozenset"}

# Entrypoints that close over a *fact snapshot* (or a frozen apply-outcome
# set) rather than a host. The value built here mirrors the shape the example
# reads between its runs, so the deploy body can be exercised offline; keyed
# by script and function name, because two scripts share the factory name but
# not the fact behind it.
SNAPSHOT_ARGS = {
    (
        "examples/demonstrations/composition/two_deploy_split.py",
        "build_configure_nginx",
    ): lambda name: {name: ["nginx"]},
    (
        "examples/demonstrations/composition/conf_d_cleanup.py",
        "build_cleanup_conf_d",
    ): lambda name: {
        name: [
            "/etc/datadog-agent/conf.d/cpu/conf.yaml",
            "/etc/datadog-agent/conf.d/legacy_check/conf.yaml",
        ]
    },
    (
        "examples/actions/deploy_action_deploy.py",
        "build_configure_nginx",
    ): lambda name: {name: Result(exit_status=0, stdout="nginx/1.24.0", stderr="")},
    (
        "examples/modules/server/server_kill.py",
        "build_kill",
    ): lambda name: {name: {4321: {"command": "nginx"}}},
    # The lxd alias example freezes one cached-image fingerprint per host
    # between its runs; a 64-char hex str is the shape main() selects.
    (
        "examples/modules/lxd/lxd_image_alias.py",
        "build_alias_image",
    ): lambda name: {name: "0" * 64},
    # The fetch_url example freezes the Composer installer signature main()
    # fetches control-side into the download's checksum; a 96-char hex str is
    # the sha384 shape main() reads.
    (
        "examples/modules/files/files_fetch_url.py",
        "build_installer",
    ): lambda name: "0" * 96,
}


# Entrypoints that only behave inside a driver run. The in-deploy action guard
# reads a flag ``run`` sets, so calling the deploy on its own lets the action
# through instead of raising -- the request it would build is a static literal
# (covered by the scan below) and the script is run end to end further down.
DRIVER_ONLY_ENTRYPOINTS = {
    ("examples/actions/action_in_deploy_guard.py", "illegal_deploy"),
    # Every parent request in the outputs-relay demo is built *after* the
    # scoped child's fact barrier, so a break-at-barrier drain reaches none of
    # them; the op literals are covered by the static scan and the full body by
    # the end-to-end offline run further down.
    ("examples/demonstrations/waves/role_outputs_relay.py", "parent"),
}

# Scripts that run without a connection: in-memory hosts, or a guard that fires
# during prepare before any transport is opened. The nine wave demonstrations
# under ``examples/demonstrations/waves/`` inject ``SimulatedHost`` through
# ``run_waves``'s ``host_factory``, so their whole barrier path is exercised in
# memory (§19).
OFFLINE_EXAMPLES = (
    "examples/demonstrations/composition/composed_group_tag_roles.py",
    "examples/demonstrations/environment/environment_apt_proxy.py",
    "examples/actions/action_in_deploy_guard.py",
    "examples/demonstrations/waves/barrier_fact.py",
    "examples/demonstrations/waves/checkpoint_handler.py",
    "examples/demonstrations/waves/barrier_act.py",
    "examples/demonstrations/waves/control_fetch.py",
    "examples/demonstrations/waves/scope_two_instances.py",
    "examples/demonstrations/waves/role_outputs_relay.py",
    "examples/demonstrations/waves/vars_typed.py",
    "examples/demonstrations/waves/dry_one_wave.py",
    "examples/demonstrations/waves/run_waves_report.py",
    # The zabbix API example drives the control-side JSON-RPC seam against an
    # in-memory fake server, so it runs end to end with no network (§16.4).
    "examples/modules/zabbix/zabbix_api.py",
)


def example_files() -> list[pathlib.Path]:
    """Every example script, in a stable order."""
    return sorted(
        path for path in EXAMPLES_ROOT.rglob("*.py") if "__pycache__" not in path.parts
    )


def relative(path: pathlib.Path) -> str:
    """The path as it appears in docs and run commands."""
    return path.relative_to(REPO_ROOT).as_posix()


def static_scan_files() -> list[pathlib.Path]:
    """Every module the immutable-container ``op(...)`` rule must hold across.

    That is the example scripts *and* the migrated role modules under
    ``src/reemote/roles/``: the ``op(...)`` calls carrying ``list``/``dict``
    arguments move there when the roles land, and a mutable default in a role
    module is a library bug, not an example bug (§17.5). The private helpers
    (``__init__.py``, ``_render.py``) build no ``op(...)`` requests, so they are
    skipped. Unlike ``example_files`` this set is only walked by the *static*
    scan -- role modules get their own offline drain under ``tests/roles/``, not
    the example import harness.
    """
    files = list(example_files())
    if ROLES_ROOT.is_dir():
        files += sorted(
            path
            for path in ROLES_ROOT.rglob("*.py")
            if "__pycache__" not in path.parts and not path.name.startswith("_")
        )
    return files


# --- static: no mutable container reaches an op(...) ------------------------


def assigned_values(tree: ast.Module) -> dict:
    """Every name assigned anywhere in the module, mapped to its value node.

    Used to follow ``packages=MY_PACKAGES`` back to the literal or call that
    produced it, since the request would carry exactly that object.
    """
    bound: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(target.id, node.value)
    return bound


def mutable_reason(node: ast.expr, bound: dict, seen: frozenset) -> str | None:
    """Why *node* itself would be unhashable as a request value, if it would.

    ``seen`` guards the name-following recursion; a ``Call`` whose result is
    unknown is left alone (that is the dynamic check's job).
    """
    if isinstance(node, MUTABLE_CONTAINERS):
        return type(node).__name__
    if isinstance(node, ast.Name):
        if node.id in seen:
            return None
        target = bound.get(node.id)
        if target is None:
            return None
        return mutable_reason(target, bound, seen | {node.id})
    if isinstance(node, ast.Call):
        func = node.func
        names = (
            {func.id}
            if isinstance(func, ast.Name)
            else {func.attr}
            if isinstance(func, ast.Attribute)
            else set()
        )
        if names & (MUTABLE_CONSTRUCTORS | MUTABLE_METHODS):
            return "result of a mutable-returning call"
    return None


def is_immutable_wrapper(node: ast.expr) -> bool:
    """Whether *node* spells ``tuple(...)``/``frozenset(...)``: hashable."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in IMMUTABLE_WRAPPERS
    )


def find_mutable(node: ast.expr, bound: dict, seen: frozenset = frozenset()) -> str | None:
    """The first unhashable container at or below *node*, pruning safe spots."""
    reason = mutable_reason(node, bound, seen)
    if reason:
        return reason
    if isinstance(node, ast.JoinedStr):
        return None  # an f-string renders a str: always hashable
    if isinstance(node, ast.Subscript):
        # ``MAPPING[key]`` yields the mapping's *value*, not the mapping, and
        # that value's type is only known at run time -- the dynamic check
        # builds those requests for real.
        return None
    if is_immutable_wrapper(node):
        return None  # tuple(...)/frozenset(...) is the sanctioned spelling
    for child in ast.iter_child_nodes(node):
        found = find_mutable(child, bound, seen)
        if found:
            return found
    return None


def is_op_call(node: ast.AST) -> bool:
    """Whether *node* calls ``op`` -- the request builder in every example."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "op"
    )


def mutable_op_arguments(source: str, rel: str) -> list[str]:
    """Offending ``op(...)`` arguments in one module's source, as notes."""
    tree = ast.parse(source)
    bound = assigned_values(tree)
    offenders = []
    for call in ast.walk(tree):
        if not is_op_call(call):
            continue
        # args[0] is the operation function itself, never a data value.
        checked = [(f"args[{index}]", expr) for index, expr in enumerate(call.args[1:])]
        for keyword in call.keywords:
            if keyword.arg is not None:
                checked.append((f"kwargs[{keyword.arg!r}]", keyword.value))
                continue
            # ``**MAPPING`` spreads a dict: the dict is consumed here, so only
            # its *values* end up in the request.
            spread = keyword.value
            if isinstance(spread, ast.Name):
                spread = bound.get(spread.id, spread)
            if isinstance(spread, ast.Dict):
                for key, value in zip(spread.keys, spread.values):
                    if value is None:  # ``**other`` nested inside a dict literal
                        continue
                    reason = find_mutable(value, bound)
                    if reason:
                        location = f"kwargs(**{getattr(key, 'value', '?')})"
                        offenders.append(f"{rel}:{call.lineno} {location}: {reason}")
                # A spread of a computed mapping cannot be resolved here; the
                # dynamic check builds those requests for real.
                continue
        for location, expr in checked:
            reason = find_mutable(expr, bound)
            if reason:
                offenders.append(f"{rel}:{call.lineno} {location}: {reason}")
    return [note for note in offenders if note]


def test_no_example_passes_a_mutable_container_to_op():
    """The documented immutable-container rule holds across every example.

    Examples are not executed by the suite (they target the shared demo
    hosts), so this scan is what keeps their ``op(...)`` calls legal: an
    unhashable value would raise ``UnhashableArgumentError`` at the deploy line.
    The migrated role modules are scanned by the same rule (``static_scan_files``).
    """
    offenders = []
    for path in static_scan_files():
        offenders += mutable_op_arguments(path.read_text(), relative(path))

    assert not offenders, "op(...) arguments must stay hashable:\n" + "\n".join(offenders)


# The scan is only worth having if it really bites, so both directions are
# pinned: each planted offender must be named, and the legal spellings --
# including the ones the pruners above exist to allow -- must stay quiet.
PLANTED_OFFENDERS = {
    "a list literal": 'op(dnf.packages, packages=["nginx"])\n',
    "a name bound to a list": 'PACKAGES = ["nginx"]\nop(dnf.packages, packages=PACKAGES)\n',
    "a mutable-returning call": "op(dnf.packages, packages=sorted(names))\n",
    "a list nested in a tuple": 'op(dnf.packages, packages=(["nginx"],))\n',
    "a value spread from a constant": (
        'SPEC = {"packages": ["nginx"]}\nop(dnf.packages, **SPEC)\n'
    ),
}

LEGAL_SPELLINGS = (
    'op(dnf.packages, packages=("nginx",))\n',
    "op(dnf.packages, packages=tuple(sorted(names)))\n",
    'op(dnf.packages, name=f"{len(names)} packages", packages=("nginx",))\n',
    'SPEC = {"packages": ("nginx",)}\nop(dnf.packages, **SPEC)\n',
    # A subscript yields the mapping's value, which the scan cannot type; the
    # dynamic check builds those requests for real.
    "op(files.edit, transform=SPOOL_TRANSFORMS[host.name])\n",
)


@pytest.mark.parametrize("kind", sorted(PLANTED_OFFENDERS))
def test_the_scan_flags_a_planted_offender(kind: str):
    found = mutable_op_arguments(PLANTED_OFFENDERS[kind], "planted.py")

    assert found, f"the scan missed {kind}"


@pytest.mark.parametrize("source", LEGAL_SPELLINGS)
def test_the_scan_accepts_the_legal_spellings(source: str):
    assert not mutable_op_arguments(source, "planted.py")


# --- dynamic: import each example and really build its requests --------------


def import_example(path: pathlib.Path):
    """Import one example module by file path, without touching ``main()``.

    Every example guards its entrypoint with ``if __name__ == "__main__"``, and
    its module-level code only builds constants, so importing is side-effect
    free and opens no connection.
    """
    name = "example_module_" + relative(path).replace("/", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {relative(path)}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def required_parameters(node: ast.FunctionDef) -> int:
    """How many positional arguments *node* needs before defaults kick in."""
    return len(node.args.posonlyargs) + len(node.args.args) - len(node.args.defaults)


def entrypoint_parameter_name(node: ast.FunctionDef) -> str | None:
    """The name of the first required positional parameter, if any."""
    positional = [*node.args.posonlyargs, *node.args.args]
    index = len(positional) - len(node.args.defaults) - 1
    return positional[index].arg if index >= 0 else None


def deploy_entrypoints(tree: ast.Module) -> list[tuple[str, str]]:
    """Deploy-shaped module-level callables, as ``(name, how)`` pairs.

    ``how`` is ``"host"`` for a deploy (or a composed ``deploy`` object) that
    takes a host, ``"snapshot"`` for a factory closing over a fact snapshot,
    and ``"none"`` for a zero-argument factory. ``main`` is never a candidate:
    it drives the network.
    """
    entrypoints = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) or not isinstance(
            node, (ast.FunctionDef, ast.Assign)
        ):
            continue
        if isinstance(node, ast.FunctionDef):
            if not any(is_op_call(inner) for inner in ast.walk(node)):
                continue
            name = node.name
            if name == "main":
                continue
            required = required_parameters(node)
            if required > 1:
                continue
            if required == 0:
                entrypoints.append((name, "none"))
            elif entrypoint_parameter_name(node) == "host":
                entrypoints.append((name, "host"))
            else:
                entrypoints.append((name, "snapshot"))
            continue
        for target in node.targets:
            # ``deploy = compose(...)`` / ``sub_deploy(...)``: a composed deploy
            # is a host-taking callable, so it is worth driving directly.
            if isinstance(target, ast.Name) and target.id == "deploy" and isinstance(
                node.value, ast.Call
            ):
                entrypoints.append((target.id, "host"))
    return entrypoints


def smoke_host(module):
    """A host for the example's own first inventory entry.

    ``Host.from_inventory`` only validates and stores the entry: the asyncssh
    connection is opened lazily on the first command, and building a request
    sends none.
    """
    inventory = getattr(module, "INVENTORY", None)
    if isinstance(inventory, (list, tuple)) and inventory:
        entry = inventory[0]
        if isinstance(entry, dict):
            return Host.from_inventory(entry)
    return Host(name="web1")


def seed_spool_transforms(module, host: Host) -> None:
    """Fill the spool-and-install recipe's per-host transform table.

    Those examples build ``SPOOL_TRANSFORMS`` between their fact snapshot and
    run 1 (in ``main``), so the table is still empty when a deploy is driven
    offline. The seed transform is a plain function -- hashable, and identity-
    stable for one request -- which is all the request needs.
    """
    transforms = getattr(module, "SPOOL_TRANSFORMS", None)
    if isinstance(transforms, dict):
        transforms.setdefault(host.name, lambda current: "seeded")


# Sentinel: this entrypoint takes no argument at all.
_NO_ARG = object()


def drain(stream, *, until_barrier: bool = False) -> list:
    """Pull a deploy stream into a list, optionally stopping at the first barrier.

    Without ``until_barrier`` this is a plain ``list(...)``: the body runs to
    exhaustion. A barrier-bearing deploy -- a wave demonstration, or a role body
    drained offline -- must instead be abandoned at the first barrier it yields.
    Resuming past a barrier sends ``None`` back into the ``yield fact(...)`` /
    ``yield act(...)`` expression where a fact value, an action ``Result``, an
    ``ApplyReport`` or a control return belongs, so the body either crashes on
    the ``None`` or builds a later ``op(...)`` it never yields -- which the
    built-versus-yielded counter reports as a lost request. Stopping at the
    barrier keeps that accounting balanced while still constructing (and so
    hashing) every request the body reached before it (§11.8).
    """
    items = []
    for item in stream:
        items.append(item)
        if until_barrier and isinstance(item, Barrier):
            break
    return items


def build_requests(factory, argument, host: Host, *, until_barrier: bool = False) -> list:
    """Call an entrypoint and flatten whatever generator it produces.

    A factory that returns a deploy (``package_role(*packages)``,
    ``build_kill(snapshot)``) is called again with the host, so both layers get
    exercised. ``until_barrier`` is handed straight to :func:`drain`; it stays
    ``False`` for the barrier-free example corpus and is set by the wave
    demonstrations (§19).
    """
    produced = factory(argument) if argument is not _NO_ARG else factory()
    if callable(produced) and not isinstance(produced, types.GeneratorType):
        produced = produced(host)
    return drain(produced, until_barrier=until_barrier)


def requests_in(items: list) -> list[OperationRequest]:
    """Only the ``OperationRequest`` items of a drained entrypoint.

    A deploy that yields ``sub_deploy`` wrappers or composed sub-streams can
    hand back callables as well, so the drain is filtered rather than assumed.
    """
    return [item for item in items if isinstance(item, OperationRequest)]


def test_drain_stops_at_the_first_barrier():
    """``until_barrier`` abandons the stream the moment it yields a barrier.

    The tail is never reached, so a body that would build-but-not-yield past the
    barrier is not exercised -- exactly the lost-request case the flag guards.
    """
    before = object()
    barrier = CheckpointRequest(name="configure done")
    after = object()

    def stream():
        yield before
        yield barrier
        yield after

    assert drain(stream(), until_barrier=True) == [before, barrier]


def test_drain_without_the_flag_runs_to_exhaustion():
    """The default drain is the plain full pull the barrier-free corpus needs."""
    barrier = CheckpointRequest(name="configure done")
    tail = object()

    def stream():
        yield barrier
        yield tail

    assert drain(stream()) == [barrier, tail]


@pytest.mark.parametrize("path", example_files(), ids=relative)
def test_example_builds_hashable_requests_offline(path: pathlib.Path):
    """Every request an example can build is built here, and hashed.

    Construction is where the hashability rule is enforced, so this is the
    offline proof that no example trips it -- including arguments computed at
    runtime, which the static scan cannot see.
    """
    module = import_example(path)
    entrypoints = deploy_entrypoints(ast.parse(path.read_text()))
    if not entrypoints:
        # A read-only fact script: it builds no requests by design, so the
        # clean import above is the whole check.
        return

    rel = relative(path)
    host = smoke_host(module)
    seed_spool_transforms(module, host)
    built = 0
    for name, how in entrypoints:
        if (rel, name) in DRIVER_ONLY_ENTRYPOINTS:
            continue
        factory = getattr(module, name, None)
        assert callable(factory), f"{rel}: {name} is not callable"
        argument: dict | Host | object
        if how == "host":
            argument = host
        elif how == "snapshot":
            key = (rel, name)
            assert key in SNAPSHOT_ARGS, f"{rel}: {name} needs a snapshot fixture"
            argument = SNAPSHOT_ARGS[key](host.name)
        else:
            argument = _NO_ARG
        items = build_requests(factory, argument, host, until_barrier=True)
        requests = requests_in(items)
        built += len(requests)
        for request in requests:
            # The rule under test: every argument value participates in the
            # deduplication key, so the built request must hash.
            hash(request)

    assert built or all(
        (rel, name) in DRIVER_ONLY_ENTRYPOINTS for name, _how in entrypoints
    ), f"{rel}: no request built by {entrypoints}"


# --- end to end: the scripts that need no connection -------------------------


@pytest.mark.parametrize("script", OFFLINE_EXAMPLES)
def test_offline_example_runs_end_to_end(script: str):
    """Network-free examples run for real, so their whole path is covered.

    ``composed_group_tag_roles`` and ``environment_apt_proxy`` inject the test
    framework's ``SimulatedHost`` via ``run``'s ``host_factory`` and
    ``action_in_deploy_guard`` aborts during prepare before any transport
    opens, so none touches the demo inventory.
    """
    completed = subprocess.run(
        [sys.executable, script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        f"{script} exited {completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert completed.stdout, f"{script} produced no output"
    # A guarded failure is printed; an uncaught one dumps a traceback.
    assert "Traceback" not in completed.stdout + completed.stderr
