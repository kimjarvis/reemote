# Design note: composable roles as resumable deploy generators

| | |
|---|---|
| **Status** | Proposal — complete implementation design. No library code, no example refactor. |
| **Scope** | A role as a deploy-shaped generator that may yield barrier items; a wave-loop driver that resumes it between prepare/apply cycles; typed role variables; reporting; migration of the existing 14 role programs. |
| **Relation** | Implements "Alternative 3 (C1)" from the role-composition investigation, with role de-duplication deliberately dropped. The competing design is [role_composition.md](./role_composition.md) (Option B: `Role` class + `Session`). |
| **Non-goals** | Role de-duplication, a static dependency graph, whole-tree request preview, role identity or instance addressing, a `Role` base class, a `Session` object, a YAML front end. |

---

## 1. The idea in one page

A role is not a class. It is a **deploy factory**: an ordinary function that closes over
resolved variables and returns a deploy generator. The generator yields two kinds of item:

- `OperationRequest` — exactly what a deploy yields today;
- a **barrier** — `fact`, `act`, `checkpoint` or `control`.

The driver runs a **wave**: it pulls requests one per host in lockstep, materializes each
request and applies it immediately, and when every active host has parked on a barrier it
executes those barriers concurrently and **resumes each generator with the barrier's value**
via `gen.send(value)`. The value becomes the result of the `yield` expression.

```python
def apache(v: VarsSet[ApacheVars]) -> Deploy:
    """A role: resolved variables in, a deploy out."""

    def deploy(host: Host) -> Generator[StageItem, Any, ApacheOutputs]:
        hv = v.for_host(host)
        if not is_ubuntu(host):
            return ApacheOutputs(version="")

        yield from install(hv)(host)                              # stage: install

        result = yield act(Shell, command=apache_version_command(hv), name="apache -v")
        version = parse_apache_version(result["stdout"])

        ssl_exists = tuple(
            bool((yield fact(FileExists, path=vhost.certificate_file)))
            for vhost in hv.vhosts_ssl
        )

        plans = configure_plan(hv, version, ssl_exists)
        yield from configure({host.name: plans})(host)            # stage: configure

        wave_report = yield checkpoint("configure done")
        notified = changed_hosts(wave_report, notifying_names(plans))
        yield from handler({host.name: plans}, notified)(host)    # stage: handler

        return ApacheOutputs(version=version)

    return deploy
```

Four moving parts:

1. **A widened stream** — deploys may yield barriers as well as requests.
2. **Four barrier types** — the only things a sync generator body cannot do itself:
   await a fact, await an action, read the run report, await control-side IO.
3. **A wave loop** — `run_waves` owns hosts, lockstep, rendezvous, errors and the report.
4. **Role factories plus typed variables** — nesting is `yield from`; a dependency's
   outputs are its return value.

What is deliberately absent: any engine knowledge of *which role* is running, any
identity, any de-duplication, any stage declaration, any `Session`.

---

## 2. Why this works where the previous note said it could not

`role_composition.md` §5.3 proves that a role cannot be deploy-shaped from three facts
about the **current** invocation model:

