# Migrating Ansible roles to Python: a library-level translation target

## Abstract

When a large language model rewrites an Ansible role in ordinary Python, the result typically executes but does not behave like the role it replaces: fleet-wide lock-step execution, dry-run capability, and per-host change reporting are lost. We argue that the cause is structural rather than a deficit of model capability: translation tasks succeed when a canonical target idiom exists, and Python has lacked a configuration-management library for the model to translate into. We survey prior Python-based approaches — programmatic control of Ansible, general remote-execution libraries, and Python-native configuration management — with particular attention to pyinfra, the closest existing analogue, which we find falls short as a translation target on five axes: its concurrency model is not built on asyncio, its results interface is oriented toward the command line rather than a typed return value, its module vocabulary covers a fraction of Ansible's, its deploys read mutable live state without a determinism guarantee, and its file operations depend on the controller's file system. We then describe Reemote, an open-source library designed against these five criteria, and report an evaluation in which two Ansible Galaxy roles were converted automatically and verified for idempotence in an offline simulation.

## 1. Introduction

Ansible expresses server configuration as declarative playbooks and roles in YAML [1]. The language is widely deployed, but it offers limited support for the surrounding engineering practice: infrastructure logic is not unit-testable with ordinary Python test frameworks, is not checkable with static type analysis, and cannot directly express computations that exceed the limits of a declarative DSL with a templating dialect.

Migrating roles to Python would recover these properties, and large language models make such a migration appear cheap: a role can be pasted into a model with the instruction to rewrite it in Python. The output, however, is typically a script that shells out to package managers and wraps the sequence in error handling — a program that reproduces the role's happy path while discarding the machinery that made the role operable.

This paper examines why. Section 2 surveys prior work and analyses pyinfra, the closest prior approach, against five requirements of a translation target. Section 3 formulates the problem: the properties a translated program loses, and the motivation for reaching Python without losing them. Section 4 describes the design of Reemote [15], an open-source library that implements configuration management as a library rather than a command-line tool. Section 5 reports an evaluation in which two Galaxy roles were converted automatically, and Section 6 states the limitations of that evaluation.

## 2. Prior work

### 2.1 Programmatic control of Ansible

The most direct approach to operating Ansible from Python is to keep Ansible. `ansible-runner` [2] provides a stable interface for launching playbooks and consuming their events; Ansible's internal Python API exposes playbook parsing and execution. Neither removes Ansible from the execution path: the YAML, the state engine, and the CLI-shaped result all remain. These tools wrap Ansible; they do not provide a Python vocabulary of configuration operations that a translator could map roles onto.

### 2.2 Remote-execution libraries

Fabric [3] and Paramiko [4] provide SSH connectivity and remote command execution. They operate at the level of shell commands: there are no facts that describe host state, no operations that compare desired and actual state, and no change reporting beyond command exit status. They are the primitives that an improvised translation reaches for, not a target idiom for one.

### 2.3 Python-native configuration management

Salt [5] implements configuration management in Python, but as a distributed system — a daemon on each managed host (or an agentless SSH mode) driven by a CLI and its own state and pillar subsystems. It is not designed to be imported and called as a library, and its state language is a domain-specific system of its own rather than an idiom an arbitrary model can map Ansible tasks onto.

pyinfra [6] is the closest existing analogue to the missing target. Deploys are Python files; operations are Python functions that compare desired state against facts and yield commands only when a change is required; execution is two-phase, with a preparation phase that gathers facts and an execution phase that applies commands, which enables a `--dry` flag [7]. pyinfra is agentless and Python-native, and its operation functions are generator functions that read facts and yield commands [8] — a shape recognisably similar to the one this paper argues for.

### 2.4 pyinfra as a translation target

Despite this similarity, five documented characteristics of pyinfra limit its suitability as the destination for an automated Ansible-to-Python translation.

