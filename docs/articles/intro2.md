# Reemote: a pure-Python configuration-management library

Python has had no configuration-management library — until now. Pulumi is an infrastructure-management library: it can provision servers and cloud instances, but the moment you want to configure those servers you have to switch to another language, usually Ansible. Reemote is a pure-Python configuration-management library. You describe the state of your servers from a Python program.

## Try it in under a minute (docker connector)

Reemote ships a **docker connector** that runs your deploy inside a local container, so you need neither a remote host nor an SSH key to see it work. **Docker or Podman must be installed locally** — the connector shells out to whichever CLI you name in the inventory, and there is no default. Two steps:

```bash
# 1. Install (Python 3.13+; pip works just as well as uv)
uv venv .venv && source .venv/bin/activate && uv pip install reemote

# 2. Save the script below as hello.py, then run it
python hello.py
```

```python
# hello.py
import asyncio

from reemote import files, format_log, format_plan, op, run

INVENTORY = [{
    "host": "demo",
    "family": "Alpine",
    "connection": {
        "connector": "docker",
        "identifier": "alpine:3.20",
        "docker_cmd": "podman",   # or "docker"
    },
}]


def deploy(host):
    yield op(files.install,
             name="Write a greeting",
             content="Hello from reemote!\n",
             dest="/tmp/reemote-hello.txt",
             mode="0644")


async def main():
    # Dry run: facts are gathered for real, nothing is mutated.
    plans, _, _ = await run(INVENTORY, deploy, dry=True)
    print("== Dry run: what would change ==")
    print(format_plan(plans))

    # Apply: the same deploy, executed against a fresh container.
    _, report, _ = await run(INVENTORY, deploy)
    print("\n== Applied: what changed ==")
    assert report is not None
    print(format_log(report))


asyncio.run(main())
```

Reemote starts a throwaway `alpine:3.20` container, previews the change without touching it, then runs the same deploy for real against a second container, prints the resulting command log, and finally commits each container into a new image and removes it. Verified output:

```text
== Dry run: what would change ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]

== Applied: what changed ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]
    $ scp <files install> -> /tmp/reemote-install-2e52d4f8373a608a/content
    $ sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-2e52d4f8373a608a/content /tmp/reemote-hello.txt && chmod 644 /tmp/reemote-hello.txt && rm -rf /tmp/reemote-install-2e52d4f8373a608a'
```

As in Ansible, execution has two phases. The dry run reports the changes that would be made; the apply carries them out and records the exact commands that were sent. The two calls differ only in `dry=True`, and in dry mode `report` is `None` because nothing was mutated. Each `run(...)` starts its own throwaway container from `alpine:3.20`, so both phases observe the same absent file — the point of the demo is the two-phase pattern, not idempotence.

The inventory is a plain Python list of host dictionaries carrying connection information. Reemote is agentless: for SSH targets the connection block is passed to [asyncssh](https://asyncssh.readthedocs.io/).

A deploy is a generator that yields operation requests in order — here, a single `files.install`. In general these operations map closely to their Ansible counterparts, so the vocabulary will be familiar.

The following deploy installs and starts nginx:

```python
def deploy(host):
    yield op(apt.packages, name="Install nginx",
             packages=("nginx",), sudo=True)
    yield op(files.install, name="Write landing page",
             content=PAGE, dest="/var/www/html/index.html",
             mode="0644", sudo=True)
    yield op(systemd.service, name="Run nginx",
             service="nginx", running=True, enabled=True, sudo=True)
```

The program is asynchronous. Operations are dispatched to every host in the inventory concurrently, and the controller waits for every host to finish an operation before advancing to the next.

`run(...)` returns the plans and the report as structured objects your program can inspect, giving you programmatic control over the entire configuration process.

Reemote is a Python library for automation. It has no CLI, and there is no DSL to learn. The library is thoroughly documented, and the repository contains over 400 usage examples.

Reemote is inspired by [Pyinfra](https://pyinfra.com/), an excellent CLI configuration-management tool maintained by Nick Mills-Barrett, which we recommend without reservation. Commercially developed, Reemote is now open source under the EPL-2.0 license. I am the maintainer of the open-source library.

If you have ever wanted to configure servers in Python — the same language as the rest of your stack — Reemote is worth a look.
