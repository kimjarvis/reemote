# Configure servers with Python

**reemote is a Python library for configuring and deploying servers.** Describe the packages, files and services you want using Python functions; reemote queries each server, plans the work and applies the changes. Your deployment is a Python program that you can import, test, compose and run from your existing automation.

This introduction takes about **10 minutes**. You will create an inventory, preview a small change, apply it and run it again to see that no further change is needed. Use a Linux test server you are allowed to modify; no package installation or administrator privileges are needed for the first deployment.

## Why a Python API?

Ansible is a common choice for server configuration. If you prefer to express that work in Python, reemote keeps the configuration logic in the language you already use: functions instead of a separate task language, ordinary imports instead of a special role loader, and Python data structures instead of expressions embedded in configuration files.

That is especially useful when deployment is part of an application. You can obtain an inventory from your own service, generate configuration with a Python library, share validation code with your application and await a deployment from an existing async workflow. Your editor can navigate the code, your tests can inspect the requested operations, and your application can consume structured results rather than scrape terminal output.

You still need to understand the servers you manage. Python does not make package names portable or remove the need for SSH permissions. reemote supplies a configuration API, not an automatic translation between operating systems.

## Four words to know

- An **inventory** identifies the servers and how to connect to them.
- A **fact** reads information: a hostname, installed packages or a file's contents.
- An **operation** describes a requested change, such as ensuring a file contains some text. Operations use facts to decide what work is needed.
- An **action** runs immediately, outside a deployment, and returns its result. Use actions for one-off commands or checks that do not have a suitable operation.

A **deploy** is a Python generator that yields operation requests in order. You do not need to implement facts or operations to use the library.

## 1. Install on your controller

The *controller* is the machine running your Python script. It needs **Python 3.13 or newer**. The following source installation uses Git and Python's virtual environments; run it in a directory where you want to keep the project:

```bash
git clone https://github.com/kimjarvis/reemote5.git
cd reemote
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e . asyncssh
```

If you already have a checkout, start with the virtual-environment commands inside it. `asyncssh` is explicitly included because the current package metadata does not declare the dependency required by the SSH transport. For reproducible automation, pin the project revision and your environment's dependencies.

reemote is a library: there is no `reemote` command to invoke. You will run your own Python script. The Python deployment program runs on the controller; you do not install reemote on the target. This example needs an SSH server, a working login, ordinary Linux shell utilities and SCP support on the target.

Before continuing, check that you can SSH into your test server with the account and key you intend to use. Verify its host-key fingerprint through a trusted source and add the verified key to your known-hosts file. Keep host-key checking enabled.

## 2. Create an inventory

Create `inventory.json` beside the script you will write next:

```json
[
  {
    "host": "demo",
    "connection": {
      "address": "192.0.2.10",
      "username": "deploy",
      "key_path": "~/.ssh/id_ed25519"
    }
  }
]
```

Replace `192.0.2.10` with your server's address, `deploy` with your login account, and the key path if necessary. The address shown is a documentation placeholder, not a public test server.

`host` is a label used in results; `connection.address` is the actual SSH destination. They need not be the same. Use a different label for every server. JSON is just a convenient way for this tutorial to load a Python list of dictionaries; reemote does not load the file itself.

That is enough inventory for a first deployment. Groups, tags, passwords, ports and other options can wait until you need them.

## 3. Write a deployment

Create `hello.py` next to `inventory.json`:

```python
import argparse
import asyncio
import json
from collections.abc import Generator
from pathlib import Path

from reemote import Host, OperationRequest, files, format_log, format_plan, op, run


def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(
        files.install,
        name="Write a greeting",
        content="Hello from reemote!\n",
        dest="/tmp/reemote-hello.txt",
        mode="0644",
    )


async def main(apply: bool) -> None:
    path = Path(__file__).with_name("inventory.json")
    inventory = json.loads(path.read_text(encoding="utf-8"))
    plans, report, _ = await run(inventory, deploy, dry=not apply)
    print("Plan:")
    print(format_plan(plans))
    if report is not None:
        print("Applied:")
        print(format_log(report))
    else:
        print("Preview only. Pass --apply to make changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
```

The deployment has one request: make a text file contain a greeting. It writes only the named demonstration file and an intermediate upload under `/tmp`; choose another filename if that path is already used. There is no `sudo=True`, so the commands run as your SSH login user. The final file is readable by other users: do not put secrets in this example.

