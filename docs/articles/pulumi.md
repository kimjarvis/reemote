# After `pulumi up`: configuring Pulumi's servers with reemote

From Python I call Pulumi to provision my servers, and then I run an Ansible playbook to configure them. Why? Why is that the shape of it? The provisioning is real code — typed, reviewable, testable, importable — and then, the moment a machine exists and I need something done *inside* it, I have to leave Python and write another language, with another mindset, in another file, run by another tool that needs to be told about the servers the first tool just created.

Why can't I configure the servers in the same Python program? And I do not mean "call Python from the playbook", or "run the playbook from a post-create hook", or shell out to a CLI and scrape its stdout. I mean *in the same program*: after the instance I just declared, using the address it just handed back, one `await` deep. I have the hostname right there. I have the key right there in Python. Everything I need to know about that machine is at hand, but it all has to be re-encoded for Ansible. It's an impedance mismatch, like SQL to objects. The provision-configuration impedance mismatch.

Why do I have to use Ansible at all? I do not want a controller, an inventory format of its own, a callback plugin API, or a DSL for saying what should be true. I want to `import` a package, hand it a host, and get back what changed. So why has nobody written a configuration management *library* in Python — one you `pip install` and call from your own process, the way you would call anything else? How hard can it be? Somebody has to run `apt list --installed`, compare it against what was asked for, and install the package only when the two disagree.

It turns out that is most of it, and that it fits in a library. The mismatch is a habit, not a constraint.

*Pulumi's job ends when the stack is applied: the instance exists, the network is wired, the DNS has propagated. The server is still empty. Reemote is a Python library that takes over at exactly that point — it installs the packages, writes the files, and runs the services inside the machines Pulumi built. This article is about that handoff: what reemote is, and how it picks up where `pulumi up` leaves off — quite possibly in the same program.*

## The handoff

Start with the part that ought to have been obvious years ago. The handoff does not have to be a boundary between two programs, because there is only one language on either side of it: Pulumi's Python SDK and reemote are both ordinary Python running on an `asyncio` event loop, so the code that builds a server and the code that configures it can sit in one module, in one process — the instance declared with a provider package, the deploy that fills it in just another coroutine on the same loop.

What crosses from one half to the other is a reachable address — plus one thing to get right about a machine that has only just been created: it does not answer SSH the moment the provider API says it exists.

```python
# __main__.py — one program: Pulumi builds the server, reemote configures it
import pulumi
from pulumi_aws import ec2
from reemote import (
    Host, WaitForConnection, apt, files, gather_action, op, run, systemd,
)

web = ec2.Instance("web", instance_type="t3.small", ami="ami-0c3d3ab1d1a0f4dc0")
pulumi.export("webHost", web.public_dns)

PAGE = "<html><body><h1>Deployed by reemote</h1></body></html>\n"

def deploy(host):
    yield op(apt.packages, name="Install nginx", packages=("nginx",), sudo=True)
    yield op(files.install, name="Write landing page",
             content=PAGE, dest="/var/www/html/index.html", mode="0644", sudo=True)
    yield op(systemd.service, name="Run nginx",
             service="nginx", running=True, enabled=True, sudo=True)

async def configure(address):
    inventory = [{"host": "web", "group": "web",
                  "connection": {"connector": "ssh",
                                 "address": address,
                                 "username": "deploy"}}]
    # Cloud-init has to finish and sshd has to start before any of that works.
    hosts = [Host.from_inventory(entry) for entry in inventory]
    await gather_action(hosts, WaitForConnection, timeout=600, retry=10)
    return await run(inventory, deploy)

# web.public_dns is a pulumi.Output: apply passes its plain value once the
# instance exists, and Output.from_input schedules the coroutine on the loop
# Pulumi already runs — asyncio.run would fail here, because a Pulumi program
# owns the event loop.
web.public_dns.apply(
    lambda address: pulumi.Output.from_input(configure(address)),
)
```

