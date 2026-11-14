# Transpiling Ansible to Python: it's all about the library

*Ask an AI to translate an Ansible role into Python and you get back a pile of `subprocess` and `paramiko` calls that reinvents configuration management — badly, and differently every time. The model is not the bottleneck. The bottleneck is that, until now, there was no Python library shaped like Ansible for the model to translate **into**. Reemote is that library. Once it is the target, which AI you use barely matters.*

## Paste a role, get spaghetti

Try it. Take an ordinary Ansible role — install a package, drop a config file, enable a service, open a firewall port — and ask a capable model to "rewrite this in Python."

You will get something that runs. It will `import subprocess`. It will shell out to `apt-get install -y`, check the return code, then `systemctl enable`, then maybe wrap the lot in a `try/except`. There will be no idempotence, or a hand-rolled version of it that greps `dpkg -l`. There will be no concurrency. The config file gets written with `open(..., "w")` on your laptop and pushed over with `scp` in a way that only works on the machine you tested it on.

Ask again tomorrow, or ask a different model, and you get a *different* pile of spaghetti. Neither is wrong the way a compiler is wrong; both are just ad hoc. Now spend more tokens — a longer prompt, a bigger context window, a reasoning model. The output gets longer and better commented. It does not get more *consistent*, and it does not converge on an architecture. That is the tell, and it is the whole subject of this article.

## AI is a pattern matcher, and it needs a pattern

The "it's just autocomplete" dismissal of large language models is right for the wrong reasons. A model generates by matching patterns it has seen. Translation tasks go well when there is a **target pattern** to match against — a destination idiom that appears often enough in the training data that the model can lean on it instead of inventing one.

Compile C to assembly and the model has millions of examples of what good assembly looks like. Convert a JSON blob to a Python dataclass and the shape is obvious. But "translate an Ansible role to Python" has no canonical target. There is no widely-repeated idiom for *configuration management in Python*, because — as the next section establishes — nobody shipped the library that would have created one. So the model falls back to the nearest patterns it does have: `subprocess`, `os.system`, `paramiko`, Fabric-style SSH one-liners. Every generation re-derives a different arrangement of those primitives, because there is nothing to converge toward.

No amount of token spend fixes a missing pattern. You cannot prompt a model into an idiom that does not exist in the world yet.

## There is no transpiler — and no framework to transpile into

It is worth being precise about the landscape, because "just use AI" sits on top of a real gap.

There is **no official, automated transpiler** that turns a YAML playbook into native Python. That is not for lack of trying; it is structural. Ansible playbooks are declarative orchestrations leaning on a state engine, an SSH/WinRM abstraction, dynamic variable handling, and an idempotence model. Python is procedural. The two do not line up statement-for-statement, so there is nothing to "compile."

What does exist falls into three buckets, and none of them is a target you would want an AI to translate *into*:

- **Programmatic control of Ansible itself** — `ansible-runner`, or Ansible's internal Python API (`ansible.playbook.Play`, `DataLoader`). These let a Python program *launch* playbooks and consume their events. They do not get you off Ansible; they wrap it. The YAML, the state engine, and the CLI-shaped result all remain.
- **General Python automation frameworks** — Fabric, or Pulumi's Python SDK. Fabric is SSH command execution: it hands you `run("apt-get install ...")`, which is exactly the low-level primitive the model was already reaching for, with no facts, no operations, no idempotence. Pulumi is a different paradigm again — cloud resource provisioning, not host configuration management.
- **Raw building blocks** — `paramiko` for SSH, `requests` for APIs, `subprocess` for everything else. This is the layer the model invents on top of when you give it nothing better.

Notice what all three lack: a **configuration-management library** with the same conceptual shape as Ansible — modules, facts, operations, idempotence — that you can `import` and call from Python. That library is the missing pattern. Until it exists, "transpile Ansible to Python" has no destination, and the AI has nothing to aim at.

## The library matters more than the model

Here is the whole argument, stated plainly: **for Ansible-to-Python migration, the choice of Python library dominates the choice of AI.**

Give a model nothing and it invents an architecture every time. Give it a rich, consistent, well-documented library whose vocabulary lines up one-to-one with Ansible's, and the task collapses from "design a configuration-management system" to "map each task to the operation that already does this." That second task is exactly what models are good at, and it is bounded enough that a human can *review* the output.

The best current choice for that library is Reemote — not because it is magic, but because it is the thing that was missing: configuration management as a Python library, with the same conceptual model as Ansible, so there is finally an idiom for the model to converge on.

## What Reemote is

Reemote is a Python package (`pip install reemote`) that does configuration management as a **library** rather than a CLI. There is no `reemote` binary. You write a deploy — a plain Python generator — and `await` it:

```python
plans, report, hosts = await run(inventory, deploy)
```

Four words cover the model:

- **Inventory** — a list of dicts naming your targets and how to reach them (over SSH, or through a local docker connector).
- **Fact** — a read of remote state: the hostname, the installed packages, a file's contents, a service's status. Facts are typed, sorted, JSON-printable data structures, never scraped strings.
- **Operation** — a request to converge the server toward a described state. An operation reads facts, decides whether anything must actually change, and emits commands *only* when it must. That is idempotence, built into every operation rather than bolted on.
- **Action** — an immediate, non-deploy escape hatch (`Shell`, `Script`, `WaitForConnection`) for one-off commands.

A deploy yields operation requests in order:

```python
def deploy(host):
    yield op(apt.packages, name="Install nginx", packages=("nginx",), sudo=True)
    yield op(files.install, name="Write landing page",
             content=PAGE, dest="/var/www/html/index.html", mode="0644", sudo=True)
    yield op(systemd.service, name="Run nginx",
             service="nginx", running=True, enabled=True, sudo=True)
```

That is the same three-step shape as the role at the top of this article — package, file, service — expressed as ordinary Python you can import, loop over, and unit-test.

The runner executes every deploy **twice**: once to plan against live facts, once to apply. Both passes must yield the identical request sequence or it raises a `DeterminismError`. `dry=True` stops after the plan and gives you a real preview against live state — Ansible's `--check`, but as a return value you can assert on instead of a wall of coloured diff output.

## Why Reemote is the right transpilation target

The properties that make Reemote a good library are the same ones that make it a good *destination* for an AI migration. Here they are, each tied to what it removes from the translation problem.

### Async end to end, on asyncssh