1. `_prepare_host` drains `deploy_func(host)` to build the plan and then **re-invokes**
   it to build the generator `run_apply` will advance — a second, independent pass
   ([drivers.py:437-442](../src/reemote/drivers.py#L437-L442));
2. the two passes are compared request-by-request by value, raising
   `DeterminismError` on divergence;
3. a report does not exist until apply finishes.

Each impossibility is a consequence of **re-invocation**, not of the two-phase model.
Alternative 3 invokes the generator **once** and resumes it. The three objections dissolve:

| §5.3 objection | Under the wave model |
|---|---|
| An action inside a deploy would run during prepare | An action is a *yielded item*, executed by the driver **between** waves, never inside a pull. A direct `gather_action(...)` call in the body still raises `ActionInDeployError`. |
| A fact read in the body differs between prepare and apply | The body runs once and is suspended at the fact. The value is sent back after the barrier; there is no second pass to disagree with. |
| `changed_hosts` needs a report that does not exist yet | a checkpoint parks until the wave is applied and sends the wave's `ApplyReport` back. |

A second consequence is easy to miss and worth stating: **the "two-deploy outcome split"
is no longer needed for mere sequencing.** Because the driver materializes a request and
applies it *before pulling the next one*, "deploy 1 creates a file, deploy 2 edits it"
is one stream with no barrier. A barrier is required only when the role's **Python control
flow consumes a value** — a fact, an action result, a report, or a control-side fetch.

---

## 3. The stage stream

### 3.1 The union and the contract

```python
# The four barrier types of §4 as one alias. A PEP 604 union is valid in
# isinstance(), so the loop classifies an item with one check.
Barrier = FactRequest | ActionRequest | CheckpointRequest | ControlRequest

StageItem = OperationRequest | Barrier
Deploy = Callable[[Host], Generator[StageItem, Any, Any]]
# An `async def` factory returns exactly this type once awaited (§9).
```

The deploy contract is the current one, widened:

- one item per `yield`;
- every request built with `op()` must be yielded (counting is unchanged);
- every barrier built with `fact()`/`act()`/`checkpoint()`/`control()` must be yielded;
- yielding anything else raises `DeployRequestError` at the pull site;
- the body may `return value`; the driver records it, and a parent captures it with
  `yield from`.

`run_waves` charges barriers to the same `_RequestCounter` that catches a lost request, so
a built-but-unyielded barrier is reported exactly like a lost request -- the helpers still
say "request", read it as "stage item". The existing helper `op()` gains a
`_charge_built()` call so the four barrier constructors can share it; the `run()` messages
and their tests are untouched.

### 3.2 Composition

**The role vocabulary is `yield from`.** A role factory returns a deploy, a deploy is a
generator, and `yield from` is the sequence operator: it forwards every item — requests
*and* barriers — up the stream and returns the child's value. Nothing else is needed to
compose roles, and the top-level program is itself a role (§10.5).

`compose` and `sub_deploy` therefore stay **request-only**. Both remain public and both are
untouched for `run()`-era programs (20 + 38 call sites in `examples/`, 26 + 19 in `tests/`),
but a barrier handed to either is a `DeployRequestError` that names the mistake and the fix:

```text
host web1: compose() received a FactRequest; compose and sub_deploy sequence
OperationRequests only -- compose roles with `yield from` in a role generator,
where barriers are legal
```

Why reject rather than tolerate:

- **`compose(dedupe=True)` cannot be honest across roles.** `_effective_key` excludes both
  `name` and `scope`, so a request collapsed into another role's occurrence executes under
  *that* role's scope; a checkpoint scoped to the losing role then reads empty while the
  work is done. Keeping `compose` request-only also keeps dedupe where it is sound: inside
  one `run()` program, where no barrier and no checkpoint exist.
- **`sub_deploy`'s policies have no meaning on a barrier and no safe fallback.**
  `sudo`/`su`/`doas`/`name_prefix`/`when` are request fields; today each raises
  `AttributeError` naming neither the role nor the barrier, and *skipping* the barrier
  would mean `gen.send(None)` — handing the body a value it did not earn.
- **One rule instead of two.** "Barriers live in role generators; requests may go anywhere"
  is simpler to state, implement and test than "barriers pass through `compose` but not
  through `sub_deploy`'s policies".

One shared guard, ~8 lines, called from both wrappers before they touch a request field:

```python
# core.py — additive
def _require_request(item: Any, wrapper: str, host_name: str) -> OperationRequest:
    """Reject a barrier handed to a request-only combinator (§3.2)."""
    if isinstance(item, OperationRequest):
        return item
    raise DeployRequestError(
        f"host {host_name}: {wrapper}() received a {type(item).__name__}; compose and "
        "sub_deploy sequence OperationRequests only -- compose roles with `yield from` "
        "in a role generator, where barriers are legal"
    )
```

`compose`'s loop calls it before `_effective_key`; `sub_deploy`'s before reading `name`,
`sudo`, `su`, `doas` or `when`. Both widen their accepted input to `StageItem` and keep
their `OperationRequest` output type: the rejection is a runtime contract, not a typing one.

What a role gives up, and what replaces it:

| `compose`/`sub_deploy` gave | In the role vocabulary |
|---|---|
| `compose(a, b)` ordering | `yield from a(host)` then `yield from b(host)` in one factory — and the child's return value is capturable |
| `dedupe=True` across roles | nothing, by design: hoist the shared prerequisite at the composition site (§10.5) |
| `sub_deploy(when=...)` block guard | `if not is_ubuntu(host): return` at the top of the generator |
| `sub_deploy(name_prefix=...)` | `scope` (§3.3), which `changed_hosts` cannot mistake for a name |
| `sub_deploy(sudo=...)` over a child role | the child factory takes the privilege as one of its variables — a real loss, stated in §13 |

`when` on individual requests is unchanged and still evaluated at the execution boundary.
Host selection for barriers is manual and cannot be declared — a limitation of the model,
not of this implementation.

### 3.3 One new metadata field: `scope`

`yield from` flattens the stream: the driver sees a single sequence and cannot tell which
role emitted an item. Reporting and checkpoint slicing need that path, so add one field
that never reaches a host:

```python
# core.OperationRequest  — additive, defaulted
scope: tuple[str, ...] = ()      # report path, e.g. ("web", "apache")

# drivers.LogEntry       — additive, defaulted
scope: tuple[str, ...] = ()
```

Rules:

- `scope` is set only by `scoped(label, deploy)`, never by `op()`;
- it is **excluded** from `_effective_key`, exactly like `name`; with §3.2, dedupe only
  ever sees a barrier-free `run()` stream, so no collapsed request can land under another
  role's scope;
- it is included in `__hash__` and equality, because it is part of the request value and
  both phases of a *legacy* `run()` see the same scope (always `()` there);
- it is not a privilege or a guard, so it has no execution effect.

```python
def scoped(label: str, deploy: Deploy) -> Deploy:
    """Tag every item with a report path; never touches request.name."""
    def wrapped(host: Host) -> Generator[StageItem, Any, Any]:
        for item in deploy(host):
            yield replace(item, scope=(label,) + item.scope)
    return wrapped
```

This is the replacement for Option B's instance addressing. It must **not** be
implemented with `sub_deploy(name_prefix=...)`: that rewrites `OperationRequest.name`, and
every `changed_hosts(report, names)` call matches on `name`.

---

## 4. The four barrier types

A barrier is a frozen dataclass carrying the same two metadata fields — `name` (a human
label) and `scope` (the report path) — plus its own payload. There are exactly four, and they
exist because they are the four things a **sync** generator body cannot do:

| Barrier | Constructor | Sends back | Host contact | Purpose |
|---|---|---|---|---|
| `FactRequest` | `fact(cls, *args, **kwargs)` | the fact's value | one host | read remote state into control flow |
| `ActionRequest` | `act(cls, *args, **kwargs)` | `Result` (or action-specific value) | one host | run something now and consume the outcome |
| `CheckpointRequest` | `checkpoint(name="")` | the wave's `ApplyReport`, sliced to scope | none | read outcomes: `changed_hosts` |
| `ControlRequest` | `control(func, *args, **kwargs)` | the coroutine's return value | controller | async control-side IO (URL fetch, local file) |

### 4.1 `Fact`

```python
@dataclass(frozen=True)
class FactRequest:
    fact_cls: type
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    scope: tuple[str, ...] = ()

def fact(fact_cls: type, *args: Any, name: str = "", scope: tuple[str, ...] = (), **kwargs: Any) -> FactRequest
```

Execution: `await fact_cls.process(host, *args, **kwargs)` for **that** host. Every parked
host runs its own fact concurrently at the rendezvous, which reproduces `gather_fact`'s
fan-out without needing a host set: the wave loop already holds one barrier per host.

Semantics:

- a fact is a **read**; it is executed, never simulated. `dry` stops before the first
  barrier, so no fact runs there (§6.3), and no read-only preview mode is added (§16.6);
- no `FactCache` is involved — a barrier is an explicit, fresh read, not an operation's
  internal materialization-time read;
- errors propagate exactly as they do from `process` (`HostConnectionError`,
  `CommandNotFoundError`, ...), aggregated across hosts by the driver.

```python
exists = yield fact(FileExists, path="/etc/httpd/conf.d/ssl.conf")
distro = yield fact(LinuxDistribution)
major = distro["major"]
```

### 4.2 `Act`

```python
@dataclass(frozen=True)
class ActionRequest:
    action_cls: type
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    scope: tuple[str, ...] = ()

def act(action_cls: type, *args: Any, name: str = "", scope: tuple[str, ...] = (), **kwargs: Any) -> ActionRequest
```

Execution: `await action_cls.process(host, *args, **kwargs)`. `Shell.process` returns a
`Result` (`exit_status`, `stdout`, `stderr`); a non-zero exit is **data**, not an exception,
matching the action contract. Transport and privilege failures raise.

```python
result = yield act(Shell, command="/usr/sbin/apache2 -v", name="apache -v")
if result["exit_status"] == 0:
    version = parse_apache_version(result["stdout"])
```

`Act` is the reason the `ActionInDeployError` guarantee needs restating rather than
removing: a barrier is executed by the driver, outside the pull, so the guard must be held
**during pulls only**. `run_waves` installs it around each pull (see §5.3); a
`gather_action(...)` call written inside the body therefore still raises immediately, while
the driver's own `action_cls.process(...)` call is unaffected.

### 4.3 `Checkpoint`

```python
@dataclass(frozen=True)
class CheckpointRequest:
    name: str = ""
    scope: tuple[str, ...] = ()

def checkpoint(name: str = "", *, scope: tuple[str, ...] = ()) -> CheckpointRequest
```

A checkpoint touches no host. It participates in the rendezvous (so the wave closes), then
sends back an `ApplyReport` containing the log entries **of the wave that just ended**,
filtered to entries whose `scope` starts with the checkpoint's scope. There is no wrapper
type — the `yield` expression *is* an `ApplyReport`, the same type `run()` returns:

```python
wave_report = yield checkpoint("configure done")
notified = changed_hosts(wave_report, notifying_names(plans))
```

`changed_hosts` is unchanged, because it only reads `ApplyReport.log`. The payload carries no
name and no wave number, and neither is wanted: the name is the literal the body just wrote
(and is already on the record), and the wave is a position in the tree (§7.2), not a decision
input.

"Report of the wave that just ended" is the precise definition, and it is stronger than
"everything since the last checkpoint": each wave is one prepare/apply cycle, so a
checkpoint can never see an entry that has not been applied.

One placement rule follows from it: **a checkpoint must immediately follow the stage it
reports on.** Two checkpoints in a row means the second one closes a wave with no requests
in it and reads an empty report, so its notifications are silently lost. A role that wants
a union across stages therefore interleaves the stages with their checkpoints and
accumulates the union itself:

```python
notified = frozenset()
for stage, build, names in (
    ("install done", build_install, INSTALL_NAMES),
    ("configure done", build_configure, CONFIG_NAMES),
):
    yield from build(hv, version)(host)        # the stage's requests
    wave_report = yield checkpoint(stage)      # closes that stage's wave
    notified |= changed_hosts(wave_report, names)
```

Convergence falls out: a converged wave logs `changed=False`, so the union is empty and the
handler deploy yields nothing.

There is no fifth type. A boundary that carries no value is `yield checkpoint("...")` with the
report ignored: it still forces the rendezvous, closes the wave and prints a line (§16.7).

### 4.4 `Control`

```python
@dataclass(frozen=True)
class ControlRequest:
    func: Callable[..., Awaitable[Any]]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    scope: tuple[str, ...] = ()

def control(func: Callable[..., Awaitable[Any]], *args: Any, name: str = "", scope: tuple[str, ...] = (), **kwargs: Any) -> ControlRequest
```

Execution: `await func(*args, **kwargs)` on the **controller**, not on a host. It exists for
control-side IO whose input is per-host state — a variable set entry, a fact, a report — and
which therefore cannot be hoisted out of the per-host generator:

```python
key_text = yield control(files.fetch_url, hv.repo_gpg_key_url)   # per host
key_stream = io.BytesIO(key_text.encode("utf-8"))
```

So there are two control-side channels, and the choice between them is mechanical:

| IO | Where | How often |
|---|---|---|
| identical for the whole inventory | `async def` factory (§9) | once per role invocation |
| depends on the host, a fact or a report | `Control` barrier | once per parked host |

Two consequences of routing it through the barrier rather than the factory:

- under C1 there is exactly one pass, so the IO runs once per role execution (not once per
  phase);
- if replay (C2) is ever adopted, `Control` results are the journal entries that keep the
  body deterministic — anything done in the body instead would repeat on every replay. (A
  factory `await` sits outside that journal and simply re-runs.)

A `Control` barrier is executed once per **host** parked on it, so N hosts means N calls —
which is exactly why an inventory-wide fetch belongs in the factory instead.

---

## 5. The wave loop — complete implementation

Everything below lives in a new driver path. `run_prepare`/`run_apply` and their tests are
untouched; `run()` keeps its exact behaviour and gains one explicit rejection (§5.8).

### 5.1 Data structures

```python
# drivers.py

@dataclass
class WaveRuntime:
    """Per-host state for the wave loop. One generator, resumed many times."""
    host: Host
    gen: Generator[StageItem, Any, Any]
    started: bool = False            # next(gen) vs send(value)
    pending: Any = None              # value to send on the next resume
    parked: Barrier | None = None    # barrier item waiting at the rendezvous
    pulled: OperationRequest | None = None
    done: bool = False
    result: Any = None               # the generator's return value
    counter: _RequestCounter = field(default_factory=_RequestCounter)


@dataclass(frozen=True)
class StageRecord:
    """One line of the report: a planned request, a skipped request, or a barrier."""
    wave: int
    scope: tuple[str, ...]
    planned: PlannedOperation | None = None
    log: LogEntry | None = None
    barrier: Barrier | None = None
    outcome: Any = None
    unknown: bool = False           # dry mode stopped here; nothing was executed


@dataclass
class WaveReport:
    """Per-wave plan/log records plus the merged ApplyReport of every logged wave."""
    records: list[StageRecord] = field(default_factory=list)
    wave_ids: list[int] = field(default_factory=list)

    def close_wave(self, wave: int) -> None:
        if any(record.wave == wave for record in self.records):
            self.wave_ids.append(wave)

    def mark_unknown(self, wave: int, runtimes: list["WaveRuntime"]) -> None:
        """Record every barrier of ``wave`` as planned-but-not-executed.

        Dry mode stops at the first rendezvous, so each parked barrier and
        everything after it is unknowable (§6.3). ``unknown`` is a flag rather
        than a sentinel outcome, because a fact or control callable may
        legitimately return ``None``.
        """
        for rt in runtimes:
            self.records.append(
                StageRecord(wave=wave, scope=rt.parked.scope, barrier=rt.parked, unknown=True)
            )

    def apply_report(self, wave: int, scope: tuple[str, ...]) -> ApplyReport:
        """The applied entries of one wave, filtered to a scope prefix."""
        report = ApplyReport()
        for record in self.records:
            if record.wave != wave or record.log is None:
                continue
            if record.scope[: len(scope)] == scope:
                report.log.append(record.log)
        return report

    def merged(self) -> ApplyReport:
        """All applied entries, in wave order — the analogue of run()'s report."""
        report = ApplyReport()
        for record in self.records:
            if record.log is not None:
                report.log.append(record.log)
        return report

    def tree(self) -> str: ...       # §7.2
```

`StageRecord` deliberately keeps `PlannedOperation` and `LogEntry` **unchanged** (plus the
new `scope` field on `LogEntry`), so `format_plan`/`format_log` can render each record and
the per-line output stays byte-identical to today's.

### 5.2 The loop

```python
# drivers.py

async def run_waves(
    inventory: list[dict],
    deploy_func: Callable[[Host], Generator[StageItem, Any, Any]],
    *,
    host_factory: Callable[[dict], Host] = Host.from_inventory,
    dry: bool = False,
) -> tuple[WaveReport, list[Host]]:
    """Plan and apply a barrier-aware deploy, one wave per prepare/apply cycle.

    Each request is materialized and applied before the next is pulled, so a wave
    needs no second invocation and no determinism comparison (§6). When every
    active host has parked on a barrier, all parked barriers run concurrently and
    each generator is resumed with its own result.
    """
    validate_inventory(inventory)
    hosts = [host_factory(entry) for entry in inventory]
    report = WaveReport()
    try:
        runtimes = [WaveRuntime(h, deploy_func(h)) for h in hosts]
        wave = 0

        while runtimes:
            # 1. Pull one item per free host. A host is free unless it is parked
            #    on a barrier or has finished.
            for rt in runtimes:
                if rt.parked is None and not rt.done:
                    _pull(rt)

            # 2. Plan and apply this step's requests, one per host, in lockstep.
            stepped = [rt for rt in runtimes if rt.pulled is not None]
            if stepped:
                outcomes = await asyncio.gather(
                    *(_step(rt, dry) for rt in stepped), return_exceptions=True
                )
                _collect_steps(report, wave, outcomes)
                for rt in stepped:
                    rt.pulled = None

            # 3. Rendezvous: every active host is parked or done. Close the wave,
            #    then execute every parked barrier concurrently.
            if all(rt.done or rt.parked is not None for rt in runtimes):
                parked = [rt for rt in runtimes if rt.parked is not None]
                if parked and dry:
                    # A barrier's outcome may depend on requests that were planned
                    # but never applied, so the preview stops here (§6.3).
                    report.mark_unknown(wave, parked)
                    report.close_wave(wave)
                    break
                report.close_wave(wave)
                if parked:
                    outcomes = await asyncio.gather(
                        *(_run_barrier(rt, report, wave) for rt in parked),
                        return_exceptions=True,
                    )
                    # Resume from the sanitised values, never from the raw
                    # outcomes: an aggregate error has already raised above, so
                    # every value here is data and may be sent into a generator.
                    values = _collect_barriers(report, wave, parked, outcomes)
                    for rt, value in zip(parked, values):
                        rt.pending, rt.parked = value, None
                    wave += 1

            runtimes = [rt for rt in runtimes if not rt.done]
    finally:
        # The driver owns the hosts: run()'s finally, moved here.
        for host in hosts:
            aclose = getattr(host, "aclose", None)
            if aclose is not None:
                await aclose()
    return report, hosts
```

Loop properties worth checking against the corpus:

- **Per-host asymmetry is legal.** Hosts need not share a barrier count or a request count;
  the rendezvous waits for every *active* host to park. Apache's per-host SSL loop can run
  three times on one host and once on another.
- **A wave may contain zero requests** (a barrier-only wave, which is what consecutive
  barriers produce) **or zero barriers** (a barrier-free role, which is a single wave).
- **A finished host leaves the loop**, so later barriers run only for the hosts that are
  still working.
- **Lockstep is preserved**: step 2 applies request *n* on every host before step 1 pulls
  *n+1*. The `asyncio.gather` join is the existing barrier.

### 5.3 Pulling, with both guards installed

```python
# drivers.py

def _pull(rt: WaveRuntime) -> None:
    """Resume one host's generator and classify the item it produced.

    Synchronous: resuming a generator does no IO. All the IO of a wave is in
    ``_step`` (the operations) and ``_run_barrier`` (the barriers), both of
    which the loop awaits -- so a pull can never interleave with a transport.
    """
    with _stage_pull(rt.counter):
        try:
            if not rt.started:
                item = next(rt.gen)
                rt.started = True
            else:
                item = rt.gen.send(rt.pending)
        except StopIteration as stop:
            rt.done, rt.result, rt.pending = True, stop.value, None
            # Same lost-item guard the legacy phases run when a body runs out:
            # a request or barrier built with a constructor and never yielded.
            if rt.counter.unyielded:
                raise DeployRequestError(
                    _unyielded_message(
                        _lost_requests(rt.host.name, rt.counter.unyielded), "wave"
                    )
                )
            return
        except RecursionError as exc:              # a role that depends on itself
            raise RoleRecursionError(
                f"host {rt.host.name}: role composition recursed without a base case; "
                "a role must not yield from itself (there is no de-duplication)"
            ) from exc

    rt.pending = None
    # One type check and one tally for both kinds of item (see below).
    _account_stage(item, rt.counter, rt.host.name)
    if isinstance(item, OperationRequest):
        rt.pulled = item
    else:
        # _account_stage has already rejected everything outside the union.
        rt.parked = item


def _account_stage(
    item: Any, counter: _RequestCounter | None, host_name: str
) -> None:
    """Type-check one yielded item and charge it to the host's tally.

    The wave counterpart of ``core._account_request`` (§5.8): it accepts the
    widened union -- an ``OperationRequest`` or any :data:`Barrier` -- and
    charges ``counter.yielded`` for either, so ``counter.unyielded`` keeps
    meaning what it means today and a body that builds a request (or a
    barrier) with a constructor and then drops it is still caught when the
    generator runs out. ``counter`` is ``None`` for a hand-assembled
    ``WaveRuntime``, which keeps the type check while leaving the tally
    untracked -- exactly the compromise ``_account_request`` makes.

    Anything outside the union is a contract error raised here, before the
    loop can act on it: a role yields requests and barriers, nothing else.
    """
    if not isinstance(item, OperationRequest | Barrier):
        raise DeployRequestError(
            f"host {host_name}: the deploy body yielded {item!r}, a "
            f"{type(item).__name__}; a role yields OperationRequests and barriers "
            "(fact, act, checkpoint, control), nothing else"
        )
    if counter is not None:
        counter.yielded += 1


@contextmanager
def _stage_pull(counter: _RequestCounter) -> Iterator[None]:
    """Install the request tally AND the in-deploy guard around one pull.

    The guard must be active while the body runs (so a direct gather_action call
    raises at the call site) and inactive while the driver executes a barrier.
    """
    with _tracked_requests(counter), _deploy_guard():
        yield
```

`_deploy_guard` is a new context manager in `actions.py`; `_deploy_phase` is rewritten in
terms of it so the two cannot drift:

```python
# actions.py — additive

@contextmanager
def _deploy_guard() -> Iterator[None]:
    token = _deploy_active.set(True)
    try:
        yield
    finally:
        _deploy_active.reset(token)
```

### 5.4 One step: materialize once, apply immediately

```python
# drivers.py

async def _step(
    rt: WaveRuntime, dry: bool
) -> tuple[PlannedOperation, LogEntry | None]:
    request = rt.pulled
    guard = request.when_failure(rt.host)
    if guard is not None:
        # A skipped request is logged, not omitted: _apply_host_step returns a
        # skipped LogEntry today and format_log renders it as [skipped: ...].
        reason = f"when guard {guard.__name__}"
        return (
            PlannedOperation(
                request=request,
                changed=False,
                reason=reason,
                commands=(),
                skipped=True,
            ),
            LogEntry(
                host=rt.host.name,
                name=request.name or request.func.__name__,
                changed=False,
                reason=reason,
                skipped=True,
                scope=request.scope,
            ),
        )

    # A fresh cache per step, exactly as run_apply does today, so an operation
    # that reads a fact sees the effect of the requests already applied.
    cache = FactCache(rt.host, phase="wave")
    ctx, commands = await materialize(rt.host, cache, request)
    planned = PlannedOperation(
        request=request,
        changed=ctx.changed,
        reason=ctx.reason,
        commands=tuple(commands),
    )
    if dry:
        return planned, None

    sudo, su, doas = request.sudo.elevates(), request.su.elevates(), request.doas.elevates()
    sent: list[Command] = []
    if ctx.changed:
        for cmd in commands:
            await send_command(rt.host, cmd, sudo=sudo, su=su, doas=doas)
            sent.append(cmd)
    entry = LogEntry(
        host=rt.host.name,
        name=request.name or request.func.__name__,
        changed=ctx.changed,
        reason=ctx.reason,
        commands=tuple(sent),
        sudo=sudo,
        sudo_user=_privilege_sudo_user(rt.host, sudo),
        su=su,
        su_user=_privilege_su_user(rt.host, su),
        doas=doas,
        doas_user=_privilege_doas_user(rt.host, doas),
        scope=request.scope,
    )
    return planned, entry
```

This function is the whole determinism story: `materialize` is called **once**, its
commands are sent in the same step, and there is no second materialization to compare.

### 5.5 Executing a barrier

```python
# drivers.py

async def _run_barrier(rt: WaveRuntime, report: WaveReport, wave: int) -> Any:
    barrier = rt.parked
    if isinstance(barrier, FactRequest):
        return await barrier.fact_cls.process(rt.host, *barrier.args, **barrier.kwargs)
    if isinstance(barrier, ActionRequest):
        return await barrier.action_cls.process(rt.host, *barrier.args, **barrier.kwargs)
    if isinstance(barrier, ControlRequest):
        return await barrier.func(*barrier.args, **barrier.kwargs)
    if isinstance(barrier, CheckpointRequest):
        # The payload is the just-closed wave's report, already sliced to the
        # checkpoint's scope; `changed_hosts` only ever reads `.log`.
        return report.apply_report(wave, barrier.scope)
    raise TypeError(f"unknown barrier {barrier!r}")
```

No `gather_fact`/`gather_action` call appears here: the rendezvous **is** the fan-out. Each
host calls its own fact or action, all parked hosts run concurrently under one
`asyncio.gather`, and the action's connection is **not** closed — the driver owns hosts and
closes them once in `run_waves`'s `finally`. (This is why the loop calls
`action_cls.process(...)` directly rather than `_gather_action`, whose `finally` closes
every host it touched.)

### 5.6 Error collection

Barrier failures are aggregated across hosts with the same classes and precedence the
existing drivers use:

```python
def _collect_barriers(report, wave, parked, outcomes) -> list[Any]:
    connection: list[HostConnectionError] = []
    timeout: list[ConnectionTimeoutError] = []
    privilege: list[PrivilegeError] = []
    for rt, outcome in zip(parked, outcomes):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, HostConnectionError):
                connection.append(outcome)
            elif isinstance(outcome, ConnectionTimeoutError):
                timeout.append(outcome)
            elif isinstance(outcome, PrivilegeError):
                privilege.append(outcome)
            else:
                raise outcome
        else:
            report.records.append(
                StageRecord(wave=wave, scope=rt.parked.scope, barrier=rt.parked, outcome=outcome)
            )
    if connection:
        raise HostConnectionError.aggregate(connection)
    if timeout:
        raise ConnectionTimeoutError.aggregate(timeout)
    if privilege:
        raise PrivilegeError.aggregate(privilege)
    # Every exception has either raised above or been aggregated in a group
    # that raised, so the outcomes are all plain values by this point.
    return list(outcomes)
```

`_collect_steps` is the same shape for `_step` outcomes, appending
`StageRecord(planned=..., log=...)` and re-raising `CommandError` unchanged (a non-zero
command exit aborts, as today; a non-zero **action** exit is data, because `Shell.process`
returns a `Result`):

```python
def _collect_steps(report: WaveReport, wave: int, outcomes) -> None:
    """Record one lockstep step: a plan line and a log line per host.

    ``asyncio.gather`` returns in input order, so iterating ``outcomes`` keeps
    the report host-ordered. ``PrivilegeError`` is aggregated across the
    step's hosts exactly as ``run_apply`` does; anything else aborts at once,
    so a ``CommandError`` or a bug still stops the run.
    """
    privilege: list[PrivilegeError] = []
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            if isinstance(outcome, PrivilegeError):
                privilege.append(outcome)
            else:
                raise outcome
            continue
        planned, log = outcome
        report.records.append(
            StageRecord(
                wave=wave,
                scope=planned.request.scope,
                planned=planned,
                log=log,
            )
        )
    if privilege:
        raise PrivilegeError.aggregate(privilege)
```

### 5.7 Wave boundaries and numbers

A wave is closed at every rendezvous, so **each barrier closes its own wave**: two
consecutive barriers occupy two consecutive barrier-only waves. The first wave is `0`.
Examples:

| Program | Waves |
|---|---|
| `tools` (one request, no barrier) | 1 (wave 0 only) |
| `repo_epel` (two facts, a conditional control fetch, then install + configure) | 3–4 (wave 0: `LinuxDistribution`, wave 1: `FileExists`, wave 2: `Control` when the repo file is absent, last wave: the requests) |
| `apache` (install, probe, certificate facts, configure, checkpoint, handler) | 3 + one per SSL vhost (4 with a single certificate) |
| `php` (fact, install, cleanup, configure, handler — one checkpoint per stage) | 5 |

A wave with no records is not appended to `wave_ids`, so an entirely empty role emits
nothing.

### 5.8 `run()` compatibility

`run()` and its two phases are untouched, including the determinism guard and the
`ActionInDeployError` behaviour. One rule is added: a barrier yielded to `run()` is a
contract error, because `_prepare_host` would treat it as a request.

```python
# core.py — the legacy account function stays strict
def _account_request(item, counter, host_name, phase):
    if not isinstance(item, OperationRequest):
        raise DeployRequestError(
            f"host {host_name}: the {phase}-phase deploy body yielded "
            f"{type(item).__name__}; barriers require run_waves(), not run()"
        )
    ...
```

The wave path uses a separate `_account_stage`, which accepts the union and charges
`yielded` for both kinds. This keeps every existing message and test intact. Both phases
stay public: they are the documented barrier-free path (§16.9).

---

## 6. Determinism, `dry`, and what replaces the guard

### 6.1 The guard, restated

Today's guard exists because the plan is built in one pass and shown to the user, and the
apply is a **second pass** that might disagree. The wave model removes the second pass:
each request is materialized and applied in the same step. There is no interval in which
the body can be re-entered, so there is nothing to diverge.

This is a **stronger** guarantee per request (no plan/apply window at all) and a **weaker**
one overall (no whole-deploy preflight). Both statements should be published; the previous
note's invariant 7 ("requests are compared by value") becomes "requests are value objects
and are never re-derived".

### 6.2 Rules that relax

Because there is exactly one pass, the two-phase identity constraints stop being hard
errors:

| Old rule | Why it existed | Under C1 |
|---|---|---|
| Build an `io.BytesIO` at module level, never in the body | prepare and apply built different objects, compared by identity | A stream built in the body is read once; it is safe (and still must be rewound between hosts if shared) |
| `when`/`func` must be stable, module-level objects | an inline lambda is a fresh object per phase | An inline `when` lambda no longer trips a guard; it does defeat `compose(dedupe=True)` |
| Never branch on a fact inside a deploy | the two phases would branch differently | Branching on a **barrier result** is the entire point |

Keep the old habits anyway: stable predicates and hoisted streams make `compose(dedupe=True)`
work for `run()` programs and keep code readable. But they are guidance under C1, not
invariants the engine enforces.

### 6.3 `dry` is a one-wave preview

`dry=True` plans wave 0 and **executes no barrier at all**. The first parked barrier is the
honesty boundary: its result may depend on requests that were planned but not applied, so
continuing would be speculation. The report marks that barrier and everything after it
`(unknown until run)` and the run stops.

Consequences, stated plainly:

- a barrier-free role is **fully** planned in dry mode;
- a role with barriers gets a one-wave preview — less than Option B's per-stage `Unknown`
  tree, and less than today's `execute(dry=True)` per deploy, which plans the whole deploy
  because a deploy has no barriers;
- **no barrier ever runs in dry**, including facts. This is stricter than today's role
  programs, which run their probes for real between dry deploys. If a read-only preview is
  wanted, it must be a separate `run_waves` mode that executes facts and is documented as
  optimistic; it is deliberately out of scope here (§16.6).
- an `async def` factory has already been awaited before `dry` starts, so its control-side IO
  happens even in a preview; only barriers are suppressed (§9, §13).

```text
$ uv run examples/roles/apache.py --dry
apache
  wave 0
    web1: Install apache2                    [changed: not installed]
    web1: Enable apache2                     [no change: already enabled]
    barrier: apache -v                       (unknown until run)
  — rest of role                             (unknown until run)
```

### 6.4 Idempotence and convergence

Unchanged. Every operation still computes `changed` from the host's state at materialization
time. A converged rerun materializes requests, finds `changed=False`, sends nothing, logs
`no change`, and the checkpoint-driven handler union is empty. The wave model adds no new
state that could break convergence.

---

## 7. Reporting

### 7.1 What the driver records

Every step appends a `StageRecord` with the `PlannedOperation` it computed and the
`LogEntry` it produced (or `None` in dry mode). Every barrier appends a record holding the
barrier and its outcome. Records carry the `scope` path and the wave number.

`WaveReport.merged()` returns a normal `ApplyReport`, so `changed_hosts` and any existing
report consumer work unchanged. `WaveReport.apply_report(wave, scope)` is the
checkpoint-facing slice.

### 7.2 The tree

```python
def tree(self) -> str:
    """The scope/wave tree; a method alias for the module-level renderer."""
    return format_waves(self)


def format_waves(report: WaveReport) -> str:
    """Group records by scope path, then by wave, then delegate per line.

    Each PlannedOperation renders through format_plan and each LogEntry through
    format_log, so a line is byte-identical to what the example prints today;
    only the indentation and the wave/barrier headings are new. A barrier is
    printed inside the wave it closed (§5.2), after that wave's request lines.
    """
```

```text
apache
  wave 0
    web1: Install apache2                      [changed: not installed]
    web2: Install apache2                      [changed: not installed]
    barrier: apache -v → 2.4.58
  wave 1
    barrier: FileExists /etc/ssl/web1.pem → False
  wave 2
    web1: Write vhosts                         [changed: content differs]
    barrier: checkpoint configure done → 1 host notified
  wave 3
    web1: Restart apache (handler)             [changed: restarted]
php
  ...
```

One barrier line is emitted per parked host; the sample elides `web2`'s repeats. It assumes
one SSL vhost, which is §5.7's four-wave `apache` row.

`scope` is the addressing mechanism: two instances of the same factory are distinguished by
`scoped("apache-eu", ...)` and `scoped("apache-us", ...)`. There is no automatic instance
identity and no collapse — that is the consequence of dropping de-duplication.

### 7.3 Report-layer rules

- Never prefix `OperationRequest.name` with a role or scope. `changed_hosts` matches on
  `name`; the §10 trap from the Option B note applies verbatim.
- A checkpoint's report is the **wave** report, not "everything so far". This is what makes
  a notification read able to trust `changed` (the driver is fail-fast on command exits).
- A skipped request (`when` guard failed) appears in the plan **and** in the log, rendered
  `[skipped: when guard ...]` with `changed=False`, exactly as today — so it never queues a
  handler (§5.4).

---

## 8. Typed role variables

Typed variables are orthogonal to the barrier model; the model from the Option B note is
reused unchanged, minus the two-channel split (`s.vars`/`s.outputs`), which a return value
replaces.

### 8.1 The model

`pydantic>=2.14.1` is already a runtime dependency.

```python
class VHost(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    servername: str
    documentroot: str


class SslVHost(VHost):
    certificate_file: str
    certificate_key_file: str


class ApacheVars(BaseModel):
    """The role's static inputs. Field names are UNPREFIXED; ``prefix`` binds them."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    listen_ip: str = "*"
    listen_port: int = 80
    listen_port_ssl: int = 443
    vhosts: tuple[VHost, ...] = (
        VHost(servername="local.dev", documentroot="/var/www/html"),
    )
    vhosts_ssl: tuple[SslVHost, ...] = ()
    mods_enabled: tuple[str, ...] = ("rewrite", "ssl")
    state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    enabled: bool = True
    # ... 24 fields in the real role
```

`frozen=True` keeps the model hashable for a factory closure; `tuple`, never `list`,
because `op()` rejects unhashable arguments — a rule this note leaves alone, and the only
place dedupe still hashes request values (§3.2 confines it to `run()`).

### 8.2 Resolution and `VarsSet`

```python
@dataclass(frozen=True)
class VarsSet(Generic[V]):
    prefix: str
    values: Mapping[str, V]          # host name -> resolved model

    def for_host(self, host: Host) -> V:
        return self.values[host.name]

    def __getitem__(self, host_name: str) -> V:
        return self.values[host_name]


def resolve_vars(
    cls: type[V],
    prefix: str,
    inventory: list[dict],
    *,
    overrides: Mapping[str, Any] | None = None,
) -> VarsSet[V]:
    """defaults < inventory `extra` keys (prefixed) < call-site overrides."""
    values: dict[str, V] = {}
    for entry in inventory:
        extra = InventoryEntry.model_validate(entry).model_extra or {}
        raw = {**_bind(cls, prefix, extra), **(overrides or {})}
        values[entry["host"]] = cls.model_validate(raw)
    return VarsSet(prefix, MappingProxyType(values))


def _bind(cls, prefix: str, source: Mapping[str, Any]) -> dict[str, Any]:
    """Take only this role's prefixed keys and strip the prefix."""
    known = set(cls.model_fields)
    bound: dict[str, Any] = {}
    for key, value in source.items():
        if not key.startswith(prefix):
            continue
        bare = key[len(prefix):]
        if bare not in known:
            raise RoleVarsError(
                f"{key!r} is not a variable of {cls.__name__}; expected one of "
                f"{', '.join(sorted(prefix + f for f in known))}"
            )
        bound[bare] = value
    return bound
```

Compatibility: an inventory that already carries `apache_listen_port: 8080` binds to
`listen_port` with no migration. Unprefixed keys are ignored by every role and remain
available as raw `host.extra`.

**Amended at T7.** The typo rule assumes a prefix names a namespace no other role shares,
and the corpus has one pair that breaks it: `php_` and `php_xdebug_`, a dependency and the
role that runs it. `php_xdebug_version` carries the `php_` prefix, so the rule above reports
the child role's variable as the parent role's misspelling and §10.5 cannot resolve either.
`resolve_vars` therefore takes `nested_prefixes`, and `_bind` skips a key one of them matches
*before* the field check. Only the parent side passes it; a role with no nested prefix is
unaffected, and the typo check still fires for every other key.

`overrides` are uniform per role invocation (the call-site level). Per-host variation lives
in the inventory; that is what the `extra` channel is for.

---

## 9. A role's anatomy

```python
Deploy = Callable[[Host], Generator[StageItem, Any, Any]]
```

A role has exactly three parts:

1. **A factory** — `def apache(v: VarsSet[ApacheVars]) -> Deploy`, or `async def` when it
   has controller-side IO to do. It closes over resolved variables, does any preparation,
   and returns the deploy. It is the call-site unit: `apache(apache_v)` is a deploy — await
   it first if the factory is `async def` — accepted by `run_waves` and `yield from`, and by
   `compose`/`sub_deploy` when it is barrier-free (§3.2).
2. **A per-host generator** — `def deploy(host)`. Host selection is a normal `if` at the
   top; all control flow between barriers is ordinary Python; barriers are `yield`
   expressions.
3. **A return value** — optional, typed, captured by `yield from` in a parent. This is the
   dependency-output channel. A role that publishes nothing returns `None`.

**Async factories.** A factory may be `async def ... -> Deploy`: in an `async def` the
annotation is the awaited type, so it still returns a `Deploy`. Its IO runs once per role
invocation, before any host is pulled, which is the right home for an inventory-wide fetch or
a template read:

```python
async def nginx(v: VarsSet[NginxVars], bundle_url: str) -> Deploy:
    """One fetch for the whole inventory: the URL must not vary per host."""
    ssl_bundle = await files.fetch_url(bundle_url)
    def deploy(host: Host) -> Generator[StageItem, Any, None]:
        yield from install(v.for_host(host), ssl_bundle)(host)
    return deploy


report, hosts = await run_waves(inventory, await nginx(nginx_v, SSL_BUNDLE_URL))
```

The URL is an argument, not a `VarsSet` field: a factory sees only variables, and a
`VarsSet` is keyed by host, so a factory-level value is by definition the same for every
host. Per-host IO, or IO whose input is a fact or a report, stays a `Control` barrier (§4.4).

Because a sync generator body cannot `await`, asyncness **propagates upward**: a parent role
that composes an async child must itself be `async def` and await the child's deploy in the
factory, then `yield from` the result:

```python
async def edge_tier(apache_v: VarsSet[ApacheVars], nginx_v: VarsSet[NginxVars]) -> Deploy:
    nginx_dep = await nginx(nginx_v, SSL_BUNDLE_URL)     # awaited here, not in the generator
    def deploy(host: Host) -> Generator[StageItem, Any, None]:
        yield from scoped("apache", apache(apache_v))(host)
        yield from scoped("nginx", nginx_dep)(host)
    return deploy
```

So asyncness terminates at the root: `main()` awaits whatever the top-level factory returns.
A program that mixes sync and async roles is therefore async at the top and sync at the
leaves, which is a real cost of the decision — paid once, in the program, not per role.

Nesting:

```python
def web_tier(apache_v, php_v, xdebug_v) -> Deploy:
    def deploy(host):
        yield from scoped("apache", apache(apache_v))(host)   # child barriers pass through
        php_out = yield from scoped("php", php(php_v))(host)  # returns PhpOutputs
        yield from scoped("xdebug", php_xdebug(xdebug_v, php_v))(host)
        if php_out.webserver_notified:                        # cross-role relay, by value
            yield from apache_restart_handler(php_out.webserver_notified)(host)
    return deploy
```

Rules:

- **No engine de-duplication.** `yield from php(...)` runs php every time it is written.
  Hoist a shared prerequisite into the composition site (§10.5) or accept the rerun.
- **A role must not depend on itself.** There is no graph to check, so a cycle is caught as
  `RecursionError` and re-raised as `RoleRecursionError` at the pull site.
- **`scope` is manual.** `scoped("apache", apache(v))` sets the report path. Two instances
  are two scopes; nothing collapses them.
- **A role is a program**, so it is sequential by construction. Independent roles sequence
  in `yield from` order; there is no cross-role stage interleaving.

---

## 10. Worked roles

The examples below are the shapes the corpus roles take once they move into
`src/reemote/roles/` (§17). Helper factories from today's programs (`install`,
`configure`, `handler`, `build_*`) keep their module-level names; only the orchestration
changes. §18 walks the remaining twelve role by role.

