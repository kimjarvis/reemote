
# Reemote

**Reemote is a Python library for configuring and deploying servers.** Describe
the packages, files and services you want using Python functions; reemote
queries each server, plans the work and applies the changes. Your deployment is
a Python program that you can import, test, compose and run from your existing
automation.

Reemote is built on a two-phase *prepare → execute* execution model. Deploy
scripts are **Python generators** that yield `OperationRequest` descriptors
instead of calling operations directly. That lets the driver pause the script at
every operation boundary: the prepare phase produces a full plan for dry-run
reporting, and the apply phase runs all hosts in lockstep with a fresh fact
cache per step, so sequential *"act, then read"* logic gives the correct answer
in both phases.

**Scope.** reemote is a library, not a deployment tool. It ships no CLI and no
inventory format: hosts are a plain *inventory* (a list of dictionaries) handed
to `run`. The transport is real — the default `Host.from_inventory` factory
opens an [asyncssh] connection per entry, gathers facts and runs commands over
it, and `run` closes the connections when it finishes.

## Installation

The *controller* is the machine running your Python script. It needs **Python
3.13 or newer**. You do not install reemote on the target servers.

**Installing from source is the recommended route**, because the checkout also
gives you the runnable `examples/` tree and lets you pin the exact revision you
deploy from. Run this in a directory where you want to keep the project:

```bash
git clone https://github.com/kimjarvis/reemote.git
cd reemote
uv sync
source .venv/bin/activate
```

`uv sync` creates `.venv`, installs reemote in editable mode and resolves its
declared dependencies — `asyncssh`, `distro` and `pydantic` — so the SSH
transport needs no separate install step. Here we use 
[uv](https://docs.astral.sh/uv/); `venv` and `pip` work just as well if you
prefer them.

The package is **also published on [PyPI](https://pypi.org/project/reemote/)**,
which is enough if you only need the library and not the examples:

```bash
uv pip install reemote
```

Inside a project of your own, use `uv add reemote` instead so it lands in your
`pyproject.toml`.

## License & Intellectual Property

### License
This project is licensed under the Eclipse Public License 2.0 (EPL-2.0).
SPDX-License-Identifier: EPL-2.0

### Origin & Open Source Release
This software is part of a commercially available application. It is being released to the open source community under the EPL-2.0 to foster collaborative development, improve software reliability, and encourage community contributions and issue identification.

### Originality & Attribution
This codebase was independently developed and was inspired by the design concepts of Pyinfra by Nick Mills-Barrett. This project is an original work and does not contain, incorporate, or derive from any source code or copyrighted material of Pyinfra.

[asyncssh]: https://asyncssh.readthedocs.io/
