# reemote5

**reemote5** is a python library for configuration management.
It provides cross-host lockstep ordering and dry-run reporting. 

<!---

**reemote5** is a proof-of-concept execution model for configuration
management. It explores an alternative to [pyinfra]'s two-phase
*prepare → execute* design: **deploy scripts are Python generators** that
yield operation descriptors instead of calling operations directly.

That single change lets the driver pause a deploy script at every operation
boundary, which in turn makes sequential *"act, then read"* logic produce the
correct answer in both phases — while keeping the properties that the
two-phase split exists to provide: cross-host lockstep ordering and dry-run
reporting.



!!! warning "POC scope"

    reemote5 is a **library**, not a deployment tool. It is deliberately
    restricted to *generating commands*:

    - no SSH or any other connection transport,
    - no CLI, inventory files or plugin system — hosts are specified as a
      simple *inventory* (a list of dictionaries) passed to
      [`run`][reemote5.drivers.run],
    - without an injected transport, hosts are in-memory `SimulatedHost`
      objects — part of the test framework (`tests/simulated_host.py`),
      never of the library.

    Nothing here connects to a real machine. The point of the project is the
    execution model, demonstrated end to end and covered by tests.

    The command-execution path *is* asynchronous:
    [`send_command`][reemote5.drivers.send_command] — and therefore
    [`run_apply`][reemote5.drivers.run_apply] and
    [`run`][reemote5.drivers.run] — are coroutines, preparing for a real
    [asyncssh]-based transport (pyinfra uses Paramiko + gevent instead).
    Fact gathering is async for the same reason: a fact's `process`,
    [`FactCache.get`][reemote5.core.FactCache.get], the operations that await
    it, and [`run_prepare`][reemote5.drivers.run_prepare] are all coroutines,
    so the whole prepare/apply path is awaited end to end.

## Why generators?

A deploy is naturally written as a sequence of steps where later steps depend
on the effect of earlier ones. Consider "install nginx, then remove its
default site": the site link only exists *after* the package is installed.

In a two-phase model the prepare phase runs before anything is executed, so a
naive prepare phase cannot see that link:

```text
prepare: install nginx → changed        (nginx not installed yet)
         remove link   → no change      (link does not exist yet — wrong!)
apply:   install nginx → changed        (link now created as a side effect)
         remove link   → ???            (script already decided "no change")
```

pyinfra solves this with its own machinery. reemote5 solves it with the
generator itself:

1. The deploy body is a generator, so the driver controls *when* the next
   step is computed.
2. In apply, the driver recreates the
   [`FactCache`][reemote5.core.FactCache] **before** pulling each request, and
   uses that cache to materialize it.
3. Fact reads made by an operation therefore observe everything the previous
   operations did — sequential semantics, no phase flag required.

The prepare phase still yields a full plan, and the apply phase still runs
every host in lockstep. Neither facts nor operations know which phase they are
in; only the drivers do.

--->

## Installation

The project is managed with [uv] and targets Python 3.13+:

```bash
uv sync                    # library + dev (pytest)
uv sync --group docs       # + mkdocs-material and mkdocstrings
```

## Quick start

A deploy script is a generator with a fixed signature, yielding requests built
with the [`op`][reemote5.core.op] helper:

```python
from collections.abc import Generator

from reemote5 import Host, OperationRequest, dnf, files, op, run

SITE = "/etc/nginx/sites-enabled/default"
TARGET = "/etc/nginx/sites-available/default"


def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(dnf.packages, name="Install nginx", packages=("nginx",))
    yield op(files.link, name="Remove default site", src=SITE, present=False)
```

Hosts are specified as an *inventory* — a list of dictionaries, each carrying
at least a host's name (`host`) and SSH `connection` information — and passed
to [`run`][reemote5.drivers.run], which validates
it (every entry must have `host` and `connection`; a malformed inventory raises
[`InventoryError`][reemote5.hosts.InventoryError]) and then prepares and applies
in one call. `run` is a coroutine, so it must be awaited (here from an
`asyncio.run` entry point):