### 10.1 `tools` — the degenerate role (one wave, no barriers)

```python
from reemote.core import op
from reemote.modules import dnf

def tools() -> Deploy:
    def deploy(host: Host) -> Generator[StageItem, Any, None]:
        yield op(
            dnf.packages,
            name="Install htop",
            packages=("htop",),
            sudo=True,
        )
    return deploy


def app(epel_v: VarsSet[EpelVars]) -> Deploy:
    """The program is a role; `yield from` is the sequence (§3.2)."""
    def deploy(host: Host) -> Generator[StageItem, Any, None]:
        yield from scoped("epel", repo_epel(epel_v))(host)    # epel first: htop needs it
        yield from scoped("tools", tools())(host)
    return deploy


async def main() -> None:
    report, hosts = await run_waves(inventory, app(epel_v))
    print(report.tree())
```

`tools` is one wave, one record, no barrier — what every existing deploy already is. The
program around it is a two-line role: `yield from` sequences `epel` before `tools` (§3.2),
and the driver applies EPEL's requests before it pulls `tools`'s, so `htop` sees the repo.

### 10.2 `repo_epel` — facts, a control-side fetch, and two deploys in one stream

```python
class EpelVars(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repofile_path: str = "/etc/yum.repos.d/epel.repo"
    repo_disable: bool = False
    repo_url: str | None = None
    repo_gpg_key_url: str | None = None


def repo_epel(v: VarsSet[EpelVars]) -> Deploy:
    def deploy(host: Host) -> Generator[StageItem, Any, None]:
        hv = v.for_host(host)
        if host.family != "RedHat":
            return

        distro = yield fact(LinuxDistribution)
        if distro["family"] != "RedHat":
            raise ValueError(
                f"{host.name}: group 'centos' but distribution "
                f"{distro['name']!r} is not RHEL-family"
            )
        major = distro["major"]
        key_id = EPEL_KEY_IDS.get(major)
        if key_id is None:
            raise ValueError(f"{host.name}: no pinned EPEL key id for major {major}")

        resolved = resolve_epel(hv, major)                 # pure, per host
        repo_exists = yield fact(FileExists, path=resolved.repofile)

        key_stream = None
        if not repo_exists:
            # Control-side read whose URL comes from a per-host fact, so it
            # cannot move into an async factory (§9). The stream is built once,
            # in this single pass.
            key_text = yield control(files.fetch_url, resolved.key_url)
            key_stream = io.BytesIO(key_text.encode("utf-8"))

        # No barrier between the two deploys: the driver applies install's
        # requests before it pulls configure's first request, so the repo file
        # exists by then. This is the old "two-deploy split" collapsing into
        # one stream.
        yield from install(resolved, key_id, key_stream, repo_exists)(host)
        yield from configure(resolved)(host)

    return deploy
```