Note the ordering inside `configure`, because it is the part people get wrong on a freshly provisioned host: the wait comes before the deploy, not inside it. Actions run immediately and sit outside the two-phase model, so `gather_action` raises if you call it from a deploy generator; between the stack and the deploy is exactly where it belongs. It also closes the connections of the hosts it was handed, which is why the wait takes `Host` objects and the deploy takes the inventory — `run` materialises and tears down its own fresh hosts from the entries it is given.

The same program can of course be split in two, and often should be: a platform team that runs `pulumi up` in one pipeline and hands the stack to an application team will keep the deploy as its own script, run with `python`, reading the address out of the stack. And the direction can be reversed: the Automation API — the `pulumi.automation` namespace of the Pulumi SDK — wraps `pulumi up` and friends as a typed SDK so a Pulumi program can run *without the CLI*, driven from your own application, "your Python code controls Pulumi, rather than Pulumi controlling your code". A single script can then apply the stack, take the outputs back as plain data, and go straight into `run(...)` — the same handoff, with the engine invocation made explicit instead of the resource declaration. Which direction you pick is a question about who owns the pipeline, not about what the tools allow.

One caveat on running both halves in one program: a Pulumi `Output` is only guaranteed to resolve when resources are really being created. Under `pulumi preview` the engine reports what it *would* do and many values stay unknown, so the configuration callback is a no-op there. That is the right behaviour — there is no server to configure yet — but it means the deploy's own preview comes from reemote instead: call `run(inventory, deploy, dry=True)` against a host you already have.

### Where the inventory comes from

The example above also answers the line of the rant I find most irritating: another tool that has to be told about the servers the first tool just created. That step is the inventory, and it is where the mismatch gets rebuilt by habit — a second description of a fleet that already has one.

A reemote inventory is a plain list of dictionaries, each with a `host` name and a `connection` mapping, optionally a `privilege` mapping and the `group` / `tags` classifiers a script branches on. There is no inventory format to learn and no plugin to write between it and a Pulumi stack — the stack's exports are Python data, and building the list is that step of glue work your organisation already does somewhere:

```python
def inventory_from_stack(project: str, stack: str, user: str) -> list[dict]:
    """Turn a Pulumi stack's exports into a reemote inventory."""
    outputs = json.loads(subprocess.check_output(
        ["pulumi", "stack", "output", "--json"],
        cwd=f"{project}/{stack}", text=True,
    ))
    return [
        {"host": name,
         "group": "web",
         "connection": {"connector": "ssh", "address": address, "username": user},
         "privilege": {"sudo_user": "root"}}
        for name, address in outputs["webHosts"].items()
    ]
```

Three things are worth deciding at this seam rather than inheriting by accident.

- **How many hosts you name.** One export per instance, or a map of them, is what lets a fleet grow without the deploy changing. `group` and `tags` then select the fan-out — `gather_action(hosts, WaitForConnection, group="web")` waits for just that group — so the inventory carries the topology once and the scripts stay generic.
- **Where the address points.** A public DNS name is what Pulumi usually exports, but the inventory takes anything SSH-reachable, so a stack that builds a private subnet can export a private IP and the deploy runs from a jump host — `port`, `proxy_command` and `tunnel` are all accepted in the `connection` mapping, since it forwards to asyncssh.
- **What happens to the host key.** A replaced instance answers the same address with a different host key, and a strict check turns that into a failure on the first deploy after a rebuild. The `connection` mapping passes asyncssh's `known_hosts` through, so the choice is explicit: a path to a known-hosts file to check against, or `None` to skip the check for ephemeral hosts. Either is defensible; leaving it undecided is not, because you will find out on the rebuild you did not expect.

Whatever produced the list, `validate_inventory` checks every entry against the schema, and `run` calls it before a single host is materialised — so a stack output with a missing key fails on the control machine rather than as an SSH error halfway through a fleet.

### Waiting for the machine to answer SSH

Timing is the other thing the two-tool habit buries. A machine the API created thirty seconds ago does not answer SSH yet — cloud-init is still running, sshd may not be — and in the old shape of things you discovered that on a Friday afternoon. `WaitForConnection` is the action that closes the gap between "the API created it" and "SSH works". Each attempt is a full SSH handshake and authentication — not a bare TCP probe — retried every `retry` seconds until it succeeds or the host's `timeout` budget runs out; hosts are waited for concurrently, and awaiting the `gather_action` is the barrier that returns only once every selected host has connected. A host that never comes back raises a fatal `ConnectionTimeoutError` naming it, which aborts before the deploy starts rather than half-configuring a fleet.

