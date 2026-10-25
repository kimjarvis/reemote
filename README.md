# reemote5

A proof-of-concept execution model for configuration management, and an
alternative to [pyinfra]'s two-phase *prepare → execute* design.

Deploy scripts are **Python generators** that yield `OperationRequest`
descriptors instead of calling operations directly. That lets the driver pause
the script at every operation boundary: the prepare phase produces a full plan
for dry-run reporting, the apply phase runs all hosts in lockstep with a fresh
fact cache per step, so sequential *"act, then read"* logic gives the correct
answer in both phases. Facts and operations stay phase-unaware — only the
drivers know which phase they are in.

**Scope.** reemote5 is a library, not a deployment tool. It is limited to
generating commands: no SSH transport, no CLI, and hosts are in-memory
`SimulatedHost` stand-ins. Both the command-execution path (`send_command`,
the apply drivers) and fact gathering (each fact's `process`, `FactCache.get`,
the operations that await it, and `run_prepare`) are `async`, preparing for a
real [asyncssh]-based transport (where pyinfra uses Paramiko + gevent).

```python
import asyncio
from collections.abc import Generator

from reemote5 import OperationRequest, SimulatedHost, apt, files, format_log, op, run


def deploy(host: SimulatedHost) -> Generator[OperationRequest, None, None]:
    yield op(apt.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        src="/etc/nginx/sites-enabled/default",
        present=False,
    )


async def main() -> None:
    plans, report = await run([SimulatedHost(name="web1")], deploy)
    print(format_log(report))


asyncio.run(main())
```

## Getting started

```bash
uv sync                          # library + pytest
uv run python examples/install_nginx.py
uv run pytest
```

## Documentation

```bash
uv sync --group docs
uv run --group docs mkdocs serve   # http://localhost:8000
```

- [Home](docs/index.md) — what the model is and why generators
- [Guide](docs/guide.md) — writing deploy scripts, facts, operations, commands
- [API Reference](docs/api.md) — every public object, generated from docstrings

## Layout

```text
src/reemote5/core.py         OperationRequest, OpContext, FactCache, HostRuntime, op()
src/reemote5/commands.py     command descriptors yielded by operations
src/reemote5/drivers.py      run_prepare / run_apply / send_command / run
src/reemote5/facts.py        AptPackages, FileLink
src/reemote5/hosts.py        SimulatedHost
src/reemote5/operations/     apt.packages, files.link
examples/install_nginx.py    end-to-end usage example
tests/test_model.py          model tests
```

Requires Python 3.13+.

[pyinfra]: https://pyinfra.com/
[asyncssh]: https://asyncssh.readthedocs.io/