**Concurrency model.** pyinfra's concurrency is built on gevent and its SSH transport on Paramiko [9]. Neither belongs to the asyncio ecosystem: a pyinfra run cannot be awaited from an asyncio event loop, and a program already built on asyncio must bridge between the two concurrency models or delegate the run to a subprocess.

**Results interface.** pyinfra's primary interface is a CLI that writes results to the terminal [7]. A program that needs to know what changed consumes either human-readable output or, in pyinfra 3, the `--json` serialisation of that CLI output [10]. When pyinfra is used in API mode, results are accumulated in the global `State` object during the run rather than returned by the call that performs it. There is no typed, per-operation report returned from a library call, with each entry recording whether it changed anything, why, and the exact commands sent.

**Module vocabulary.** pyinfra bundles 46 operation modules [11]. Ansible's ecosystem spans several thousand modules across its collections [12], and Ansible roles routinely draw on domains that pyinfra does not cover: firewall management beyond `iptables` (`firewalld`, `ufw`), kernel parameter configuration (`sysctl`), GPG keyring management, and hardware inventory are absent from pyinfra's module list [11]. A role that touches such a domain has no destination operation; the translator must invent one, which is precisely the unbounded generation this paper argues against.

**Purity and idempotence.** pyinfra's idempotence is a per-operation convention: an operation compares facts to arguments and yields commands only when needed [8]. It is not an enforced global property. Deploy code reads mutable live state as it is evaluated — facts are fetched from hosts while operation functions are called [7] — and the framework documents that operations depending on the changes of earlier operations observe stale facts on the first run, leaving them inoperative until a re-run or an explicit hint such as `assume_present` [7]. Several bundled operations are documented as stateless and non-idempotent, including `apt.ppa`, `apt.update`, `files.get`, and `files.rsync` [11]. There is no mechanism that verifies a deploy would produce the same plan if evaluated again; the determinism of a deploy depends on the discipline of its author.

**Controller file-system awareness.** pyinfra's file operations take paths on the machine running pyinfra: `files.put` resolves `src` relative to the deploy directory by default, `files.template` reads a template file from the controller (the documented convention is a `templates/` directory, with the current working directory as the default lookup path), and `files.get` and `files.sync` write to and read from controller directories [13]. A deploy's behaviour therefore depends on the disk layout of the controller, and a translator must reproduce these search-path rules to preserve meaning.

None of the surveyed approaches provides a library that is asyncio-native, returns structured results, covers Ansible's module vocabulary, enforces deploy determinism, and is independent of the controller's file system. The remainder of this paper describes a design that does.

## 3. Problem statement

### 3.1 Properties lost in translation

An Ansible role inherits three operational guarantees from the engine that runs it [1], and a translation into ordinary Python — with no target framework — loses all three.

The first is lock-step execution across multiple hosts. Ansible advances a fleet through a playbook together, parallelising each task across hosts while preserving task order. A naive translation collapses this into a sequential loop or an improvised concurrency scheme that no longer preserves ordering.

The second is dry-run capability. Ansible's `--check` mode previews what would change against live infrastructure before anything is modified [1]. Ad-hoc generated scripts execute their mutations without a plan to inspect, because they never separated deciding from doing.

The third is change identification. Ansible reports, per task and per host, whether state was modified, already correct, or failed. A translated script emits terminal text at best; recovering the same information from standard output is fragile and version-dependent.

### 3.2 Why Python

The motivation for migration is that Python offers properties the YAML DSL does not, provided the three guarantees above are preserved. Infrastructure logic becomes ordinary code: testable with standard frameworks such as `pytest`, analysable with type checkers and linters before deployment, and expressible without the ceiling of a declarative language. Variables become function arguments and typed data structures rather than a templating dialect, and complex data transformations can live beside deployment logic instead of being shelled out to a second toolchain.

## 4. Design of a translation target