Waves: one per barrier — wave 0 `LinuxDistribution`, wave 1 `FileExists`, wave 2 the
`Control` fetch when the repo file is absent — then a final wave holding install's and
configure's requests in order (§5.7). There is no checkpoint because nothing reads an
outcome. The fetch stays a barrier because `resolved.key_url` depends on `major`, a per-host
fact; a fetch with one URL for the whole inventory would be a factory `await` instead (§9).

### 10.3 `apache` — an action probe, a per-host fact loop, a checkpoint and a handler

```python
@dataclass(frozen=True)
class ApacheOutputs:
    version: str
    service: str = "apache2"


def apache(v: VarsSet[ApacheVars]) -> Deploy:
    def deploy(host: Host) -> Generator[StageItem, Any, ApacheOutputs]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return ApacheOutputs(version="")

        # Wave 0: install, closed by the probe barrier below.
        yield from install(hv)(host)

        # Barrier: the binary exists only after install applied.
        result = yield act(
            Shell,
            command=f"{DAEMON_PATH}{DAEMON} -v",
            name="apache -v",
        )
        if result["exit_status"] != 0:
            raise RuntimeError(f"{host.name}: apache -v failed: {result['stderr']}")
        version = parse_apache_version(result["stdout"])

        # Barrier per certificate: one wave each, per-host and asymmetric.
        ssl_exists = tuple(
            bool((yield fact(FileExists, path=vhost.certificate_file)))
            for vhost in hv.vhosts_ssl
        )

        plans = configure_plan(hv, version, ssl_exists)

        # Wave 2 (with one certificate): configure, then read its outcome.
        yield from configure({host.name: plans})(host)
        wave_report = yield checkpoint("configure done")

        notified = changed_hosts(wave_report, notifying_names(plans))

        # Wave 3: the handler, only on hosts the checkpoint selected.
        yield from handler({host.name: plans}, notified)(host)

        return ApacheOutputs(version=version)

    return deploy
```

Compare with the current program:

```python
# today — main() owns the orchestration
await execute(inventory, deploy_install, "Deploy 1: install Apache")
results = await gather_action(targets, Shell, command=probe)
configure_report = await execute(inventory, build_configure(plans), "Deploy 2: configure")
notified = changed_hosts(configure_report, names)
await execute(inventory, build_handler(plans, notified), "Deploy 3: restart handler")
```

The migration is mechanical in shape but not textual: `execute` becomes `yield from`, the
two `gather_action`/`gather_fact` fan-outs become per-host barriers, and the report read
becomes a checkpoint. The helper factories are untouched.

### 10.4 `php_xdebug` — a dependency is `yield from` plus a return value

```python
@dataclass(frozen=True)
class PhpOutputs:
    version: str | None
    extension_conf_paths: tuple[str, ...]
    webserver_notified: frozenset[str]


def php_xdebug(v: VarsSet[PhpXdebugVars], php_v: VarsSet[PhpVars]) -> Deploy:
    def deploy(host: Host) -> Generator[StageItem, Any, None]:
        hv = v.for_host(host)
        if not hv.enable:
            return

        # The dependency: run php inline for this host and take its outputs.
        # There is no registry and no collision detection -- the caller passes
        # php's variables explicitly, which is the whole de-duplication story.
        # The local must not be called `php`: that would shadow the factory
        # for the whole function body and raise UnboundLocalError here.
        php_out = yield from php(php_v)(host)
        if php_out.version is None:
            return

        yield from setup(hv, php_out.version)(host)          # unarchive / download
        yield from write_ini(hv, php_out.extension_conf_paths, php_out.version)(host)

    return deploy
```

php itself uses one checkpoint per stage to publish its notification union:

```python
def php(v: VarsSet[PhpVars]) -> Deploy:
    def deploy(host: Host) -> Generator[StageItem, Any, PhpOutputs]:
        hv = v.for_host(host)
        distro = yield fact(LinuxDistribution)               # wave 0
        version = resolve_php_version(hv, distro)

        # A checkpoint immediately after the stage it reports on (§4.3): it
        # closes that stage's wave, so the entries read back are that stage's.
        # Three checkpoints after one combined wave would read two empty
        # reports and lose the cleanup and configure notifications.
        notified = frozenset()
        for stage, build, names in (
            ("install done", build_install, INSTALL_HANDLER_NAMES),
            ("cleanup done", build_packaged_cleanup, CLEANUP_HANDLER_NAMES),
            ("configure done", build_configure, CONFIGURE_HANDLER_NAMES),
        ):
            yield from build(hv, version)(host)              # waves 1, 2, 3
            wave_report = yield checkpoint(stage)
            notified |= changed_hosts(wave_report, names)

        yield from build_handler(hv, version, notified)(host)   # wave 4
        return PhpOutputs(
            version=version,
            extension_conf_paths=conf_paths(hv, distro),
            webserver_notified=notified,
        )

    return deploy
```

Cross-role notification is an **explicit relay**: php returns `webserver_notified` and the
composing role hands it to apache's handler (§9's `web_tier`). There is no `"apache:restart
apache"` address; the price of dropping de-duplication is that every cross-role channel is
a value passed by the composing program.

### 10.5 The program — hoisting the shared prerequisite

```python
def web_stack(
    epel_v: VarsSet[EpelVars],
    pg_v: VarsSet[PostgresqlVars],
    apache_v: VarsSet[ApacheVars],
    php_v: VarsSet[PhpVars],
    xdebug_v: VarsSet[PhpXdebugVars],
) -> Deploy:
    """The program is a role whose children are roles; `yield from` is the sequence."""
    def deploy(host: Host) -> Generator[StageItem, Any, None]:
        yield from scoped("epel", repo_epel(epel_v))(host)       # once, before dependents
        yield from scoped("postgresql", postgresql(pg_v))(host)  # effect, not value
        yield from scoped("php", php(php_v))(host)
        yield from scoped("apache", apache(apache_v))(host)
        yield from scoped("xdebug", php_xdebug(xdebug_v, php_v))(host)
        yield from scoped("tools", tools())(host)                # a bare deploy beside roles
    return deploy


async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))

    report, hosts = await run_waves(inventory, web_stack(
        epel_v=resolve_vars(EpelVars, "epel_", inventory),
        pg_v=resolve_vars(PostgresqlVars, "postgresql_", inventory),
        apache_v=resolve_vars(ApacheVars, "apache_", inventory),
        php_v=resolve_vars(PhpVars, "php_", inventory, nested_prefixes=("php_xdebug_",)),
        xdebug_v=resolve_vars(PhpXdebugVars, "php_xdebug_", inventory),
    ))
    print(report.tree())
```

Four properties to note:

- **The program is a role.** No `compose`: `yield from` sequences the children, and unlike
  `compose` it can capture a child's return value (`php_out = yield from ...`), which is the
  cross-role channel §9's `web_tier` uses for its handler relay.
- **EPEL is hoisted**, so it runs once. Without de-duplication this is the caller's job; it
  is also more explicit than a declared dependency edge.
- **php is passed to xdebug as a `VarsSet`**, not looked up by class. That is the dependency
  channel: an ordinary argument.
- **php runs twice** — once here and once inside `php_xdebug`, which re-runs it for the
  version and conf paths it needs. There is no collapse to fall back on: `compose(dedupe=True)`
  never sees a barrier (§3.2). Hoist, or accept the duplicate barrier reads and requests.

A dry run of this tree:

```text
$ uv run examples/roles/web_stack.py --dry
epel
  wave 0
    barrier: LinuxDistribution                     (unknown until run)
  — rest of role                                   (unknown until run)
postgresql
  (not started)
```

Only the first wave is planned; the first barrier is the honesty boundary. `repo_epel`
opens with `fact(LinuxDistribution)`, so its dry preview is one empty wave — for a role
whose *first* item is a barrier the preview carries no plan lines at all. That is the
honest answer, not a defect: nothing before the barrier was ever sent.

---

## 11. Implementation checklist

### 11.1 New module `src/reemote/stages.py`

| Export | Contents |
|---|---|
| `Barrier` | Union alias over the four barrier types (§3.1); `isinstance`-able |
| `FactRequest`, `ActionRequest`, `CheckpointRequest`, `ControlRequest` | Frozen dataclasses (§4) |
| `StageItem` | `OperationRequest \| Barrier` |
| `Deploy` | `Callable[[Host], Generator[StageItem, Any, Any]]` (§3.1); the awaited return type of an `async def` factory |
| `fact`, `act`, `checkpoint`, `control` | Constructors; each charges the build counter |
| `scoped` | Report-path wrapper (§3.3) |
| `RoleRecursionError` | Raised when a role's `yield from` chain recurses |

### 11.2 `src/reemote/core.py`

- Add `OperationRequest.scope: tuple[str, ...] = ()`; normalize to a tuple in
  `__post_init__`; include in `__hash__`; **exclude** from `_effective_key`.
- Add `_charge_built()` (the `op()` counter increment, extracted) so the barrier
  constructors share it.
- Reuse `_lost_requests` / `_unyielded_message` unchanged; their "request" wording is
  read as "stage item" on the wave path.
- `_account_request` keeps its strict `OperationRequest` check and its message, plus a hint
  that barriers need `run_waves()`.
- Add `_require_request(item, wrapper, host_name)` and call it from `compose` and
  `sub_deploy`, so a barrier handed to either raises `DeployRequestError` instead of
  `AttributeError` (§3.2). Requests are unaffected.
- No other change; all existing tests must pass unmodified.

### 11.3 `src/reemote/drivers.py`

- `LogEntry.scope: tuple[str, ...] = ()`.
- `WaveRuntime`, `StageRecord`, `WaveReport` (§5.1).
- `run_waves`, `_pull`, `_step`, `_run_barrier`, `_collect_steps`, `_collect_barriers`,
  `_stage_pull`, `_account_stage` (§5.2–5.6).
- `format_waves(report)` and `WaveReport.tree()` (§7.2).
- `__all__` additions: `run_waves`, `WaveReport`, `StageRecord`, `format_waves`.

