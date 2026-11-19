# Why AI translations of Ansible roles fail — and the Python library designed to fix them

*A study of AI-driven code translation finds that converting Ansible configuration roles into Python systematically loses the properties that made them safe to run on production infrastructure. The cause is structural rather than a deficit of model capability: Python has lacked a configuration-management library for the model to translate into. An open-source library, Reemote, is presented as that target, and the approach is evaluated through an automated conversion of popular Ansible Galaxy roles — including Jeff Geerling's canonical roles — into Python.*

When a large language model is asked to rewrite an Ansible role in Python, the result typically runs: it imports `subprocess`, shells out to `apt-get`, checks a return code, and wraps the sequence in a `try/except`. What it does not do is behave like the role it replaced. A new evaluation of AI-driven configuration-code translation examines this gap, identifies its structural cause, and reports an experiment in which supplying a dedicated target library yields bounded, reviewable programs rather than improvised scripts.

## Lost in translation

Ansible is not merely a list of commands. It is a declarative orchestration engine, and a role inherits a set of operational guarantees from the engine that runs it. The study finds that when an AI translates a role into ordinary Python without a specialised target framework, three of these guarantees are systematically lost.

The first is **lock-step execution across multiple hosts**. Ansible advances a fleet of machines through a playbook together, parallelising each task across hosts while keeping the task order intact. A naive translation collapses this into a sequential loop, or into an improvised concurrency scheme that no longer preserves the ordering the role depended on.

The second is **dry-run capability**. Ansible's `--check` mode previews what would change against live infrastructure before anything is touched. Ad-hoc generated scripts execute their mutations blind; there is no plan to inspect, because the script never separated deciding from doing.

The third is **change identification**. Ansible reports, per task and per host, whether state was actually modified, already correct, or failed. A translated script emits terminal text at best. Recovering the same information means scraping standard output with regular expressions — fragile, version-dependent, and rarely attempted by generated code.

The result is a program that reproduces the role's happy path while discarding the machinery that made it operable: no fleet orchestration, no preview, no reliable account of what changed.

## A missing pattern, not a missing capability

The root cause, the work argues, is neither model capacity nor context length. Large language models generate by matching patterns they have seen, and translation tasks succeed when a canonical target idiom exists to converge on. Compiling C to assembly or converting JSON to a dataclass works because the destination shape appears millions of times in training data.

"Translate an Ansible role to Python" has no such destination. Historically, Python has lacked a dedicated configuration-management library mirroring Ansible's conceptual shape — typed facts, idempotent operations, a unified module vocabulary. What exists instead are wrappers that launch Ansible itself, general SSH-automation frameworks, and raw building blocks such as `paramiko` and `subprocess`. None of them is a configuration-management idiom.

With no pattern to match, the model improvises from the nearest primitives it knows, reinventing a flawed configuration engine with every generation. Repeated runs, or different models, produce different arrangements. The authors report that spending more tokens produces longer, better-commented output but not convergence: prompting cannot supply an idiom that does not yet exist.

## Why Python is worth the effort

The motivation for the work is that Python offers advantages over Ansible's YAML-based domain-specific language, provided the operational guarantees can be preserved.

Infrastructure logic becomes ordinary code: testable with standard frameworks such as `pytest`, analysable with type checkers and linters that catch errors at development time rather than on production nodes. Complex data transformations, API integrations, and machine-learning pipelines can live beside deployment logic instead of being shelled out to. Variables become function arguments and typed data structures rather than a templating dialect with its own precedence rules. Logic that exceeds the expressive limits of a declarative DSL can be written directly in the host language.

What has been missing is a way to reach those advantages without surrendering idempotence, orchestration, and change reporting on the way.

## Five properties of a translation target

The solution examined in the work is **Reemote**, an open-source Python package that implements configuration management as a library rather than a command-line tool. The study characterises five architectural properties that make it a viable destination for AI translation.

**Asynchronous execution on asyncssh.** The entire execution path — connecting, gathering facts, sending commands — is built on Python's `async` machinery and the `asyncssh` transport. Hosts progress concurrently, network waits overlap across the fleet, and each host's commands remain strictly ordered. The generated code does not handle this complexity directly: a deploy is a plain synchronous generator, and the runtime supplies the concurrency. The model therefore works within an existing concurrency framework rather than constructing one.

**Structured results instead of a CLI.** A run returns data, not terminal output:

```python
plans, report, hosts = await run(inventory, deploy)
```

The plans describe the proposed work per host; the report carries one entry per applied operation, each recording whether it changed anything, why, and the exact commands sent. The authors describe structured returns as essential for automation, and for translation: reading a field such as `entry.changed` is a common data-access pattern in training data, whereas parsing Ansible's callback output is not. A `dry=True` flag stops after planning, restoring the lost dry-run capability as a return value a program can assert on.