The design question is what architectural properties a Python library must have for an AI translation of an Ansible role to be bounded and reviewable. We implemented these properties in Reemote [15], and we argue each removes one class of decisions the translator would otherwise have to make.

**Asynchronous execution on asyncssh.** The entire execution path — connecting, gathering facts, sending commands — is built on Python's `async` machinery and the asyncssh transport [14]. Hosts progress concurrently, network waits overlap across the fleet, and each host's commands remain strictly ordered. The generated code does not handle this concurrency: a deploy is a plain synchronous generator, and the runtime supplies the parallelism. The translator therefore works within an existing concurrency framework rather than constructing one — and the result is directly composable with asyncio-based programs, addressing the first pyinfra limitation of Section 2.4.

**Structured results.** A run returns data, not terminal output:

```python
plans, report, hosts = await run(inventory, deploy)
```

The plans describe the proposed work per host; the report carries one entry per applied operation, each recording whether it changed anything, why, and the exact commands sent. A `dry=True` flag stops after planning and returns the plan as data a program can assert on. Reading a field such as `entry.changed` is a common data-access pattern in training data; parsing CLI output is not.

**Module vocabulary mapped to Ansible.** The modules cover package management, files and version control, services, firewalls, system configuration, keys, databases, and containers. Each module implements its domain as operations paired with the facts they read, so `apt_repository` maps to `apt.repo`, `lineinfile` to `files.edit`, `get_url` to `files.download`, and `service` to `systemd.service`. The vocabulary is the main enabler of translation: an Ansible task becomes a function call that already exists — a bounded mapping the model can perform and a human can review.

**Pure functional deploys.** Every deploy is a pure generator function of its host, and the runner executes it twice — once to plan against live facts, once to apply — raising a `DeterminismError` if the two passes disagree. This addresses the fourth pyinfra limitation directly: purity is not left to author discipline but enforced by the runtime, and a deploy that reads mutable state (a stray `datetime.now()`, a fact consulted at the wrong time) is rejected at plan time. The property also makes generated code testable without live servers.

**No awareness of the controller's file system.** Deploys take content as explicit strings or caller-opened streams, never controller paths; file transfers between hosts are remote-to-remote. A converted program is self-contained and portable: it behaves identically regardless of where it is invoked from, and the translator does not need to reproduce any controller-side directory-search rules, addressing the fifth limitation.

## 5. Evaluation

To examine whether these properties support automated conversion in practice, two Ansible Galaxy roles were converted with an AI agent given the library's module reference as context. The subjects were Jeff Geerling's Apache role [16], which installs and configures Apache across several operating-system families, and the agent role published by Datadog [17], a larger configuration spanning repositories, signing keys, templated configuration, and service handlers.

The converted programs are publicly available in the Reemote repository under `examples/roles/` [16]. The Apache conversion is an 887-line program expressing the role's Debian/Ubuntu path; the Datadog conversion runs to 1,558 lines. Both are sequences of small deploys rather than monoliths: where a role branches on a mutable read — a probed Apache version, an enumerated certificate directory — the conversion gathers the fact between runs, freezes the answer, and closes a deploy factory over it. Handlers become a second deploy pass filtered by the first pass's structured report, replacing Ansible's `notify` keyword with ordinary Python.

Each converted program is exercised end-to-end in an offline test suite against a simulated host, through the library's real execution driver. The central assertion is idempotence: after a complete first run, a second run against the same state must report no changes anywhere and queue no restart handlers. Both conversions pass.

The conversion also served as a stress test of the library. Mapping the Datadog role exposed five capability gaps — additive import of multiple signing keys into a shared keyring, read-back of service-owned configuration files, idempotent "latest" upgrades, index refresh after repository changes, and provenance-aware repository removal. All five were closed in the library, and the converted program simplified as each fix landed; one remaining gap is confined to the offline test harness and is bridged by a documented test-local shim. We note this as a feedback loop between converter and target: each conversion exposed gaps, the gaps were closed in the library, and the conversion simplified in turn.