The important line is `yield op(...)`. `op` builds a description of the operation; `yield` hands that description to the runner. It does not execute the operation immediately. The runner supplies the execution context, reads the current remote state and sends the necessary commands.

`files.install` accepts text directly. The remote destination is a path, but the operation does not search the controller for a file to upload. Later, you can generate that text with ordinary Python or read it in your application before calling `run`.

## 4. Preview, apply, repeat

Start with a preview:

```bash
python hello.py
```

For an absent destination, the plan includes:

```text
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]
```

Here, **changed in a plan means a change is proposed**, not that it happened. `dry=True` stops after planning. It still connects to the server and runs fact queries, so a preview needs working credentials and remote tools. It does not send the operation's upload or install commands.

Apply the deployment explicitly:

```bash
python hello.py --apply
```

The script now prints both the plan and the apply log. The log shows the transfer and shell command that installed the text. You can verify the result using your own server details:

```bash
ssh -i ~/.ssh/id_ed25519 deploy@192.0.2.10 'cat /tmp/reemote-hello.txt'
```

It should print `Hello from reemote!`. Now run the apply command a second time. With the contents unchanged, both the plan and log report:

```text
demo: Write a greeting         [no change: content already up to date (/tmp/reemote-hello.txt)]
```

This is **idempotence**: asking for the same state again does not rewrite an already-correct file. Change the greeting in `hello.py` and run it again to see a proposed update. Do not assume every operation is idempotent; an explicit restart, for example, deliberately performs work again.

The script's `--apply` switch is ordinary `argparse` code, not a library-wide CLI convention. Without that switch, this particular script always previews. Other scripts can choose different policies.

## What just happened?

`run(inventory, deploy, ...)` validates the inventory and runs two phases:

1. **Prepare:** build the requests and query the current server state to produce a plan.
2. **Apply:** build the requests again, recheck the state before each operation and execute the work that is then needed. A dry run omits this phase.

A plan is a preview against current state, not a simulation of every consequence of earlier operations. For example, installing a package can create a file that a later operation should remove. That removal might appear unnecessary during prepare but become necessary during apply. The requests remain the same; their change decisions can differ.

The return value is `(plans, report, hosts)`. `plans` contains the per-host preview; `report` contains the apply log, or is `None` for a dry run. The host objects represent your targets, not a complete snapshot of their files and services. `run` closes the connections it used when it finishes.

Deploy functions are deliberately **pure descriptions of work**: the same fixed inputs should produce the same requests. Keep file reads, API calls, time-dependent decisions and immediate actions outside them. The guide explains how to combine those activities with deployments safely.

The runner uses **asyncio** to overlap network work across servers. Within apply, hosts progress through operation positions together, while commands for one host execute in order. Your deploy stays a simple synchronous generator; `asyncio.run` drives the asynchronous runner from a normal script.

## Grow the example

To target another server, add a second inventory entry with a unique `host` label. The same deploy is used for both. Start with a small test inventory, since an apply affects every eligible target you supply.

To remove the greeting when you are finished, replace the request in `deploy` with:

```python
    yield op(
        files.file,
        name="Remove the greeting",
        path="/tmp/reemote-hello.txt",
        present=False,
    )
```

Preview and apply as before. This removes only the named regular file. For packages and services, choose operations matching the target: `apt.packages` for an APT system, `dnf.packages` for a DNF system, and `systemd.service` for systemd. The [guide's server example](guide.md#configure-a-small-web-server) combines packages, files and a service with the required privileges.

## Where to go next

- Read the [Guide](guide.md) for a roughly one-hour progression through inventories, useful deployments, facts, actions, composition and the execution model.
- Use **API Reference → Modules** to find facts and operations by subject. Start with [files](modules/files.md), [apt](modules/apt.md), [dnf](modules/dnf.md), [server](modules/server.md) or [systemd](modules/systemd.md). Class-style names such as `FileContents` are facts; lowercase functions such as `install` are operations. Entry types describe returned data, not additional commands to run.
- Use the [Actions reference](actions/actions.md) for immediate commands, and the [SSH](connectors/ssh.md) or [Docker/Podman](connectors/docker.md) pages for connection settings.

The guide also explains [how to read the reference and find examples](guide.md#finding-facts-operations-and-examples), so you can extend your deployment without reading the whole API first.