**Comprehensive modules with a one-to-one Ansible vocabulary.** The modules cover package management, files and version control, services, firewalls, system configuration, keys, databases, and containers. Each implements its domain as operations paired with the facts they read, so `apt_repository` maps to `apt.repo`, `lineinfile` to `files.edit`, `get_url` to `files.download`, and `service` to `systemd.service`. The work identifies this vocabulary as the main enabler of translation: Ansible tasks become Python routine and function calls — a bounded mapping the model can perform and a human can review — instead of an architecture the model must design.

**Pure functional deploys.** Every deploy is a pure generator function of its host, and the runner executes it twice — once to plan against live facts, once to apply — raising a determinism error if the two passes disagree. For AI-generated code, this makes the output testable without live servers, and typical model mistakes such as a stray `datetime.now()` are caught at plan time rather than in production.

**No awareness of the controller's file system.** Ansible resolves `copy` and `template` sources through implicit search paths relative to the role's directories, coupling a playbook to the disk layout of the machine running it. Reemote deploys take content as explicit strings or caller-opened streams, never controller paths. A converted program is therefore self-contained and portable: it behaves identically regardless of where it is invoked from, and the translator does not need to reproduce Ansible's implicit directory-search rules.

## The experiment: Galaxy roles, converted

To validate the approach, an empirical evaluation was conducted in which popular Ansible Galaxy roles were converted automatically, with an AI agent given the library's module reference as context. The subjects included Jeff Geerling's widely used Apache role — which installs and configures Apache across four operating-system families — and DataDog's official agent role, a larger configuration spanning repositories, signing keys, templated configuration, and service handlers.

The resulting Python programs are publicly available in the Reemote git repository, under `examples/roles/`. The Apache conversion is an 888-line program expressing the role's Debian/Ubuntu path; the DataDog conversion runs to 1,559 lines. Both are sequences of small deploys rather than monoliths: where a role branches on a mutable read — a probed Apache version, an enumerated certificate directory — the conversion gathers the fact between runs, freezes the answer, and closes a deploy factory over it. Handlers become a second deploy pass filtered by the first pass's structured report, replacing Ansible's `notify` keyword with ordinary Python.

Each converted program is exercised end-to-end in an offline test suite against a simulated host, through the library's real execution driver. The central assertion is idempotence: after a complete first run, a second run against the same state must report no changes anywhere and queue no restart handlers. Both conversions pass.

The experiment also functioned as a stress test of the library itself. Mapping the DataDog role exposed five capability gaps in the library — additive import of multiple signing keys into a shared keyring, read-back of service-owned configuration files, idempotent "latest" upgrades, index refresh after repository changes, and provenance-aware repository removal. All five were subsequently closed in the library, and the converted program simplified as each fix landed. One remaining gap is confined to the offline test harness rather than real execution, and is bridged by a documented test-local shim. The authors note a feedback loop in which each conversion exposed gaps, the gaps were closed in the library, and the converted program simplified in turn. The evaluation is scoped: each conversion covers the role's default path on a single operating-system family, and the reported verification runs against simulated hosts rather than a live fleet.

## What follows

The authors conclude that for AI-assisted infrastructure migration, the choice of Python library matters more than the choice of model. With a comprehensive configuration-management library whose vocabulary lines up with Ansible's, the task reduces from designing a bespoke architecture to mapping each task onto an operation that already exists — a bounded mapping that a human can review.

Two consequences follow. First, **Reemote is a library for configuration management using Python** — the category the cause analysis identified as missing — and is distributed via PyPI. Second, the authors position **Reemote as an alternative to Ansible**. The converted roles indicate that package, file, service, and handler logic can survive translation with concurrency, dry-run preview, and structured change reporting supplied by the library rather than improvised by the model, within the scope of the evaluated paths.

The work acknowledges several boundaries: Reemote is the translation target, not the translator itself. The AI performs the mapping and a human reviews it; there is no Galaxy-equivalent role ecosystem, and the right module must still be chosen for the target operating system. The broader implication, the authors suggest, is that improving AI-generated infrastructure code may depend less on larger prompts than on the existence of a suitable target library.

## More information

* **Documentation:** <https://reemote.org/>
* **Ansible role migration guide:** <https://reemote.org/migrating/>
* **Converted roles:** <https://github.com/kimjarvis/reemote/tree/main/examples/roles>
* **Source repository:** <https://github.com/kimjarvis/reemote>
* **PyPI:** `pip install reemote` (license: EPL-2.0)