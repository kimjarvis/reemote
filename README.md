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
`SimulatedHost` stand-ins provided by the *test framework*
(`tests/simulated_host.py`), never by the library itself. Both the
command-execution path (`send_command`,
the apply drivers) and fact gathering (each fact's `process`, `FactCache.get`,
the operations that await it, and `run_prepare`) are `async`, preparing for a
real [asyncssh]-based transport (where pyinfra uses Paramiko + gevent).

```python
import asyncio
from collections.abc import Generator

from reemote5 import Host, OperationRequest, dnf, files, format_log, op, run

# Test-framework stand-in, lives in tests/simulated_host.py: injected via
# host_factory so this runs without a real SSH transport.
from tests.simulated_host import SimulatedHost


def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(dnf.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        src="/etc/nginx/sites-enabled/default",
        present=False,
    )


async def main() -> None:
    inventory = [{"host": "web1", "connection": {}}]
    plans, report, hosts = await run(
        inventory, deploy, host_factory=SimulatedHost.from_inventory
    )
    print(format_log(report))


asyncio.run(main())
```

## Getting started

```bash
uv sync                          # library + pytest
uv run python examples/dnf_install_httpd.py
uv run pytest
```

## Documentation

```bash
uv sync --group docs
uv run --group docs mkdocs serve   # http://localhost:8000
```

- [Home](docs/index.md) — what the model is and why generators
- [Guide](docs/guide.md) — writing deploy scripts, facts, operations, commands
- API reference — one page per source module, generated from docstrings:
  [core](docs/core.md), [drivers](docs/drivers.md), [commands](docs/commands.md),
  [hosts](docs/hosts.md),
  [facts/dnf](docs/facts/dnf.md), [facts/files](docs/facts/files.md),
  [operations/dnf](docs/operations/dnf.md), [operations/files](docs/operations/files.md)

## Layout

```text
src/reemote5/core.py         OperationRequest, OpContext, FactCache, HostRuntime, op()
src/reemote5/commands.py     command descriptors yielded by operations
src/reemote5/drivers.py      run_prepare / run_apply / send_command / run
src/reemote5/facts/          DnfPackages, FileLink, FileExists (namespace modules)
src/reemote5/hosts.py        Host (materialized from inventory entries)
src/reemote5/operations/     dnf.packages, files.link, files.file
examples/dnf_install_httpd.py end-to-end usage example
tests/simulated_host.py      SimulatedHost (test framework, not shipped)
tests/test_model.py          model tests
```

Requires Python 3.13+.

[pyinfra]: https://pyinfra.com/
[asyncssh]: https://asyncssh.readthedocs.io/