```python
import asyncio

from reemote5 import format_log, format_plan, run

# Test-framework stand-in, lives in tests/simulated_host.py — not part of
# the library API.
from tests.simulated_host import SimulatedHost

inventory = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]


def simulated_factory(entry: dict) -> SimulatedHost:
    # Test-framework stand-in, injected via host_factory so this runs
    # without SSH: web1 is fresh (installing nginx creates the
    # default-site link); web2 already has nginx and the link.
    host = SimulatedHost.from_inventory(entry)
    if host.name == "web1":
        host.installs_links = {"nginx": {SITE: TARGET}}
    else:
        host.packages = {"nginx"}
        host.links = {SITE: TARGET}
    return host


async def main() -> None:
    plans, report, hosts = await run(
        inventory, deploy, host_factory=simulated_factory
    )
    print(format_plan(plans))
    print(format_log(report))


asyncio.run(main())
```

Pass `dry=True` to stop after the prepare phase — nothing is sent to the hosts
and `report` is `None`.

A complete, runnable version of this example — including the final host state
— lives in
[`examples/dnf_install_nginx.py`](https://github.com/kimjarvis/reemote5/blob/main/examples/dnf_install_nginx.py):

```bash
uv run python examples/dnf_install_nginx.py
```

```text
== Plan (prepare phase, --dry) ==
web1: Install nginx            [changed: 1 package(s) to install]
web1: Remove default site      [no change: link already absent]
web2: Install nginx            [no change: all packages present]
web2: Remove default site      [changed: link present, will remove]

== Run log (apply phase) ==
web1: Install nginx            [changed: 1 package(s) to install]
    $ dnf install -y nginx
web2: Install nginx            [no change: all packages present]
web1: Remove default site      [changed: link present, will remove]
    $ rm -f /etc/nginx/sites-enabled/default
web2: Remove default site      [changed: link present, will remove]
    $ rm -f /etc/nginx/sites-enabled/default

== Final host state ==
web1: packages=['nginx'] links={}
web2: packages=['nginx'] links={}
```

The interesting line is `web1: Remove default site`. The plan says *no change*
because nginx is not installed yet during prepare; the apply log says *changed*
because installing nginx created the link and the fresh fact cache saw it. Same
deploy script, same request sequence, correct outcome in both phases.


<!---

## API reference

Everything documented in the reference is re-exported from the top-level
package, so a deploy script only ever needs one import line:

```python
from reemote5 import Host, OperationRequest, dnf, files, op, run
```

The API has six layers, each documented on a page named after the source
module that defines it:

| Layer | Objects | Reference page |
| --- | --- | --- |
| **Drivers** | [`run`][reemote5.drivers.run], [`run_prepare`][reemote5.drivers.run_prepare], [`run_apply`][reemote5.drivers.run_apply], [`send_command`][reemote5.drivers.send_command], [`format_plan`][reemote5.drivers.format_plan], [`format_log`][reemote5.drivers.format_log] | [drivers](drivers.md) |
| **Core objects** | [`OperationRequest`][reemote5.core.OperationRequest], [`op`][reemote5.core.op], [`OpContext`][reemote5.core.OpContext], [`FactCache`][reemote5.core.FactCache], [`HostRuntime`][reemote5.core.HostRuntime], [`OperationError`][reemote5.core.OperationError] | [core](core.md) |
| **Commands** | [`Command`][reemote5.commands.Command], [`StringCommand`][reemote5.commands.StringCommand], [`TransferCommand`][reemote5.commands.TransferCommand], [`WaitCommand`][reemote5.commands.WaitCommand], [`WaitTimeoutError`][reemote5.commands.WaitTimeoutError] | [commands](commands.md) |
| **Hosts, facts, operations** | [`Host`][reemote5.hosts.Host], [`validate_inventory`][reemote5.hosts.validate_inventory], [`InventoryError`][reemote5.hosts.InventoryError], [`ApkAutoremovablePackages`][reemote5.modules.apk.apk.ApkAutoremovablePackages], [`ApkExplicitPackages`][reemote5.modules.apk.apk.ApkExplicitPackages], [`ApkHeldPackages`][reemote5.modules.apk.apk.ApkHeldPackages], [`ApkInstalledPackages`][reemote5.modules.apk.apk.ApkInstalledPackages], [`ApkRepositories`][reemote5.modules.apk.apk.ApkRepositories], [`AptKeys`][reemote5.modules.apt.apt.AptKeys], [`DnfPackages`][reemote5.modules.dnf.dnf.DnfPackages], [`FileLink`][reemote5.modules.files.files.FileLink], [`RemoteFileSha1`][reemote5.modules.files.files.RemoteFileSha1], [`FileContents`][reemote5.modules.files.files.FileContents], [`BlockDevices`][reemote5.modules.hardware.hardware.BlockDevices], [`GitBranch`][reemote5.modules.git.git.GitBranch], [`SystemdServiceState`][reemote5.modules.server.server.SystemdServiceState], [`apk.autoremove`][reemote5.modules.apk.apk.autoremove], [`apk.hold`][reemote5.modules.apk.apk.hold], [`apk.packages`][reemote5.modules.apk.apk.packages], [`apk.virtual`][reemote5.modules.apk.apk.virtual], [`apt.deb`][reemote5.modules.apt.apt.deb], [`apt.dist_upgrade`][reemote5.modules.apt.apt.dist_upgrade], [`apt.auto_remove`][reemote5.modules.apt.apt.auto_remove], [`apt.key`][reemote5.modules.apt.apt.key], [`apt.mark`][reemote5.modules.apt.apt.mark], [`apt.packages`][reemote5.modules.apt.apt.packages], [`apt.ppa`][reemote5.modules.apt.apt.ppa], [`apt.repo`][reemote5.modules.apt.apt.repo], [`apt.sources_file`][reemote5.modules.apt.apt.sources_file], [`apt.update`][reemote5.modules.apt.apt.update], [`apt.upgrade`][reemote5.modules.apt.apt.upgrade], [`dnf.packages`][reemote5.modules.dnf.dnf.packages], [`dnf.update`][reemote5.modules.dnf.dnf.update], [`dnf.repo`][reemote5.modules.dnf.dnf.repo], [`dnf.rpm`][reemote5.modules.dnf.dnf.rpm], [`dnf.key`][reemote5.modules.dnf.dnf.key], [`dnf.module`][reemote5.modules.dnf.dnf.module], [`files.link`][reemote5.modules.files.files.link], [`files.put`][reemote5.modules.files.files.put], [`files.get`][reemote5.modules.files.files.get], [`files.edit`][reemote5.modules.files.files.edit], [`files.move`][reemote5.modules.files.files.move], [`files.copy`][reemote5.modules.files.files.copy], [`git.config`][reemote5.modules.git.git.config], [`git.repo`][reemote5.modules.git.git.repo], [`git.worktree`][reemote5.modules.git.git.worktree], [`server.group`][reemote5.modules.server.server.group], [`server.hostname`][reemote5.modules.server.server.hostname], [`server.kill`][reemote5.modules.server.server.kill], [`server.modprobe`][reemote5.modules.server.server.modprobe], [`server.mount`][reemote5.modules.server.server.mount], [`server.service`][reemote5.modules.server.server.service], [`server.timezone`][reemote5.modules.server.server.timezone], [`server.wait_service`][reemote5.modules.server.server.wait_service] | [hosts](hosts.md), [modules/apk](modules/apk.md), [modules/apt](modules/apt.md), [modules/dnf](modules/dnf.md), [modules/files](modules/files.md), [modules/git](modules/git.md), [modules/hardware](modules/hardware.md), [modules/server](modules/server.md) |
| **Connectors** | [`DockerHost`][reemote5.connectors.docker.DockerHost], [`validate_docker_connection`][reemote5.connectors.docker.validate_docker_connection] | [connectors/docker](connectors/docker.md) |
| **Actions** | [`Shell`][reemote5.actions.Shell], [`WaitForConnection`][reemote5.actions.WaitForConnection], [`Result`][reemote5.actions.Result], [`gather_action`][reemote5.actions.gather_action], [`ActionInDeployError`][reemote5.actions.ActionInDeployError], [`ConnectionTimeoutError`][reemote5.actions.ConnectionTimeoutError] | [actions](actions.md) |

!!! note "Design-doc references"

    Several docstrings cite *design doc section N*. Those are section numbers
    from the original design notes for this POC; they are informational only
    and are not published here.

## Project layout

```text
src/reemote5/
├── core.py            # OperationRequest, OpContext, FactCache, HostRuntime, OperationError, op()
├── commands.py        # Command descriptors (Command, StringCommand, TransferCommand, WaitCommand)
├── drivers.py         # run_prepare, run_apply, send_command, run, formatters
├── actions.py         # Shell + WaitForConnection actions, Result, gather_action (immediate, outside deploys)
├── hosts.py           # Host (materialized from inventory entries)
├── connectors/        # alternative transports selected by connection.connector
│   └── docker.py      #   DockerHost (local Docker/Podman containers via the CLI)
└── modules/           # self-contained namespaces (facts + operations together)
    ├── apk/
    │   └── apk.py     #   ApkAutoremovablePackages (apk add --purge --simulate), ApkInstalledPackages (apk list --installed), ApkExplicitPackages (/etc/apk/world), ApkHeldPackages (/etc/apk/world version pins), ApkRepositories (/etc/apk/repositories), apk.autoremove (orphan purge) + apk.packages + apk.virtual (virtual package groups) + apk.hold (version pins) + their command descriptors
    ├── apt/
    │   └── apt.py     #   AptKeys (gpg keyrings), AptPackages (apt list --installed), AptSources (.list + deb822 .sources repositories), AptHeldPackages/AptPolicy, AptUpgradable (apt list --upgradable), AptAutoremovable (autoremove simulation), DebPackage (dpkg -I/-s), SimulateOperationWillChange (apt-get --dry-run counts), apt.deb/apt.dist_upgrade/apt.auto_remove/apt.key/apt.mark/apt.packages/apt.ppa/apt.repo/apt.sources_file/apt.update/apt.upgrade + their command descriptors
    ├── dnf/
    │   └── dnf.py     #   DnfPackages…, dnf.packages + its command descriptors
    ├── files/
    │   └── files.py   #   File/FileLink…, LocalSha1/RemoteFileSha1/FileContents, files.link/file/put/get/edit/move/copy + their command descriptors
    ├── git/
    │   └── git.py     #   GitBranch (git describe --all), GitConfig (git config -l), GitLocalCommit (git rev-parse), GitRemoteBranchCommit (git ls-remote), GitTag (git tag), GitTrackingBranch (git status --branch --porcelain), git.config (git config --global/--system/--local) + git.repo (git clone/fetch/checkout/pull/submodule) + git.worktree (git worktree add/remove/pull) + their command descriptors
    ├── hardware/
    │   └── hardware.py  # BlockDevices (df), CpuInfo (lscpu), Cpus (getconf), DiskStats (/proc/diskstats), DmiInfo (/sys/class/dmi/id), Ipv4Addrs/Ipv6Addrs/NetworkDevices (ip), LsBlk (lsblk), SmbiosInfo (dmidecode)
    └── server/
        └── server.py  #   SystemdServiceState/Enabled, server.group/hostname/kill/modprobe/mount/service/timezone/wait_service + their command descriptors

examples/                     # runnable usage examples (not part of the library)
tests/simulated_host.py     # SimulatedHost (test framework, not part of the library)
tests/test_core.py          # model tests, incl. the httpd trace and determinism
docs/                       # this documentation
```

## Development

```bash
uv run pytest                        # run the tests
uv run --group docs mkdocs serve     # live-reloading docs on localhost:8000
uv run --group docs mkdocs build     # build the static site into site/
```

--->

[pyinfra]: https://pyinfra.com/
[uv]: https://docs.astral.sh/uv/
[asyncssh]: https://asyncssh.readthedocs.io/