The transport is [asyncssh](https://asyncssh.readthedocs.io/), and the entire execution path — connecting, gathering facts, sending commands, closing — is coroutines. You `await` a deploy and hosts progress in parallel; network waits overlap across hosts while each host's commands stay in order. Your deploy script remains a plain synchronous generator, so you never write `async` inside an operation.

For a migration this matters because the model does not have to *invent* concurrency. Ansible gave you parallelism through `forks`; a naive Python translation loses it and loops over hosts one at a time. In Reemote the parallelism is a property of the runtime, not of the generated code — the AI produces the same simple generator either way, and the framework does the hard part.

### A library returning structured results, not a CLI

Ansible is a CLI: the result of a run is terminal output, and if your program needs to know what changed you regex-scrape stdout and hope. `run(...)` instead returns `(plans, report, hosts)`:

- **`plans`** — `{host_label: [PlannedOperation, ...]}`, the proposed work per host.
- **`report`** — an `ApplyReport` with one `LogEntry` per applied operation, each carrying `changed`, `reason`, and the exact commands that were sent. `None` when `dry=True`.
- **`hosts`** — the materialised host objects.

This is a target a model can hit reliably, because "read `report.entries[i].changed`" is an ordinary data-access pattern it has seen a million times — versus "parse Ansible's callback output," which it has not, and which changes between versions. Handlers become plain Python: read the first run's `report`, freeze the hosts that changed, and feed that set into a second deploy that restarts the service. No `notify` keyword, no handler-ordering rules to memorise.

### Comprehensive modules — a one-to-one vocabulary with Ansible

This is the single biggest reason Reemote translates well. A model can only map `apt_repository` onto something if that something exists. Reemote ships twenty modules, and each one implements the *whole* surface of its domain as operations and facts — not just the common verbs.

Take `apt`. Ansible spreads package management across `apt`, `apt_key`, `apt_repository`, `apt_mark`, and a dozen parameters. Reemote's `apt` module covers the same ground with matching operations: `packages` (install/upgrade/remove by name, version-pinnable), `repo` and `sources_file` (legacy `.list` and modern deb822 sources), `ppa`, `key` (GPG keyrings), `deb` (a standalone `.deb`), `mark` (hold/unhold), `update`, `upgrade`, `dist_upgrade`, and `auto_remove`. Every operation reads a matching fact — what is installed, what is held, which sources are configured, which keys are trusted, what an upgrade would move, what an autoremove would clean — so nothing is faked with a shell one-liner and the read side is as complete as the write side.

That completeness is what makes translation near-mechanical:

| Ansible | Reemote |
| --- | --- |
| `apt` / `package` | `apt.packages` / `dnf.packages` / `apk.packages` |
| `apt_key` | `apt.key` |
| `apt_repository` | `apt.repo`, `apt.sources_file`, `apt.ppa` |
| `apt` with `update_cache` | `apt.update` |
| hold / `apt_mark` | `apt.mark` |
| `copy` (inline content) / `template` | `files.install` (pre-rendered content) |
| `lineinfile` / `blockinfile` | `files.edit` / `files.transform` |
| `file` (mode/owner/state) | `files.file`, `files.directory`, `files.link` |
| `get_url` | `files.download` |
| `stat` | the file metadata, existence, and hash facts |
| `service` / `systemd` | `systemd.service` |
| `notify` + handler | two deploys + `changed_hosts` / `restart_handler` |

The twenty modules span package managers (`apt`, `dnf`, `apk`, `snap`, `flatpak`), files and version control (`files`, `git`), services (`systemd`, `openrc`), firewalls (`firewalld`, `iptables`, `ufw`), system configuration (`server`, `sysctl`, `selinux`, `hardware`), keys (`gpg`), databases (`mysql`, `postgresql`), and containers (`lxd`). When the destination vocabulary is this complete, the model spends its effort choosing the right operation instead of reimplementing a missing one.

### Pure functional deploys

Every request argument is hashable, a deploy is a pure function of its host, and the runner executes it twice — plan then apply — under a determinism guard that raises if the two passes disagree. There is no hidden mutable state, no reliance on the order a `for` loop happened to run in, no "did this already change?" flag threaded through globals.

For AI-generated code this is a safety net. Model output is plausible but unreviewed; a pure, deterministic deploy is *testable*. You can call the generator, inspect the requests it yields, and assert on them with `pytest` — no server required. And the determinism guard catches the classic model mistakes (a `datetime.now()`, a `random`, a lambda built inline) at plan time rather than in production. The framework enforces the discipline the generated code might otherwise lack.

### No awareness of the controller file system

Ansible couples a playbook to the controller's disk. `copy: src=app.conf`, `template: src=config.j2`, and `script: src=deploy.sh` all resolve a path *relative to the role's conventional directories* (`files/`, `templates/`) through a search-path rule. The task's meaning depends on where files happen to live on the machine running Ansible — an implicit, invisible dependency the model has to guess at and reproduce.

Reemote has no such coupling. A deploy never takes a controller path:

- `files.install` takes `content` as a plain string — the text is right there in the code.
- `files.put` and `files.get` take an **IO-like stream** you opened yourself; a path string is deliberately *not* accepted.
- `files.copy` and `files.move` are remote-to-remote — `src` is a path on the target, not on the controller.

The result is that a Reemote deploy is self-contained and pure: given the same host and the same arguments it produces the same requests, with nothing implicit read off the controller's layout. For a transpiler that removes an entire class of guesswork — the AI never has to replicate Ansible's role-directory search magic or invent a file layout. It renders the content (from a string, a Pydantic model, or a Jinja2 template pre-rendered control-side) and hands it to an operation.

## What this means for your migration

Point the model at Reemote's module reference as context, feed it the role, and ask it to map each task to an operation. Because the vocabulary lines up, the output is bounded and reviewable: a generator of `op(...)` requests, not an architecture. You read it the way you would read any pull request — does this operation match that task, are the privilege flags right, is the content correct.

The constructs that do *not* map one-to-one — handlers, `host_vars`, Jinja2 templates, `lineinfile` edits, `get_url` downloads — each have a documented Reemote shape, and the migration guide works through them construct by construct with before/after pairs. That guide is the human companion to this argument: once you accept that the library is the right target, it shows you the exact mappings. See the [Ansible role migration guide](../migrating.md).

The model still matters, of course — a stronger model makes fewer mistakes. But it is a second-order effect. The first-order effect is whether there is a framework to translate into. Now there is.

## What Reemote is not

Honest boundaries, because overselling helps nobody:

- **Not a deployment CLI.** There is no `reemote` command. You write the script.
- **Not an inventory format.** Inventory is a Python list of dicts — load it from JSON, YAML, a database, or write it inline.
- **Not a role ecosystem.** Twenty modules ship in the box, but there is no Ansible Galaxy equivalent to pull third-party roles from.
- **Not an OS translation layer.** Python does not make `nginx` installable by the same name on Alpine and Debian. You still pick the right module for the target — the AI still has to know `apk` from `apt`.
- **Not an automatic transpiler.** Reemote is the *target*, not the translator. The AI does the mapping and you review it. The library's job is to make that mapping bounded, idiomatic, and testable — which is precisely the part that was impossible before.

## Try it

- Documentation and full guide: <https://reemote.org/>
- Ansible role migration guide: <https://reemote.org/migrating/>
- Source: <https://github.com/kimjarvis/reemote>
- PyPI: `pip install reemote`
- License: EPL-2.0

If you have tried to get an AI to translate an Ansible role and come away with unreviewable spaghetti, the fix was never a bigger prompt. It was a library worth translating into. Give Reemote a minute.