That makes it useful for more than provisioning. It is the same action that sits between a deploy that changed kernel arguments, a `Shell` action that reboots, and the deploy that finishes the job — and, at Pulumi's layer, between a stack that replaced an instance and the config that has to land on it again.

## What reemote is

Everything so far has been about the seam; this is what sits on this side of it. Reemote is `pip install reemote` — a library with no CLI, no daemon, no agent, and no state file. Nothing installs on the target beyond SSH, and nothing persists between runs on yours. That is the answer to "why has nobody written a configuration management library": there was very little to write, provided nobody insisted on a controller.

A deploy is a generator function that yields operation requests. The factory signature takes the host being deployed to — so the same function can branch on host data, and different hosts can share it. Requests name what they want ("Install nginx") and describe the desired end state; they say nothing about how to get there, because the engine works out whether anything must change at all.

The module surface covers the configuration domain, as described in more detail in the rest of this documentation: package management for Debian, Red Hat and Alpine family systems; file contents, modes, owners, links and archives; services on systemd and OpenRC; users, groups and processes; databases through the MySQL and PostgreSQL modules; firewalls through iptables, firewalld and UFW; plus git, cron, sysctl and friends. Everything reaches the target over SSH — or over the docker connector, which runs the identical deploy inside a local container, which is how the examples in this repository are tested.

## How the engine decides what to run

The closing jab of that opening — *somebody has to run `apt list --installed`, compare it against what was asked for, and install the package only when the two disagree* — was not a dismissal. It is the design.

`run(...)` executes in two phases. It plans — reading **live facts** from each target: what packages are installed, what this file contains, whether this unit is active — and derives whether each request needs any change. Then it applies, repeating the pass under a determinism guard that raises if the two passes disagree. Operations emit shell commands only when a real delta exists, so the second run against the same live server reports no change.

The result is data: per-host plans, and a report whose entries carry `changed`, a `reason`, and the exact commands sent. `dry=True` stops after the plan, giving you a genuine preview against live state — the natural thing to log in CI before letting the apply proceed.

Three properties matter once this sits behind an infrastructure tool:

- **There is no state to keep true.** Reemote stores nothing between runs; the server *is* the state. Whatever Pulumi built — a fresh instance from an AMI, or one that has been running and drifting for a year — the deploy reads what is actually there and converges it.
- **It reaches anything SSH-reachable.** Cloud VMs, bare metal in a rack you own, VMs on a private OpenStack, containers via the docker connector. If your stack hands back a host, reemote can configure it.
- **It composes as a library.** Inventory built from a stack export or a database query, config text generated by the same Pydantic model the application uses, a deploy gated behind a webhook, pure generator functions tested with `pytest`.

## Where the handoff lands

None of this moves the line between the two jobs; it only stops that line from being a border crossing. Reemote starts where the provider API stops. It cannot launch the instance, attach the volume or create the security group — it reaches machines that already exist, and it needs SSH to get in. Its scope is the Linux operating system: BSD, macOS and Windows are out of scope. It carries none of the collaboration primitives of an IaC backend — no stacks, no reviewable state — by design, since it keeps no state of its own.

So the split stays exactly where operations teams put it: Pulumi guarantees the server exists and is networked; reemote guarantees the server is *useful*. What changes is that the second half stops being a different language, a different repo and a different invocation. It is a library call away, in the program you were already writing.

## Try it

- Reemote: `pip install reemote` — docs at <https://reemote.org/>, source at <https://github.com/kimjarvis/reemote>
- Pulumi: <https://www.pulumi.com/docs/iac/guides/basics/how-pulumi-works/> for how stacks, exports and outputs work; <https://www.pulumi.com/docs/iac/languages-sdks/python/python-blocking-async/> for the event-loop rules the combined program above follows; and <https://www.pulumi.com/docs/iac/concepts/automation-api/> to drive the engine from your own Python program
