# reemote5

A python library for configuration management, built on an execution model
that is an alternative to [pyinfra]'s two-phase *prepare → execute* design.

Deploy scripts are **Python generators** that yield `OperationRequest`
descriptors instead of calling operations directly. That lets the driver pause
the script at every operation boundary: the prepare phase produces a full plan
for dry-run reporting, the apply phase runs all hosts in lockstep with a fresh
fact cache per step, so sequential *"act, then read"* logic gives the correct
answer in both phases. Facts and operations stay phase-unaware — only the
drivers know which phase they are in.

**Scope.** reemote5 is a library, not a deployment tool. It ships no CLI and
no inventory format: hosts are a plain *inventory* (a list of dictionaries)
handed to `run`. The transport is real -- the default `Host.from_inventory`
factory opens an [asyncssh] connection per entry, gathers facts and runs
commands over it, and `run` closes the connections when it finishes. Both the
command-execution path (`send_command`, the apply drivers) and fact gathering
(each fact's `process`, `FactCache.get`, the operations that await it, and
`run_prepare`) are coroutines, awaited end to end (where pyinfra uses Paramiko
+ gevent). `SimulatedHost`, the in-memory host that lets the same model run
without a network, belongs to the *test framework*
(`tests/simulated_host.py`) and is injected through `run`'s
`host_factory`; it is never exported by the library.

```python
import asyncio
import json
from collections.abc import Generator
from pathlib import Path

from reemote5 import Host, OperationRequest, dnf, files, format_log, op, run

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"


def is_centos(host: Host) -> bool:
    # A `when` guard reads static inventory data (host.group) only, so the
    # prepare and apply phases reach the same eligibility decision and the
    # steps never reach a host that has no dnf.
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    # The welcome configuration only appears once the package is installed.
    # The apply phase rebuilds the fact cache before each step, so this
    # removal still sees it -- no branch, no phase flag.
    yield op(
        dnf.packages,
        name="Install httpd",
        packages=("httpd",),
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.file,
        name="Remove welcome configuration",
        path=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    plans, report, hosts = await run(inventory, deploy)
    print(format_log(report))


asyncio.run(main())
```

Pass `dry=True` to stop after the prepare phase (`report` is `None`), or a
`host_factory` building `SimulatedHost` to exercise a deploy offline -- that is
how the test suite and `tests/test_core.py` run it.

## Getting started

```bash
uv sync                          # library + pytest
uv run python examples/modules/dnf/dnf_packages.py
uv run pytest
```

> **Warning — the examples connect for real.** Running one opens asyncssh
> connections to the hosts in the project-root `inventory.json` and executes
> against them: the script's first pass is the read-only `dry=True` plan, the
> second applies for real (here installing `httpd` and removing
> `/etc/httpd/conf.d/welcome.conf`, both under `sudo`). The `when=is_centos`
> guard limits which hosts are touched, it does not make the run safe on
> machines you do not own — point the inventory at your own hosts, or keep only
> the dry run.
>
> Two scripts need no host at all:
> `examples/demonstrations/composition/composed_group_tag_roles.py` injects the
> test framework's `SimulatedHost` and runs in memory, and
> `examples/actions/action_in_deploy_guard.py` never opens a transport because
> its guard aborts during prepare. The pytest suite never touches the network
> either.

## Documentation

```bash
uv sync --group docs
uv run --group docs mkdocs serve   # http://localhost:8000
```

- [Home](docs/index.md) — what the model is and why generators
- [Guide](docs/guide.md) — writing deploy scripts, facts, operations, commands
- [Examples](docs/examples.md) — runnable scripts, one page per directory under
  `examples/`
- API reference — one page per source module, generated from docstrings:
  [core](docs/core.md), [drivers](docs/drivers.md), [commands](docs/commands.md),
  [hosts](docs/hosts.md), [exceptions](docs/exceptions.md),
  [actions](docs/actions/actions.md),
  [connectors/ssh](docs/connectors/ssh.md),
  [connectors/docker](docs/connectors/docker.md), and one page per namespace
  under [modules/](docs/modules/)

## Layout

```text
src/reemote5/core.py         OperationRequest, OpContext, FactCache, HostRuntime, op()
src/reemote5/commands.py     command descriptors yielded by operations
src/reemote5/drivers.py      run_prepare / run_apply / send_command / run
src/reemote5/actions.py      Shell + WaitForConnection actions (outside deploys)
src/reemote5/hosts.py        Host (inventory entry, owns the asyncssh transport)
src/reemote5/connectors/     alternative transports, e.g. docker.py (local
                             Docker/Podman containers via the CLI)
src/reemote5/modules/        self-contained namespaces, facts + operations together:
                             apk, apt, dnf, files, firewalld, git, gpg, hardware,
                             iptables, lxd, mysql, openrc, postgresql, selinux,
                             server, sysctl, systemd, ufw
examples/                    runnable scripts, mirroring the module/action layout
tests/simulated_host.py      SimulatedHost (test framework, not shipped)
tests/test_core.py           model tests, incl. the httpd trace and determinism
tests/modules/<ns>/          per-namespace fact and operation tests
tests/test_examples.py       offline scan plus deploy drain of every example
```

Requires Python 3.13+.

[pyinfra]: https://pyinfra.com/
[asyncssh]: https://asyncssh.readthedocs.io/