### 11.4 `src/reemote/actions.py`

- Add `_deploy_guard()` and rewrite `_deploy_phase` in terms of it.
- Extract the connection/timeout/privilege aggregation from `_gather_action` into
  `_aggregate_action_errors(results)`, reused by `_collect_barriers`; behaviour unchanged.
- `gather_action`'s public contract is untouched.

### 11.5 `src/reemote/handlers.py`

No change. A checkpoint delivers an `ApplyReport`, so `changed_hosts` works as written,
and `service_handler`/`restart_handler`/`reload_handler` remain the handler factories.

### 11.6 `src/reemote/rolevars.py`

`VarsSet`, `resolve_vars`, `_bind`, `RoleVarsError` (§8). `pydantic` is already a runtime
dependency; no `pyproject.toml` change.

### 11.7 `src/reemote/__init__.py`

Re-export the new public names explicitly and add them to `__all__` (`run_waves`,
`fact`, `act`, `checkpoint`, `control`, `scoped`, `Barrier`, `Deploy`, `FactRequest`,
`ActionRequest`, `CheckpointRequest`, `ControlRequest`, `StageItem`, `WaveReport`,
`StageRecord`, `format_waves`, `VarsSet`, `resolve_vars`, `RoleVarsError`,
`RoleRecursionError`). `ApplyReport`, `LogEntry`, `PlannedOperation`, `format_log` and
`format_plan` are already exported and are reused unchanged.

### 11.8 Tests

| File | Covers |
|---|---|
| `tests/test_stages.py` | Barrier construction and scope stamping; per-host asymmetry; rendezvous ordering; each barrier kind; checkpoint placement (a checkpoint after a barrier-only wave reads an empty report); skipped requests logged, not omitted; `compose`/`sub_deploy` barrier rejection; `dry`; error aggregation; recursion guard; contract violations (wrong item, unyielded barrier) |
| `tests/test_rolevars.py` | Precedence, prefix binding, unknown prefixed key, tuple coercion, per-host values |
| `tests/test_roles_waves.py` | `repo_epel`, `apache`, `php_xdebug` end to end on `SimulatedHost`; checkpoint slices; handler gating; tree snapshots; an `async def` factory awaited once, its fetch not repeated per host |
| `tests/test_examples.py` | Role factories added to the drain list; keep `build_*` names for `SNAPSHOT_ARGS` |
| full suite | `run()` determinism, in-deploy guard, and every existing example unchanged |

The harness detail matters: `tests/test_examples.py` statically scans `op(...)` call sites
for mutable containers and dynamically drains deploy-shaped entrypoints. Barrier
constructors are not `op()` calls, so the scan is unaffected; the drain list must call each
role factory, invoke the returned deploy on a simulated host, and **stop at the first
barrier** (draining a generator that is never resumed would otherwise look like a lost
request). A `drain(..., until_barrier=True)` helper keeps the harness honest. Once the roles
move into the package (§17), the same static scan must walk `src/reemote/roles/` too: that
is where the `op(...)` calls with dict and list arguments now live, and the scan currently
sees only `examples/`.

### 11.9 Documentation

- The roles are library citizens, so their pages live at `docs/roles/*.md` and are rendered
  from the package with mkdocstrings, exactly like `docs/modules/*`; `docs/examples/roles/*`
  is deleted rather than redirected (§17.6).
- This note stays outside `docs_dir` and is not published.
- The guide's composition section gains "Roles are resumable deploys" with the four barrier
  types and the one-wave dry caveat.
- Every transcript is regenerated from `report.tree()`.

That list is not the whole docs surface. Four published pages — five passages — make
**unconditional** two-phase claims that stay true of `run()` and become false of `run_waves`.
Each needs a scope qualifier, not a rewrite; they are enumerated here so the work is not
rediscovered during phase 6:

| Page | The claim | What it needs |
|---|---|---|
| `docs/guide.md` → *Determinism* | "The runner executes your deploy twice — once to plan, once to apply"; "the *same* IO object identity for a stream"; "A `when` guard may consult only **static inventory data**, never a fact" | All three are `run()` truths. Add that `run_waves` has one pass, so §6.2's three rules relax — and that the relaxations are guidance, not licences |
| `docs/guide.md` → *Change notification (handlers)* | "reemote has **no first-class handler mechanism** … This is the **two-deploy outcome split**" | The section role migrators actually read, and 10 of 14 roles call `changed_hosts`. `checkpoint` *is* a first-class notification read; keep the split documented beside it as the barrier-free idiom |
| `docs/handlers.md` | Its framing sentence: "`reemote.handlers` packages the **two-deploy outcome split**" | The helpers are untouched and already accept a `WaveReport` slice (§16.2), so only the framing narrows: the split is one of two ways to feed them |
| `docs/migrating.md` | Five places, including the numbered recipe step "5. **Run pass 2.**" and the fact-split recipe | The recipes must gain the barrier form, because a migrated role now lands in `src/reemote/roles/` (§17) where the split is unavailable — one host view, one pass |
| `docs/actions/actions.md` | "Users call actions **before, between and after deploys — never inside one**" | Still true of a direct `gather_action(...)` call, which the wave path keeps raising on (§2, §5.4). Add that `yield act(...)` is the in-stream form |

`docs/core.md`, `docs/drivers.md` and `docs/index.md` describe `run()` and its phases and
carry no two-deploy framing, so they need the §16.9 pointer — "the barrier-free two-phase
path, see `run_waves` for barriers" — rather than a correction. The demonstration corpus and
the new examples it needs are §19.

---

## 12. Contract rules

1. **A role is a factory returning a deploy** — `def`, or `async def` awaited at the call
   site (§9). No class, no registration, no identity.
2. **A barrier is the only way to read remote or control-side state into per-host control
   flow.** A factory may read control-side state once per role (§9); a fact read inside an
   operation is still the operation's own materialization read.
3. **One pass per request.** `materialize` runs once and its commands are sent in the same
   step; never re-derive a request.
4. **`run_waves` owns hosts.** Barriers never close a connection; a role must not either.
5. **`name` is never rewritten for scoping.** Use `scope`; `changed_hosts` matches `name`.
6. **`scope` is report-only** and excluded from `_effective_key`.
7. **`compose` and `sub_deploy` are request-only.** A barrier handed to either is a
   `DeployRequestError`; roles compose with `yield from` (§3.2).
8. **Eligibility is a host check at the top of the generator**, never `sub_deploy(when=...)`.
9. **Control-side effects are `Control` barriers or factory code**, never body IO. IO that is
   the same for every host goes in an `async def` factory and runs once; IO that depends on a
   host, a fact or a report is a `Control` barrier (§9).
10. **No de-duplication.** There is no cross-role collapse: hoist a shared prerequisite at
    the composition site or accept the rerun. `compose(dedupe=True)` is confined to
    barrier-free `run()` programs (§3.2).
11. **`run()` stays barrier-free.** A barrier yielded to `run()` is a `DeployRequestError`
    pointing at `run_waves`.
12. **Control flow may depend only on variables, barrier results and pure functions.**
    Time, randomness or a controller file read inside the body would make a rerun diverge
    with nothing to compare it to. This is also the invariant a future replay journal would
    rest on (§16.10).

---

## 13. Limits, stated plainly

- **No de-duplication.** A shared prerequisite executes once per call site. Consequences:
  duplicate barrier reads, duplicate handler deployments, duplicate report subtrees, no
  conflict detection between two variable sets, and cycles degrade to recursion errors.
  The only mitigation is hoisting at the composition site: `compose(dedupe=True)` is
  confined to barrier-free `run()` programs and never spans roles (§3.2).
- **No subtree policy over a child role.** `sub_deploy(sudo=...)` cannot wrap a role, so a
  child that must run elevated has to accept the privilege as one of its own variables.
  This is the one `sub_deploy` capability with no drop-in replacement (§3.2).
- **One host view per run.** `run_waves` builds its hosts from one inventory and closes them
  in its own `finally`, so nothing inside a role can change the privilege view or force a
  reconnect mid-run. Two corpus members need exactly that: `postgresql` runs its data plane
  as `postgres` through a second inventory (`privilege.sudo_user`), and `docker`'s
  `meta: reset_connection` relies on the next deploy connecting fresh so a new group
  membership is visible. Both are handled at the program level, by running more than one
  `run_waves` pass (§18.4, §18.11); a reconnect barrier is deliberately not added, because
  run-as users and passwords come from the inventory and never from a request.
- **No static dependency graph.** Nothing lists a role's prerequisites before it runs, so
  there is no pre-flight cycle check, no dependency-ordered preview and no versioning.
- **Dry is one wave deep.** A role with barriers gets a single-wave preview; deeper preview
  would require executing barriers optimistically or simulating the host.
- **Factory IO is neither previewed nor journalled.** An `async def` factory is awaited
  before the first pull, so `dry=True` still performs its fetches and reads, and a future
  replay (C2) re-runs it. Its value must also be host-uniform, because a factory sees no
  host (§9).
- **No role identity or addressing.** `scope` is manual and does not collapse. Two
  invocations of one factory are two report paths, not one instance.
- **No cross-role handler registry.** Notification is a value returned by a role and passed
  to another role's handler factory by the composing program (§16.8).
- **No concurrency between roles.** `yield from` is sequential; a role owns its host's
  stream. Per-host concurrency and lockstep are unchanged.
- **Migration is a rewrite of orchestration, not a rename.** `main()`'s control-side loops
  move inside the per-host generator, which changes the shape of each role program even
  though the helper factories survive.
- **The plan is retrospective.** `WaveReport` records what was planned and what was applied;
  it is not a forecast. That is the price of never re-invoking the body.

---

## 14. Comparison with Option B

| | Alternative 3 (this note) | Option B (`Role` + `Session`) |
|---|---|---|
| Unit | factory returning a deploy | class with `run(self, s, v)` |
| Nesting | `yield from`, return value | `await s.role(...)` |
| Dependency data | return value (one channel) | `s.vars` + `s.outputs` (two channels) |
| De-duplication | none, by design | `RoleDep`, instance identity, conflict rules |
| Host lifecycle | existing driver owns it | re-implemented in the Session (must avoid `run()`) |
| Cross-role notify | explicit relay | addressed registry |
| Preview | one wave | per-stage tree with `Unknown` |
| Engine change | new driver path (`run_waves`) | none |
| Determinism guard | replaced by single-pass materialization | unchanged (Session sits above it) |
| New concepts | 4 barriers, 1 driver loop | `Role`, `Session`, `RoleDep`, `VarsSet`, registry, stages |

The trade is explicit: this note makes composition a property of the engine and pays in
driver code, a weaker preview and no de-duplication; Option B keeps the engine frozen and
pays in a second orchestration API and duplicated host-lifecycle logic.

---

## 15. Migration path

Each step is independently shippable. Phases 0–1 are the engine and the package skeleton;
from phase 2 on, every role lands as one commit carrying its module, its runner, its test
and its doc page together (§17.7).

| Phase | Work | Gate |
|---|---|---|
| 0 | Engine: `stages.py`, `rolevars.py`, `run_waves`, `_deploy_guard`, tests | full existing suite green; `run()` byte-identical |
| 1 | Skeleton: `src/reemote/roles/__init__.py`, the `templates` extra, the shared runner helper, `tests/roles/`, the extended static scan | `import reemote.roles` clean; ruff; both sweeps green |
| 2 | `repo_epel` (§18.1) — facts, a control fetch, two request stages | tree snapshot; second run converges; strict mkdocs |
| 3 | `apache` (§18.2) — the template role: action probe, fact loop, checkpoint, handler | two-run convergence; handler gating |
| 4 | `php` (§18.6) and `php_xdebug` (§18.7) — checkpoint union plus `yield from` outputs | xdebug deletes its copied version table |
| 5 | The remaining ten roles, one commit each (§18) | the per-role gate below |
| 6 | Retire `examples/roles/*` monoliths and `docs/examples/roles/*`; switch the nav; update both migration skills | no dangling link; strict build; full suite |

The per-role gate is: `uv run pytest tests/roles/test_<role>.py tests/test_examples.py`,
`uv run ruff check src/reemote/roles/ examples/roles/`,
`NO_MKDOCS_2_WARNING=1 .venv/bin/mkdocs build --strict`, and — where the demo host's family
matches the role's — two live runs, the second reporting no change.

The falsifier for phase 4 is copied from the Option B note: converting php-xdebug **must
delete** its copied Ubuntu version table and its hand-inherited `php_version` /
`php_extension_conf_paths` reads. If it does not shrink, the return-value dependency channel
is not carrying its weight and the design should be revisited before phase 5.

---

## 16. Decisions

All twelve are decided, and they stay in place with their original numbering so earlier
discussion still lines up. Where a decision has a reopening condition, it is stated.

1. **Names — decided: one rule.** Constructors are short and verb-like (`fact`, `act`,
   `checkpoint`, `control`, `scoped`); types are uniformly `<Kind>Request` (`FactRequest`,
   `ActionRequest`, `CheckpointRequest`, `ControlRequest`), so `Barrier` reads as four of one
   kind and the type follows from the constructor. Every barrier carries the same two
   metadata fields and nothing else — `name` and `scope` — so `checkpoint(label=...)` becomes
   `checkpoint(name=...)`, which deletes the redundant `label`/`name` pair §4.3 used to have
   and gives the tree one label rule for all four. Prose keeps the concept nouns (`fact`,
   `act`, `checkpoint`, `control`); the `*Request` types are the constructor item, not the
   idea.
2. **Checkpoint payload — decided: a bare `ApplyReport`.** The `yield` expression is the
   wave's `ApplyReport`, sliced to the checkpoint's scope. `changed_hosts` needs nothing
   else, the name is the literal the body just wrote, and the wave number is a position in
   the tree (§7.2). §4.3 and §5.5 are written this way, so phase 1 needs no wrapper and no
   later breaking change.
3. **Async role factories — decided: allowed.** A factory may be `async def ... -> Deploy`
   and is awaited at the call site, so controller-side IO that is uniform across the
   inventory runs once per role instead of once per host (§9). Accepted costs: asyncness
   propagates up to `main()`, so the parent of an async role is itself async; an async
   factory is not a `Deploy`, so it cannot be passed to `run_waves`, `scoped` or `yield from`
   un-awaited; and its IO runs even under `dry` and outside any replay journal (§6.3, §13).
   `Control` remains for per-host and barrier-dependent IO (§4.4).
4. **`Control` granularity — decided by 3.** No hoisting convention is needed: inventory-wide
   IO is an `async def` factory, and per-host or barrier-dependent IO is `Control` (§4.4).
   The barrier itself is unchanged.
