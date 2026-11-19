Ansible galaxy transpiled to python

Translating Ansible roles to Python turns infrastructure logic into ordinary code: unit-testable with pytest, checkable with type checkers and linters, and free of the expressive ceiling of a YAML DSL with a templating dialect. Until now the problem has been intractable, because Python had no configuration-management library shaped like Ansible to translate into — an AI asked to rewrite a role improvises a different pile of subprocess and SSH calls every time, losing idempotence, fleet orchestration, dry-run preview and change reporting along the way. A new library fills that gap: a Python framework whose facts, operations and module vocabulary line up with Ansible's, giving the translation a bounded, reviewable destination.

The problem

An Ansible role is not merely a list of commands. It inherits its operational guarantees from the engine that runs it: lock-step execution across the fleet, --check mode previewing changes against live state, and per-task, per-host reporting of what actually changed. A translation into ordinary Python with no target framework loses all three. The generated script shells out to apt-get, checks a return code, and wraps the sequence in a try/except — it reproduces the role's happy path while discarding the machinery that made it safe to run on production infrastructure.

The cause is structural, not a deficit of model capability. A large language model generates by matching patterns, and translation succeeds when a canonical target idiom exists to converge on. "Translate an Ansible role to Python" has had no such destination: what Python offered instead were wrappers that launch Ansible itself, general SSH-execution libraries, and raw building blocks. With no pattern to match, every generation re-derives a different arrangement of those primitives, and spending more tokens produces longer, better-commented output but not convergence.

Why it is interesting

The prize is real. In Python, infrastructure logic becomes testable with standard frameworks, analysable before deployment, and expressible without a DSL's limits — variables become typed data structures instead of a templating dialect, and complex computations live beside the deploy instead of being shelled out to a second toolchain.

And the diagnosis points at a fix that is tractable. If the bottleneck is a missing target idiom rather than a weak model, then building the idiom changes the problem's shape: with a library whose vocabulary lines up one-to-one with Ansible's, translation collapses from "design a configuration-management system" to "map each task onto the operation that already does this." That second task is exactly what models are good at, and it is bounded enough that a human can review the output the way they would review any pull request. For this migration, the choice of library dominates the choice of model.

The solution

The new library treats configuration management as a Python library rather than a CLI. A deploy is a plain synchronous generator yielding operation requests; plans, report, hosts = await run(inventory, deploy) executes it. Five properties make it a translation target:

Async end to end on asyncssh — the runtime supplies fleet-wide concurrency with per-host ordering, so the generated code never invents it.

Structured results — a run returns a plan and a per-operation report of what changed, why, and the exact commands sent; dry=True restores the lost dry-run as data a program can assert on.

A one-to-one module vocabulary — modules covering package managers, files, services, firewalls, system configuration, keys, databases and containers, so apt_repository maps to apt.repo, lineinfile to files.edit, get_url to files.download, service to systemd.service.

Pure functional deploys — the runner executes every deploy twice, plan then apply, and raises if the passes disagree; typical model mistakes are caught at plan time, and the code is testable without servers.

No awareness of the controller's file system — content is an explicit string or a caller-opened stream, never a controller path, so a converted program is self-contained and portable.

What follows from the solution's structure

Because the target is a library with a fixed vocabulary and pure, testable deploys, every correct conversion becomes a reusable pattern — and the repository ships them. Alongside the library source, the EPL-2.0 repo contains close to four hundred runnable example programs, executed by the example runner and exercised by the test suite. You can read them, copy them, and hand them to a model as context without a licensing question hanging over the output.

Ten of them are automated translations of Ansible Galaxy roles, under examples/roles/: Jeff Geerling's Apache, Docker, Java, Memcached, NFS, nginx, NTP, PHP and PHP Xdebug roles, and DataDog's official agent role. Each covers the role's default path on one operating-system family and ships with an offline test suite driving it end-to-end against a simulated host through the real execution driver; the central assertion is idempotence — a second run against the same state reports no changes and queues no handlers. All ten pass. Between them they demonstrate handlers as a second deploy pass, role variables in the inventory, pre-rendered templates, and a build-from-source branch.

The practical consequence: when you migrate your own role, do not ask the model for "Python" — ask it for the shape of the closest converted role. The model then imitates a verified, tested, in-repo program, and its job shrinks from designing an architecture to adapting one that already works. The same programs give a human reviewer a known-good standard to judge the output against.

Ansible Galaxy's value was never the YAML — it was the accumulated, working configuration knowledge. Transpiled to Python, that knowledge carries over: the open repository proves the method and seeds the corpus, and a companion control plane is where much of the catalogue, and the machinery to grow it, lives.