## 6. Limitations

The evaluation is narrow. Each conversion covers the role's default path on a single operating-system family; the verification runs against simulated hosts rather than a live fleet; and the subject set is two roles. Reemote is the translation target, not the translator itself: the AI performs the mapping and a human reviews it. There is no Galaxy-equivalent ecosystem of third-party deploys, and choosing the correct module for the target operating system remains a manual step. The comparison in Section 2.4 rests on pyinfra's documented behaviour as of its 3.x release; pyinfra is actively developed, and individual limitations may narrow over time.

## 7. Conclusion

For AI-assisted infrastructure migration, the properties of the target library matter more than the choice of model. Where the library's vocabulary lines up with Ansible's, the task reduces from designing a bespoke architecture to mapping each task onto an operation that already exists — a bounded mapping a human can review.

The survey of prior work found no Python library meeting the five criteria of Section 4; the closest, pyinfra, provides the right conceptual shape but lacks asyncio-native execution, a typed results interface, Ansible-scale module coverage, enforced deploy determinism, and controller-independence. Reemote [15] is an implementation of those criteria, distributed via PyPI under the EPL-2.0 license. Within the scope of the evaluated paths, the converted roles indicate that package, file, service, and handler logic can survive translation with concurrency, dry-run preview, and structured change reporting supplied by the library rather than improvised by the model.

## References

1. [Ansible documentation](https://docs.ansible.com/) — playbooks, check mode, and change reporting.
2. [ansible-runner](https://ansible.readthedocs.io/projects/ansible-runner/) — a stable Python interface for launching and consuming Ansible runs.
3. [Fabric](https://www.fabfile.org/) — remote SSH command execution in Python.
4. [Paramiko](https://www.paramiko.org/) — a Python SSH library.
5. [Salt documentation](https://docs.saltproject.io/) — Python-implemented, daemon-based configuration management.
6. [pyinfra](https://pyinfra.com/) — agentless infrastructure automation in Python.
7. [pyinfra: Deploy Execution](https://docs.pyinfra.com/en/3.x/deploy-process.html) — two-phase execution, fact gathering at operation-definition time, and the interdependent-operations limitation.
8. [pyinfra: Writing Operations](https://docs.pyinfra.com/en/3.x/api/operations.html) — operations as generator functions that read facts and yield commands.
9. [pyinfra source repository](https://github.com/pyinfra-dev/pyinfra) — the 3.x branch declares gevent and paramiko as dependencies.
10. [pyinfra: Using the CLI](https://docs.pyinfra.com/en/3.x/cli.html) — terminal output and the `--json` serialisation flag.
11. [pyinfra: Operations Index](https://docs.pyinfra.com/en/3.x/operations.html) — the bundled module list and the stateless/non-idempotent annotations.
12. [Ansible collection index](https://docs.ansible.com/ansible/latest/collections/index.html) — the scale and breadth of Ansible's module ecosystem.
13. [pyinfra: files Operations](https://docs.pyinfra.com/en/3.x/operations/files.html) — controller-side paths in `files.put`, `files.template`, `files.get`, and `files.sync`.
14. [asyncssh](https://asyncssh.readthedocs.io/) — asynchronous SSH for Python.
15. [Reemote](https://reemote.org/) — documentation; source at [github.com/kimjarvis/reemote](https://github.com/kimjarvis/reemote); `pip install reemote` (EPL-2.0).
16. [geerlingguy/ansible-role-apache](https://github.com/geerlingguy/ansible-role-apache) — the Apache role; its converted form lives at [`reemote.roles.apache`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/apache.py).
17. [DataDog/ansible-datadog](https://github.com/DataDog/ansible-datadog) — the Datadog agent role; its converted form lives at [examples/roles/ansible-datadog](https://github.com/kimjarvis/reemote/tree/main/examples/roles).