5. **`scope` placement — decided: a field on `OperationRequest`.** One defaulted tuple,
   normalized in `__post_init__`, included in `__hash__` and excluded from `_effective_key`
   (§3.3, §11.2). The side table was rejected as fragile across re-materialization, and a
   wrapper as hostile to `isinstance`; a core value type gaining one report-only field is the
   smaller change.
6. **A `dry` read-only mode — decided: no.** A fact read before unapplied requests reports
   pre-change state, so a plan built from it describes a world the run will not be in — and an
   `Act` probe is not guaranteed read-only, so running it would make "dry" a lie in exactly
   the case it matters. §6.4 is the better answer: a converged rerun is cheap and idempotent
   and reports `no change`, so the second run *is* the preview, with no declaration to trust.
   Reopen only with a per-fact opt-in (`dry_safe: bool` on the fact class) plus a separate
   `dry="facts"` mode, never by loosening `dry` itself.
7. **A data-less `Boundary` — decided: no fifth type.** `yield checkpoint("...")` already *is*
   the boundary: it forces a rendezvous, closes the wave, prints a line, and the payload can
   be ignored. Four is the whole vocabulary a sync body needs, and a fifth costs a branch in
   `Barrier`, the loop and the report plus every test that enumerates the kinds. A boundary
   that only groups output is served by `scope` (§3.3) and the wave headings (§7.2).
8. **A notification registry — decided: no.** The relay stays a value: php returns
   `webserver_notified`, the composing role hands it to apache's handler (§10.4). A
   driver-owned `{(scope, handler): hosts}` map would be implicit state the report must then
   explain, and it rebuilds address-based coupling — the thing `scope` exists to keep out of
   `name`. The corpus has one edge (php → apache), and one edge does not justify an address
   space. Reopen at three or more edges, or when a role must notify a role the program cannot
   name statically; even then prefer a value (`HandlerAddress(scope, name)` collections passed
   to the handler factory) over driver state.
9. **`run_prepare`/`run_apply` — decided: stay public.** They are documented, tested and sit
   beneath `run()`, which is unchanged and remains the whole existing corpus (§11.8). Mark
   them in the docs as the barrier-free two-phase path and point `run()`'s docstring at
   `run_waves` for barriers — no deprecation, no removal. Removing them would break the
   public API for no gain, and keeping them is part of why `compose`/`sub_deploy` can stay
   untouched (§3.2).
10. **Replay (C2) — decided: out of scope, but not designed out.** A replay is a fresh body
    re-run against a journal of `(wave, scope, barrier, outcome)` plus the applied requests in
    order: each barrier `yield` consumes one entry, each request `yield` must match the
    journaled request **by value** — the `run()` comparison, re-aimed. Nothing in the model
    blocks that, and rules 3 and 12 are what make the re-run reach the same yields. Two known
    exclusions, both already stated: an `async def` factory's IO sits outside the journal and
    re-runs (§9), and `dry` has no outcomes to journal (§6.3). One discipline to keep today
    so the door stays open: keep `Control` results simple (`str`/`bytes`/`dict`/pydantic),
    because a journal must serialise them — every current site already does, and `repo_epel`
    returns `key_text` rather than the `BytesIO` it builds in the body. If it is ever
    greenlit, the work is a journal format, a replay mode in `run_waves` and the matching
    rule above — no change to a barrier, a role or `changed_hosts`.
11. **`compose(dedupe=True)` — decided: no.** No scope-local dedupe, and no cross-role dedupe
    either: `compose` stays request-only (§3.2), so a collapsed request can never land under
    another role's scope. Inside a barrier-free `run()` program every request shares one
    scope, and dedupe stays sound exactly as it is today.
12. **Role placement — decided: first-class library citizens.** The corpus moves from
    `examples/roles/<name>/<name>.py` (14 monolithic scripts, ~11,500 lines) to
    `src/reemote/roles/<name>.py` (14 modules behind one explicit `__init__`), and their
    pages move from `docs/examples/roles/` to a top-level `docs/roles/` section rendered with
    mkdocstrings (§17). A role is therefore public API: typed variables, a documented
    factory, semantic-versioning obligations and the ruff/typing gates that go with them.
    What is *not* decided away is the runnable artifact — a thin ~25-line runner per role
    stays under `examples/roles/`, because the docs pages, `run_examples.py` and the offline
    sweep all need an executable target (§17.3). Reopen only if the runners are judged
    redundant once every doc page carries its own transcript.

---

## 17. Roles as first-class library citizens

Decision 12 is the move; this section is its mechanics — layout, module contents, the
runner, packaging, tests, docs, and the task list. §18 is one subsection per role.

### 17.1 Layout and naming

```text
src/reemote/roles/
├── __init__.py       # explicit re-exports; never star-imported into reemote/__init__.py
├── _render.py        # the only Jinja2 / PyYAML import site (§17.4)
├── apache.py         nginx.py          php.py
├── composer.py       nfs.py            php_xdebug.py
├── datadog.py        ntp.py            postgresql.py
├── docker.py         java.py           repo_epel.py
├── drush.py          memcached.py
```

The `ansible-role-` prefix and the hyphens go, because the directory name is now an
importable module name: `ansible-role-php-xdebug` → `php_xdebug`,
`ansible-role-repo-epel` → `repo_epel`, `ansible-datadog` → `datadog`. The factory inside
keeps the short name (`php_xdebug(...)`), which is what §9 and §10 already assume.

One collision, handled deliberately: `reemote.postgresql` is *already* a name — the module
namespace from `src/reemote/modules/postgresql/`, re-exported at top level. So

- `roles/__init__.py` is **never** star-imported into `reemote/__init__.py`. Roles are
  reached as `from reemote.roles import postgresql`; `reemote.postgresql` stays the module
  namespace. `tests/roles/test_exports.py` asserts exactly this (§17.5).
- inside `roles/postgresql.py`, the module import is aliased
  (`from reemote.modules import postgresql as pg`), because the factory defined in that file
  is also called `postgresql` and would shadow it.

No other role name collides with a top-level `reemote` export.

### 17.2 What a role module contains

| Part | Rule |
|---|---|
| `<Role>Vars` | one pydantic model: `frozen=True`, `extra="forbid"`, unprefixed field names, every Ansible default carried over, `tuple` never `list` (§8.1) |
| the factory | `def <role>(v: VarsSet[<Role>Vars]) -> Deploy`, or `async def ... -> Deploy` when its control-side IO is inventory-wide (§9, §16.3) |
| outputs | a frozen dataclass **only** where a composing role consumes it (§10.4); otherwise the generator returns `None` |
| helpers | module-level pure functions — resolvers, renderers, plan builders, request factories. None reads a host, none awaits |
| `__all__` | the vars model, the factory, the outputs dataclass. Helpers stay private by convention |

The translation is mechanical, and this table is the whole of it:

| Today, in `examples/roles/*` | In `src/reemote/roles/*` |
|---|---|
| `role_settings(host)` merging DEFAULTS with `host.extra` | `<Role>Vars` + `resolve_vars(cls, "<role>_", inventory)` (§8.2) |
| `gather_fact([host], F, ...)` in `main()` | `yield fact(F, ...)` |
| `gather_action([host], Shell, command=...)` in `main()` | `yield act(Shell, command=...)` |
| `await execute(inventory, build_x(plans), "Deploy N: ...")` | `yield from build_x(...)(host)`; the wave boundary is implicit |
| `changed_hosts(report_x, NAMES)` in the gap between deploys | `changed_hosts(yield checkpoint("x done"), NAMES)` |
| `await files.fetch_url(url)` in `main()` | `async def` factory if the URL is inventory-wide; `yield control(files.fetch_url, url)` if it comes from a fact (§4.4) |
| `plans: dict[str, dict]` frozen in `main()`, keyed by host name | local variables — the per-host body *is* the plan |
| `names = sorted({n for s in settings.values() for n in ...})`, a union over every host | the host's own names; the union was only needed because one deploy served all hosts |
| `by_action` / `compose(*service_handler(...))` partitioning | gone: a per-host body picks its own action and yields one request |
| `print(...)` progress and the `== ... ==` banners | deleted; `WaveReport.tree()` replaces them (§7.2) |
| `try/except/finally: await host.aclose()` | deleted; `run_waves` owns the hosts (§13) |
| `execute()`'s dry-then-real double `run()` | deleted; `dry` is a whole-run mode now (§6.3) |
| `if __name__ == "__main__": asyncio.run(main())` | deleted; the runner (§17.3) |

Three of those rows are where the line count actually goes. The `plans` dict, the
whole-inventory `names` union and the `by_action` partition all exist only because one
deploy object had to serve every host at once; a per-host body makes each of them
unnecessary rather than smaller. The dry-then-real `execute()` is the other big deletion —
every one of the 14 scripts runs each deploy **twice**, printing a plan and then an apply
report, and that doubling is a two-phase artifact §6.1 removes.

### 17.3 The thin runner

```python
# examples/roles/apache.py — the whole file
"""Run the apache role against an inventory."""
import asyncio
from pathlib import Path

from reemote.roles import ApacheVars, apache

from role_runner import role_main  # examples/roles/role_runner.py

if __name__ == "__main__":
    asyncio.run(role_main(apache, ApacheVars, "apache_", Path("inventory.json")))
```

`role_runner.py` holds what all 14 scripts duplicate today: read and `validate_inventory` the
document, `resolve_vars`, `await run_waves(...)`, print `report.tree()`, and map the typed
errors (`InventoryError`, `CommandError`, `HostConnectionError`, `PrivilegeError`,
`OSError`, `ValueError`, `TypeError`) to a one-line message and `SystemExit(1)`. It also
owns the `--dry` and `--inventory` flags. Roughly 60 lines once, instead of ~80 lines
fourteen times.

It is named `role_runner.py`, not `runner.py`, because the offline harness imports every
example in one process and Python caches bare module names in `sys.modules` — the corpus
convention is a globally unique basename (`provision_lxd.py`, `provision_gem.py`), and
`runner` is only accidentally free today (§19.4).

Two exclusions, both needed or the sweeps break: `role_runner.py` is not a role, so
`run_examples.py`'s `is_excluded` and `test_examples.py`'s `DRIVER_ONLY_ENTRYPOINTS` /
`OFFLINE_EXAMPLES` must name it. A runner whose factory is `async def` (§18.5) needs
`await factory(vars)` before `run_waves`, so `role_main` inspects
`asyncio.iscoroutinefunction` rather than taking a pre-built `Deploy`.

### 17.4 Packaging: the `templates` extra

Five roles import Jinja2 lazily and catch `ImportError` in `main()` with the advice
`uv run --with jinja2 python ...`; datadog does the same for PyYAML. That advice is
unavailable to library code — a consumer who installed `reemote` cannot be told to re-run
the interpreter with a different environment. So:

```toml
[project.optional-dependencies]
templates = ["jinja2>=3.1", "pyyaml>=6"]
```

`src/reemote/roles/_render.py` becomes the only import site for both, at module scope, with
a docstring naming the six roles that need the extra (apache, datadog, memcached, nginx,
ntp, postgresql). The lazy `try: import jinja2`, the `except ImportError` branch and the
`--with` advice all disappear.

Nothing is re-templated in the move. datadog's four YAML renderers and php's config
renderers are hand-written Python that emit what the Ansible `.j2` files emitted; they stay
Python, and only their `import yaml` moves. Converting them to Jinja2 would be a rewrite
masquerading as a relocation.

### 17.5 Tests

| Today | After |
|---|---|
| `tests/test_roles_ansible_<role>.py` — 14 files, 8,381 lines, each loading its script by path with `importlib.util.spec_from_file_location(name, EXAMPLE_PATH)` | `tests/roles/test_<role>.py`, `from reemote.roles.<role> import ...`. `REPO_ROOT`, `EXAMPLE_PATH` and the spec loader are deleted |
| the replay harness: gather → plan → deploy on `SimulatedHost`, run twice, assert the second run reports no change | the same assertion against `run_waves` on `SimulatedHost`, run twice |
| `monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)` (drush, php_xdebug) and `apt_module` (docker) | unchanged — the seam belongs to the library, not the role |
| 31 `SNAPSHOT_ARGS` entries keyed by `("examples/roles/<role>/<role>.py", "<factory>")` | deleted in the same commit as the role moves; `test_examples.py` then only sees the thin runners |
| the static mutable-container scan over `EXAMPLES_ROOT` | extended to walk `src/reemote/roles/` (§11.8) — a `list` default in a vars model is a library bug, not an example bug |
| — | new `tests/roles/test_exports.py`: `roles.__all__` matches the modules on disk, nothing from `reemote.roles` is star-imported into `reemote/__init__`, and `reemote.postgresql` is still the module namespace |

Two-run convergence stays the central assertion, and it gets *stronger*: today the second
run is a second `main()` invocation with a fresh plan freeze, so a role could converge by
accident of its own caching. Under `run_waves` the second run re-derives every barrier
result, so convergence proves the barriers are honest.

### 17.6 Documentation

- `docs/roles/index.md` — the catalogue: one row per role, naming the Ansible role it
  migrates, its host family, its wave count, whether it publishes outputs, whether it needs
  `reemote[templates]`, and whether it needs more than one `run_waves` pass.
- `docs/roles/<role>.md` — one page per role, **rewritten**, not copied. Fixed skeleton:
  1. what it is, and its provenance (the Ansible role, its tasks, the coverage table)
  2. install (`pip install reemote[templates]` where §17.4 applies)
  3. variables (the `<Role>Vars` field table: name, type, default, the Ansible name)
  4. API (`::: reemote.roles.<module>` — mkdocstrings, so the docstring *is* the page)
  5. run it (the thin runner, and `--dry`)
  6. what it does (the wave list — replacing today's numbered "1. `gather_fact` …
     2. `execute` …" prose, which describes `main()` and not the role)
  7. transcript (regenerated from a real run)
  8. deliberate deviations from the Ansible role
  9. scoped out

  Sections 6 and 7 are why the pages cannot be moved with `git mv`: today's transcripts
  print each deploy twice, plan then apply, and are keyed by `main()`'s banner strings.
  Under §17.2 neither exists.
- `mkdocs.yml`: a top-level `Roles:` nav section replaces `Examples → Roles` (lines
  181–194 today), and mkdocstrings `paths:` gains `src/reemote/roles`.
- `docs/examples/roles/*.md` are **deleted**, not redirected, in the same commit as the
  page that replaces them. A page out of the nav but still on disk publishes as an orphan,
  which `--strict` will not catch.
- Both migration skills (`.qoder/skills/migrate-ansible-to-python/`,
  `.qoder/skills/migrating-ansible-roles/`) prescribe `docs/examples/roles/` as the
  destination for a migrated role and `examples/roles/<name>/<name>.py` as the program
  shape. Their `worked-example.md` and `construct-recipes.md` must be repointed in phase 6,
  or the next migration re-creates the old layout.
- Gate: `NO_MKDOCS_2_WARNING=1 .venv/bin/mkdocs build --strict` after every page.

### 17.7 Task list

| # | Task | Depends on | Done when |
|---|---|---|---|
| T1 | Engine: `stages.py`, `rolevars.py`, `run_waves`, `_deploy_guard`, `WaveReport` (§11.1–§11.5) | — | the full existing suite is green and `run()` is byte-identical |
| T2 | Package skeleton: `roles/__init__.py`, `_render.py`, the `templates` extra, `tests/roles/`, the extended static scan, `tests/roles/test_exports.py` | T1 | `import reemote.roles` is clean; ruff; strict build |
| T3 | Shared runner `examples/roles/role_runner.py`, excluded from both sweeps | T1 | `run_examples.py` and `example_files()` skip it |
| T4 | `repo_epel` (§18.1) — phase 2 | T2, T3 | the §15 per-role gate |
| T5 | `apache` (§18.2) — phase 3, the template role | T4 | the gate |
| T6 | `php` (§18.6) — phase 4 | T5 | the gate; three checkpoints, unioned |
| T7 | `php_xdebug` (§18.7) — phase 4 | T6 | §15's falsifier: the module shrinks |
| T8 | `nginx` (§18.3) | T7 | the gate |
| T9 | `postgresql` (§18.4) — two factories, two passes | T7 | the gate, on both passes |
| T10 | `datadog` (§18.5) — the first `async def` factory | T7 | the gate, offline |
| T11 | `composer` (§18.8) | T7 | the gate, offline |
| T12 | `drush` (§18.9) | T7 | the gate, offline |
| T13 | `ntp` (§18.10) | T7 | the gate |
| T14 | `docker` (§18.11) | T7 | the gate, offline |
| T15 | `java` (§18.12) | T7 | the gate |
| T16 | `memcached` (§18.13) | T7 | the gate |
| T17 | `nfs` (§18.14) | T7 | the gate |
| T18 | `docs/roles/index.md` catalogue | T4–T17 | every role linked; strict build |
| T19 | Retire the leftovers: `docs/examples/roles/` gone, the `Examples → Roles` nav gone, both migration skills repointed, `examples/roles/*/<name>.py` monoliths gone | T4–T18 | no dangling link; the full suite |

Each of T4–T17 is one commit carrying five things together — the module, the thin runner,
`tests/roles/test_<role>.py`, `docs/roles/<role>.md`, and the deletions (monolith, old
test, old page, `SNAPSHOT_ARGS` rows, nav row). Splitting a role across commits leaves a
window where the docs point at a file that no longer exists. T4–T7 are §15's phases 2–4 and
are strictly ordered; T8–T17 are phase 5 and depend on T7 only because the first four roles
settle the page skeleton, the runner's error mapping and the checkpoint-union idiom. They are
otherwise independent and can be reordered or parallelised.

---

## 18. Per-role migration notes

One subsection per role. Each gives the shape today, the migration, and the points that are
easy to get wrong; §17.7 is the order they land in. Line counts are the monolith's; the
numbers in brackets are the counts of `execute` / `gather_fact` / `gather_action` /
`changed_hosts` calls in `main()`, which is what has to become barriers.

A warning that applies to all fourteen: today every deploy is run **twice** — `execute()`
plans with `dry=True`, prints the plan, then applies and prints the report. Any habit built
on that doubling (a hoisted `BytesIO`, a `names` union, a frozen `plans` dict, a
`by_action` partition) is a two-phase artifact and must be *removed*, not carried over.

### 18.1 `repo_epel` — 483 lines [2 / 2 / 0 / 0]

The smallest interesting role and the first one to move (§10.2 is the target, verbatim).
Today: `LinuxDistribution` then `FileExists` per host, a frozen plan, a control-side key
fetch, two deploys.

Migration: two `fact` barriers, one `control` barrier, then `yield from install(...)` and
`yield from configure(...)` in a single stream — no barrier between them, because the driver
applies install's requests before it pulls configure's first one. The old two-deploy split
collapses.

Watch out for:

- **The `BytesIO` rule inverts.** The script builds the key stream once in `main()` with a
  long comment explaining that `op()` compares streams by identity, so a fresh one per phase
  would trip the guard. Under `run_waves` there is one pass, so building it in the body is
  correct and hoisting it becomes the bug — a hoisted stream is shared across hosts and must
  be rewound (§6.2). Do not "fix" it back.
- **The fetch cannot move into an `async def` factory.** The URL is derived from `major`, a
  per-host fact, which is §4.4's `control` case exactly. A factory would have to fetch every
  EPEL major's key for every host.
- **`control` returns `key_text: str`, never the `BytesIO`** — §16.10's journal discipline,
  and the reason the body builds the stream after the barrier.
- Keep **both** family guards: the static `host.family` check and the `LinuxDistribution`
  re-check that raises. The second is the only proof in the corpus that a declared family
  matches the machine.
- **No checkpoint.** Nothing reads an outcome, and adding one would create a barrier-only
  wave that reports nothing (§4.3).

### 18.2 `apache` — 871 lines [3 / 2 / 2 / 1]

The template role: an action probe, a per-host fact loop, one checkpoint, one handler.
§10.3 is the target.

Watch out for:

- **The version probe is `act`, not `fact`.** `apache2 -v` is a command, and it only
  succeeds after install applied. Its result is data: check `exit_status` and raise, because
  a barrier never raises on a non-zero exit.
- **The certificate loop yields one wave per certificate**, and hosts with different
  certificate counts park asymmetrically. That is legal (§10.3), but it makes the wave count
  host-dependent — §5.7's "3 + one per SSL vhost" is the row the doc page must quote.
- **The checkpoint goes immediately after `yield from configure(...)`.** The handler's
  notification set comes from the configure wave only; a checkpoint placed after the handler
  wave reads the handler's entries and notifies nobody.
- **`ApacheOutputs` must be returned on the early-return path too** (`host.family != "Ubuntu"`),
  or a composing role's `yield from` gets `None` and the `web_tier` in §9 breaks.
- `lineinfile_transform` and the Jinja2 vhost renders are pure and stay pure; only the
  `Environment` construction moves to `_render.py` (§17.4).

### 18.3 `nginx` — 973 lines [4 / 2 / 0 / 3]

Migration: setup → `checkpoint("setup done")` → install → `fact(Cpus)` → configure →
`checkpoint("configure done")` → two handler channels.

Watch out for:

- **Three `changed_hosts` reads, two checkpoints.** The PPA gate reads the *setup* report
  (the role's `register: nginx_ppa_added` + `when: nginx_ppa_added is changed`); restart and
  reload both read the *configure* report, with two disjoint name sets. It is tempting to add
  a third checkpoint — don't; the second one is read twice.
- **`fact(Cpus)` must precede the render**, and the render must stay a pure function of
  `(settings, worker_processes)`. The `None → f'"{cpus}"'` quoting reproduces the role's
  folded-scalar default; drop the quotes and `nginx.conf` drifts on every run.
- The PPA branch reads only static `host.extra` today, so both phases agreed. It now depends
  on a checkpoint, which is a stronger reason to keep `PPA_NAME` stable and unprefixed
  (§7.3).
- **`DictLoader` over bundled template strings is the custom-template override mechanism.**
  Keep it, but build the `Environment` in `_render.py` so the override path is one function.
- Unlike §18.4, `nginx` collides with nothing at top level.

### 18.4 `postgresql` — 1,400 lines [6 / 3 / 2 / 1]

The hardest migration, and the only role that cannot be one pass. It uses **two host
views**: the normal one, and `pg_inventory`, which adds
`privilege.sudo_user = postgresql_user` so peer authentication works.

Migration: split into two factories and run two `run_waves` passes.

- `postgresql(v)`, normal view: setup, initialize (profile + data directory), the initdb
  action, configure, service, handler.
- `postgresql_data(v)`, postgres view: the `PostgresqlRoles` catalog fact, users, databases,
  privileges.

Watch out for:

- **Run-as users come from the inventory, never from a request** (`OperationRequest`'s
  docstring; `modules/postgresql` says the same). And `run_waves` builds one host list and
  closes it in its own `finally` (§13). So the second view is a second pass — there is no
  barrier that can change it, and adding one is deliberately out of scope.
- **initdb stays in pass A**, as `act(Shell, command=f"sudo -u {pg_user} {initdb_command}",
  sudo=True)`: the elevation to root comes from the inventory, the drop to postgres is inside
  the command. It has to sit between initialize and configure, so putting it in pass B would
  mean three passes. Record it as a deviation in the doc page.
- **`PostgresqlRoles` must be read after the service is up** — peer auth needs a listening
  socket — so it is pass B's first barrier, not pass A's last.
- **The handler moves earlier.** Its union is `initialize | configure`, both in pass A, so
  it now runs *before* the users and databases instead of after them. No data request
  notifies, so the reordering is safe, but it is a behaviour change and belongs in the
  deviations section.
- Delete `pg_hosts`, `pg_by_name` and their `finally: aclose()`; pass B builds its own hosts
  from `pg_inventory` inside its own `run_waves`.
- **The file name shadows the module namespace** — alias the import (§17.1).
- The `except ImportError` / `--with jinja2` branch dies with §17.4.

### 18.5 `datadog` — 1,545 lines [7 / 6 / 0 / 1]

The largest file and the most stages: legacy cleanup, apt bootstrap, signing keys + repo,
the agent package, configuration, `conf.d` cleanup, handlers.

Watch out for:

- **`fetch_keys` uses `urllib.request.urlopen`** — blocking, and *not* on the
  `_fetch_url_bytes` seam the rest of the corpus is tested through. Move the five URLs to
  `await files.fetch_url(...)` inside an `async def` factory: they are inventory-wide
  constants, which is §16.3's case exactly. This is also what makes the role testable offline
  without inventing a new seam.
- **The factory is `async def`, so asyncness propagates**: the runner must `await datadog(v)`
  (§17.3), and any composing role must itself be `async def` (§9).
- **An async factory's IO runs even under `dry`** (§6.3): five key downloads happen for a
  preview. Say so in the doc page — it is the one role where `--dry` touches the network.
- **`yaml.safe_dump(..., indent=2, width=160, sort_keys=False)` is load-bearing.** The four
  renderers are hand-written Python despite the Ansible `.j2` names; change a keyword and
  every config file rewrites on every run.
- **`FindFiles(CONF_D, fname="conf.yaml*", maxdepth=2)` must be a barrier *after* configure
  applied**, or the sweep enumerates the pre-install tree and spares the stale entries it
  exists to remove.
- The sweep's guard — skip anything that is not `conf.d/<check>.d/<suffix>` rather than build
  a bogus `conf.d/conf.d/conf.yaml` path — is a pure function over the fact result and stays
  pure.
- **`notified_hosts(reports, names)` unions across every deploy report**, reproducing
  Ansible's play-wide notification flush. Under C1 that is one checkpoint per notifying stage,
  unioned in the body — the same shape as php (§18.6), not one checkpoint at the end.

### 18.6 `php` — 1,382 lines [4 / 7 / 0 / 5]

The checkpoint-union role; §10.4 is the target.

Watch out for:

- **Five reads over three reports become three checkpoints**, one per stage, and the
  placement rule (§4.3) is load-bearing: three checkpoints after one combined wave read two
  empty reports and silently lose every cleanup and configure notification. This is the
  easiest bug to introduce in the whole migration, and it fails *quietly* — the handler
  simply never runs.
- The three channels are not symmetric: `package_changed` reads the **install** wave only
  (it is a gate, not a notification), `webserver_notified` is the **union of all three**,
  `fpm_direct` reads the **configure** wave only. Keep the three name sets distinct.
- **`FindInFiles` (the stray packaged-extension sweep) and `FileExists` (the pool directory)
  sit between cleanup and configure**, so they are barriers in their own waves, not steps in
  a plan freeze.
- The renderers are pure Python; a custom pool template is deliberately **rejected**. Keep
  the rejection — it is a documented deviation, not an omission.
- `PhpOutputs.webserver_notified` is the relay value apache consumes (§16.8). It must be the
  union, not the last checkpoint's set.
- The Ubuntu version table published in `PhpOutputs` is what deletes §18.7's copy.

### 18.7 `php_xdebug` — 754 lines [3 / 4 / 2 / 2]

Migration: §10.4 verbatim — `php_out = yield from php(php_v)(host)`.

Watch out for:

- **Do not name the local `php`.** The assignment makes it local for the whole body, so the
  factory reference on that same line raises `UnboundLocalError`. §10.4's comment records the
  trap; keep the comment.
- **§15's falsifier lives here.** The copied Ubuntu version table and the hand-inherited
  `php_version` / `php_extension_conf_paths` reads must be *deleted*, replaced by
  `php_out.version` and `php_out.extension_conf_paths`. If the module does not shrink, the
  return-value channel is not carrying its weight.
- **The `FileExists(creates=...)` gate branches the body on a barrier result** — the thing
  §6.2 says is now the point rather than a determinism bug. Resist the urge to hoist it.
- The source build is `gather_action(Shell, sudo=True)` today → `yield act(Shell, ...,
  sudo=True)`, and the non-zero exit must be turned into a `CommandError` explicitly.
- **Both `changed_hosts` reads come from one report** (webserver names and FPM names), so this
  is one checkpoint with two name sets — not two checkpoints.
- **The two prefixes nest.** `resolve_vars(PhpVars, "php_", inventory)` claims every
  `php_xdebug_*` key and raises `RoleVarsError` on it, so the dependency role needs
  `nested_prefixes=("php_xdebug_",)` (§8.2, amended at T7). Found here because this is the only
  nested prefix pair in the corpus; the unfixed call makes the role unresolvable rather than
  wrong, so it cannot be met later by accident.
- php's own handler wave already ran inside the `yield from`. A checkpoint can only read the
  wave it closes, so xdebug's checkpoint cannot see it — which is exactly what stops the
  webserver being restarted twice. Do not try to "help" by merging reports.

### 18.8 `composer` — 730 lines [3 / 3 / 2 / 0]

Watch out for:

- **The signature fetch is gated on "any host needs install"**, an inventory-wide condition
  computed from per-host facts. A body sees only its own host, so the gate is not expressible.
  Use `control` per host that needs the install: the URL is a constant but the *need* is
  per-host, and §4.4 puts per-host IO in `control`. The alternative — an `async def` factory
  that always fetches — trades one HTTP GET for a simpler body; either is defensible, but the
  choice must be recorded in the doc page's deviations.
- **The installer and each `composer require` are actions**, with `env` and `sudo`. Each
  becomes an `act` barrier, so the wave count grows with the package list. Quote it in the
  doc page rather than letting a reader discover it.
- Actions running mid-body is new: the installer's stdout is available to the same body that
  decided to run it, which is what lets "Deploy 3: composer PATH entry" fold into the stream.
- Only one of the two textual `fetch_url` hits is a call; the other is docstring prose.
  Check before removing an import.

### 18.9 `drush` — 396 lines [1 / 2 / 0 / 0]

The smallest role. One deploy, two facts (`FileLink`, `File`), no handler, no checkpoint.

Watch out for:

- Two `fact` barriers then `yield from` — and nothing else. If this migration needs a
  checkpoint, something has been over-applied.
- The download goes through `files.download`, whose `_fetch_url_bytes` seam the test
  monkeypatches. That stays exactly as it is.
- Its local `execute()` wrapper (dry-then-real) is deleted, not moved (§17.2).

### 18.10 `ntp` — 903 lines [3 / 4 / 0 / 2]

Migration: install → `checkpoint("install done")` → timesyncd/daemon/config →
`checkpoint("configure done")` → two handler channels.

Watch out for:

- **Two checkpoints, not one.** `cron_notified` reads the **install** report and
  `ntp_notified` reads the **configure** report. They are different reports, so they cannot
  share a checkpoint the way nginx's two channels can (§18.3).
- **The timesyncd gate is read after install** (`SystemdUnitFiles`), because the unit only
  exists once the packages are in. It is a `fact` barrier in its own wave, and the branch
  that disables timesyncd stays inside the body.
- The `ntp.conf.j2` / `chrony.conf.j2` dispatch is a pure function of the resolved daemon.
  Keep it; move only the `Environment` construction (§17.4).

### 18.11 `docker` — 724 lines [5 / 4 / 0 / 1]

Watch out for:

- **`meta: reset_connection` changes meaning.** The script's comment says it needs no request
  because "every `run()` builds fresh hosts". Under `run_waves` the hosts stay open for the
  whole run (§13), so that reason is gone. The behaviour survives only because the group stage
  is *last*: the new membership takes effect on the next pass. If a composing role ever needs
  it mid-run, the program must run two passes — and this is the one place where the migration
  makes an Ansible behaviour unreachable rather than merely different, so the doc page must
  say so plainly.
- **The `Groups` getent gate becomes `yield fact(Groups)` after the package wave**, and it
  must still raise when `docker` is absent — the role's `getent` task fails the play the same
  way. A barrier result is data; the raise is the role's, not the driver's.
- **The handler union spans two reports** (packages and configure), so two checkpoints,
  unioned — the php shape (§18.6), not the nginx shape.
- **The `by_action` partition and the whole-inventory `names` union both disappear.** They
  exist only because one `compose(*service_handler(...))` deploy served every host; a
  per-host body reads its own `restart_handler_state` and yields one request (§17.2).
- `ensure_home=False` keeps the request a pure membership append. That deviation is easy to
  lose when the request is re-typed, and losing it makes the role chown home directories.
- The apt repository setup uses the `apt._fetch_url_bytes` seam the test monkeypatches;
  unchanged.

### 18.12 `java` — 402 lines [1 / 2 / 0 / 0]

One `LinuxDistribution` barrier, a pure resolver that picks the package set from the major
version, one deploy, one render, no handler, no checkpoint. The smallest surface after
`drush`, and the one to reach for if a later change needs a cheap end-to-end check.

Watch out for: the major-version gate raises on an unsupported distribution. Keep the raise
in the body, immediately after the barrier — it is the role's only error path.

### 18.13 `memcached` — 478 lines [2 / 0 / 0 / 1]

No facts at all: the plan is entirely static. So the only barrier in the whole role is the
checkpoint between configure and the handler.

Watch out for:

- **Two waves total** — one for the requests, one for the handler. If a third appears, a
  barrier has been added speculatively.
- Only one of the two textual `changed_hosts` hits is a call; the other is the module
  docstring's numbered walkthrough, which §17.6 replaces with the wave list.
- The Jinja2 render of the config is pure and moves to `_render.py`'s `Environment`.

### 18.14 `nfs` — 459 lines [1 / 0 / 2 / 1]

The only role whose handler is a **command** rather than a request:
`handlers/main.yml` says `reload nfs: command: exportfs -ra`.

```python
wave_report = yield checkpoint("exports done")
if host.name in changed_hosts(wave_report, NOTIFYING):
    result = yield act(Shell, command=RELOAD_COMMAND, name="reload nfs")
    if result["exit_status"] != 0:
        raise CommandError(...)   # a barrier result is data, not an error
```

Watch out for:

- **The handler is an `act` barrier in a wave after the checkpoint**, and it is conditional
  per host: hosts that were not notified simply do not yield it. Asymmetric barriers are
  legal (§10.3).
- **Do not "simplify" it into `reload_handler` or `service_handler(action="reloaded")`.** The
  script already documents why that is unfaithful: `systemd.service(reloaded=True)` reloads
  only an already-running unit and the verb is skipped on the role's default path. The doc
  page's deviations section says this today and must keep saying it.
- A barrier's non-zero exit does not raise. Check `exit_status`, or the reload fails
  silently — and a silently un-reloaded `exportfs` is the one failure this role cannot
  tolerate.
- `EXPORTS_HEADER` plus the pure exports render stay as they are; no facts, no templates
  extra.

---

## 19. The demonstration corpus

§15 and §17 move the roles. This section covers the other 407 example scripts, which the
migration path never mentions. The summary: nothing breaks, five files start lying, and the
design's five primitives have no demonstration anywhere.

### 19.1 What does not change

`run()` and both its phases are untouched (§5.8), so every barrier-free example keeps
working byte-identically. Of the 421 scripts in `examples/`:

| Tree | Scripts | Affected |
|---|---|---|
| `examples/modules/*` | 380 | none |
| `examples/tutorials/website` | 5 | none |
| `examples/demonstrations/privilege` | 3 | none |
| `examples/demonstrations/connectors` | 1 | none |
| `examples/demonstrations/composition` | 11 | 3 (§19.2) |
| `examples/actions` | 7 | 2 (§19.2) |
| `examples/roles` | 14 | all 14 (§17) |

The eight `composed_*` demonstrations are about `compose`, `sub_deploy`, `dedupe` and `when`
— all unchanged by §3.2 and §16.11 — and need no edit. They do need one shared sentence in
`docs/examples/demonstrations/composition.md`: `compose` and `sub_deploy` now reject a
barrier with a `DeployRequestError` (rule 7), so a reader who tries
`compose(role_a, role_b)` meets an error the page never mentioned. One sentence in the intro
covers all eight; per-file edits do not.

### 19.2 Five files that start lying

| File | Its thesis today | Under C1 |
|---|---|---|
| `composition/two_deploy_split.py` | "Written as ONE deploy, that branch is **illegal** — the generator runs twice … `run_apply` raises `DeterminismError`" | That is `yield fact(...)` in one stream. §6.2's third row inverts the rule: "Branching on a **barrier result** is the entire point" |
| `composition/conf_d_cleanup.py` | "Written as ONE deploy it is **doubly illegal**" — a `FindFiles` snapshot between two cycles | One `fact` barrier mid-stream. §18.5 cites this file by name as the datadog recipe |
| `composition/handler_restart.py` | "the **two-deploy outcome split** … `changed_hosts(report, NOTIFYING)` in the gap between two runs" | `changed_hosts(yield checkpoint(...), NOTIFYING)`. The most-copied pattern in the corpus — 10 of 14 roles call `changed_hosts` |
| `actions/deploy_action_deploy.py` | "the action analogue of `two_deploy_split.py` … actions belong BEFORE, BETWEEN and AFTER deploys, **never inside one**" | `yield act(Shell, ...)` — literally §10.3's apache probe |
| `actions/action_in_deploy_guard.py` | Three "properties of the deploy model" forbidding an action inside a deploy | Behaviour unchanged, reasons stale — see below |

**Re-frame; do not delete.** Three reasons:

1. `run()` and both phases stay public (§16.9), and §6.1 is explicit that the wave model
   trades a *whole-deploy preflight* for a stronger per-request guarantee. The two-deploy
   split is the only way to keep that preflight, so both idioms stay legitimate — and these
   four files are the only place the trade-off is written down.
2. `two_deploy_split.py` and `conf_d_cleanup.py` own `SNAPSHOT_ARGS` entries in
   `tests/test_examples.py`. Deleting the files deletes the only offline coverage of the
   snapshot-factory shape.
3. The frozen-snapshot caveat survives C1: a barrier's result is fresher, but everything
   after it is unpreviewable (§6.3). "Fresher but unpreviewable" against "stale but
   previewable" is a real choice, and one demonstration of each side is how a reader sees it.

The edit is a closing docstring paragraph per file plus a paragraph on its doc page, in one
shared shape:

> On the barrier path (`run_waves`) this is one stream, not two runs: the read becomes
> `yield fact(...)` / `yield checkpoint(...)`, and the branch moves inside the generator,
> which §6.2 permits precisely because there is no second pass. This script keeps the `run()`
> form because it wants a whole-deploy `dry=True` preview of the second step.

`composition.md`'s intro then changes from "three two-deploy scenarios that move a mutable
read … into the gap between two complete prepare/apply cycles" to naming both idioms.

`action_in_deploy_guard.py` needs only a qualifier. Its three stated reasons are properties
of the *two-phase* model, not of the deploy model: reasons 1 and 3 (re-invocation per phase;
no plan-phase counterpart to pair with) do not exist on the wave path, and reason 2 inverts.
The **behaviour** is unchanged — the wave loop keeps the `ContextVar` set around each pull
(§5.4), so a direct `gather_action(...)` call in a body still raises `ActionInDeployError`,
and `yield act(...)` is the sanctioned form (§2).

### 19.3 New demonstrations: `examples/demonstrations/waves/`

The design adds five primitives — `fact`, `act`, `checkpoint`, `control`, `scope` — plus
`VarsSet`/`resolve_vars`, `run_waves`, `WaveReport.tree()` and a narrower `dry`. None has a
demonstration, and a role is a bad place to learn a primitive: §18's roles are 400–1,500
lines each and use four of these ideas at once.

Same style as `composition/`: one scenario per file, 120–200 lines, and — like
`composed_group_tag_roles.py` — runnable in memory against `SimulatedHost`, so the whole
directory is offline and joins `OFFLINE_EXAMPLES`.

| File | Demonstrates | The trap it makes runnable |
|---|---|---|
| `barrier_fact.py` | one `fact` barrier, branch on the result | `two_deploy_split.py`'s scenario in a third of the lines (§6.2 row 3) |
| `checkpoint_handler.py` | `checkpoint` + `changed_hosts` + `restart_handler` in one stream | **placement**: a second checkpoint straight after the first closes a barrier-only wave and reads an empty report (§4.3) |
| `barrier_act.py` | an `act` probe | a barrier result is data: `exit_status` must be checked by hand (§18.2, §18.7, §18.14) |
| `control_fetch.py` | a `control` barrier whose URL comes from a fact | the §4.4 / §16.4 split — inventory-wide IO is an `async def` factory, per-host IO is `control`; and `control` returns `str`, not a stream (§16.10) |
| `scope_two_instances.py` | one factory twice, `scoped("eu", …)` / `scoped("us", …)`, `report.tree()` | no collapse and no instance identity (§7.2, §13) |
| `role_outputs_relay.py` | `out = yield from child(v)(host)`, handed to a sibling's handler | the only cross-role channel (§10.4, §16.8), and the `UnboundLocalError` shadowing trap |
| `vars_typed.py` | `resolve_vars` precedence, an unknown prefixed key | defaults < inventory `extra` < call-site overrides (§8.2); `tuple` never `list` (§8.1) |
| `dry_one_wave.py` | `--dry` with a barrier present | wave 0, then `(unknown until run)` (§6.3). §16.6 decided *against* a read-only dry mode, so the limit must be visible or it gets filed as a bug |
| `run_waves_report.py` | a two-role program printing `format_waves(report)` | §7.2's tree is the design's main user-visible output and nothing else would print it |

If only two land, they are `checkpoint_handler.py` and `dry_one_wave.py`: the first prevents
a **silent** failure — a misplaced checkpoint loses every notification and the run still
exits 0 — and the second prevents a spurious bug report against a limit §16.6 chose
deliberately.

The placement trap is worth asserting rather than describing:

```python
yield from build_install(hv)(host)
first = yield checkpoint("install done")    # closes install's wave — correct
second = yield checkpoint("install done")   # closes a barrier-only wave
assert not second.log                       # every notification silently lost
```

### 19.4 Harness, nav and naming consequences

- **The drain helper is a prerequisite, not a follow-up.** A wave demo's body yields
  barriers, so `tests/test_examples.py` needs `drain(..., until_barrier=True)` (§11.8)
  *before* the first file can land: draining a generator that is never resumed would
  otherwise look like a lost request.
- Each new file adds exactly one parametrized case to
  `test_example_builds_hashable_requests_offline`, needs `main()` behind the `__name__`
  guard, and needs `family` in its inventory entry.
- The static mutable-container scan already sweeps `EXAMPLES_ROOT`, so a new subdirectory is
  covered for free — unlike the `src/reemote/roles/` extension (§11.8), which is real work.
- **Any per-directory helper needs a globally unique basename**, because the offline harness
  imports every example in one process and Python caches bare names in `sys.modules`. This
  is why §17.3's runner is `role_runner.py` and not `runner.py`: `runner` is free today, but
  only by accident, and the corpus convention is `provision_<mod>.py`.
- `mkdocs.yml`: a `Waves:` entry under `Examples → Demonstrations`, beside `Composition:`,
  `Connectors:` and `Privilege:`; mkdocstrings `paths:` gains
  `examples/demonstrations/waves`.
- A new `docs/examples/demonstrations/waves.md` in `composition.md`'s shape: one intro
  paragraph, then a `::: <module>` block with `members: []` per file.

### 19.5 Tasks

Continuing §17.7's numbering:

| # | Task | Depends on | Done when |
|---|---|---|---|
| T20 | `drain(..., until_barrier=True)` in `tests/test_examples.py` | T1 | the existing suite is green with the helper present but unused |
| T21 | `examples/demonstrations/waves/` — the nine files, `waves.md`, the nav entry | T20 | all nine offline in the sweep; strict build |
| T22 | Re-frame the four two-phase demonstrations plus `composition.md`'s intro, and qualify `action_in_deploy_guard.py`'s three reasons | T21 | no docstring states a two-phase rule unconditionally |
| T23 | The §11.9 scope qualifiers: `docs/guide.md` (two sections), `docs/handlers.md`, `docs/migrating.md`, `docs/actions/actions.md` | T21 | strict build; every claim names its path |

T22 and T23 depend on T21 only so the qualifying paragraphs can link to a runnable twin
instead of to this note, which is unpublished. Neither blocks any role task, so §15's phases
are unaffected; the natural slot is phase 6, alongside the skill updates.
