# kubectl — kubectl role

| | |
| --- | --- |
| Role | [`andrewrothstein.kubectl`](https://github.com/andrewrothstein/ansible-kubectl) |
| Module | [`reemote.roles.kubectl`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/kubectl.py) |
| Runner | [`examples/roles/kubectl.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/kubectl.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `kubectl` role mapping → `KubectlVars` |

<!---
| Waves | exactly 2 — the `Arch` fact gate, then the three requests (a dry pass parks at the gate, 1 wave) |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed — nothing is templated) |
| Offline test | [`tests/roles/test_kubectl.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_kubectl.py) |
| Factory | plain (non-`async`) — the runner calls `kubectl(v)`; the binary fetch lives *inside* `files.download`, not in a `control` barrier |

`kubectl` installs [kubectl](https://dl.k8s.io/), the Kubernetes
command-line tool, on Ubuntu hosts: the version-pinned binary, verified
against a `sha256` checksum and installed as `kubectl-<ver>` in
`/usr/local/bin`, with the bare `kubectl` symlinked beside it.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("kubectl", kubectl(v))(host)` and a run is one `run_waves`
pass. The Ansible role's whole task list is that single deploy, and its
`defaults/main.yml` is `KubectlVars`.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars` OS dispatch (`vars/Windows.yml` adds `.exe` names) | family guard + frozen Linux constants (D1); `vars/main.yml`'s URL/path formulas become `kubectl_url` and the body's `exe`/`link` locals | deviation (D1) |
| `ansible_facts.architecture` → `kubectl_arch_map[...]` | `yield fact(server.Arch)` + `arch_for` | barrier |
| `kubectl_checksums[ver]["linux"][arch]` (a 4,300-line default) | `LINUX_CHECKSUMS` (recent releases) + the `checksums` var, `resolve_checksum` fail-fast | deviation (D4) |
| `file: path={{ kubectl_install_dir }} state: directory mode: '755'` + `become` | `files.directory(install_dir, mode="0755", sudo=…)` | covered |
| `get_url: url=… dest=… checksum=sha256:… mode: '755'` + `become` | `files.download(url, dest, mode, checksum, sudo=…)` | deviation (D2) |
| `file: src=… dest=… state: link` + `become` | `files.link(path, target, sudo=…)` | covered |
| `handlers/main.yml` | empty upstream — no checkpoint, no handler | n/a |
| `test.yml` smoke (`kubectl version`) | outside the role body upstream too | scoped out |

The migration surfaced **no new library gaps**: `files.download` (issue #20,
binary path #23) is the checksum-verified `get_url` equivalent, and the
`fact` barrier carries the architecture read that used to come from Ansible's
implicit fact gathering.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

No `reemote[templates]` — the role renders nothing; the URL, the version-
suffixed destination and the link name are plain f-strings.

The binary is fetched **control-side** by `files.download`, so the control
machine needs outbound HTTPS to `dl.k8s.io` (or the configured mirror); the
host downloads nothing. SSH access to each Ubuntu host with passwordless sudo
(or a `privilege` section in the inventory): every request carries
`sudo=hv.privilege_escalate`, because reemote has no play-level `become`. The
installed binary is read back unprivileged for the idempotence comparison, so
the `0755` mode the role installs at is also what keeps the second pass
cheap — a login-unreadable `install_dir` mode would break convergence, exactly
as it would break Ansible's `get_url` read.

## 3. Variables

`KubectlVars` is a frozen pydantic model whose fields carry bare names; the
role name passed to `resolve_vars` binds the entry's `kubectl` role mapping.

--->

::: reemote.roles.kubectl
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - kubectl

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `ver` | `str` | `v1.37.2` | `kubectl_ver` |
| `mirror` | `str` | `https://dl.k8s.io/release` | `kubectl_mirror` |
| `install_dir` | `str` | `/usr/local/bin` | `kubectl_install_dir` |
| `privilege_escalate` | `bool` | `true` | `kubectl_privilege_escalate` |
| `checksums` | `tuple[KubectlChecksum, ...]` | `()` | `kubectl_checksums` |

<!---

Each `checksums` entry is a `KubectlChecksum` row — the flat, hashable form
of the role's nested `kubectl_checksums[ver]["linux"][arch]` cell:

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | `str` | the release the digest belongs to (`v1.37.2` form) |
| `arch` | `str` | a `dl.k8s.io` architecture name: `amd64`, `arm`, `arm64`, `s390x`, `ppc64le` |
| `sha256` | `str` | the published binary's 64-character hex digest, no `sha256:` prefix |

Precedence is `model defaults < inventory role mapping < overrides`.
Validation is fail-fast, before any host is contacted: `ver` must be a plain
release name (non-empty, no `/`), `mirror` non-empty (a trailing `/` is
stripped), `install_dir` absolute, each `checksums` row a complete
64-hex digest, and `extra="forbid"` plus `resolve_vars`' binding report a key
that names no field. Outcomes that used to fail mid-play fail at resolution
or at the body's first wave: an unmapped `uname -m` and a version with no
pinned digest each raise `ValueError` before a single byte is fetched
(D4/D5).

## 5. Run it

The role is library code; `examples/roles/kubectl.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables, builds the deploy from the `kubectl` factory
and drives it with `run_waves` — a `dry=True` one-wave preview, then the
apply, then a converged rerun — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import KubectlVars, kubectl

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(KubectlVars, "kubectl", inventory)
    deploy = kubectl(vars_set)                                    # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)            # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/kubectl.py
```

The `dry=True` pass parks at the `Arch` gate (`barrier: Arch → unknown
until run`): the plan needs the machine's architecture before it can name
the URL, so the dry preview shows no request at all — like `ansible
--check` without facts. The rerun against a converged host is the honest
preview.

**Run the program twice.** The second run must report no change: the
directory exists, the version-suffixed binary's content matches the pin, and
the link already points at it. That is the migration's idempotence claim,
and the offline suite asserts it — including that the converged pass still
fetches the URL control-side to make the comparison (D3).

## 6. What it does

One deploy, exactly two waves on every non-dry pass:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(server.Arch)` | the role's `ansible_facts.architecture` read — the one barrier; its answer feeds `arch_for` and `resolve_checksum`, both fail-fast |
| 1 | `files.directory` + `files.download` + `files.link` requests | the role's three tasks in task order; the body ends, so the wave closes with the stream |

The wave count does not move with the host: fresh and converged passes both
run two waves, and a dry pass parks after wave 0. There is **no checkpoint
and no handler**: the role ships an empty `handlers/main.yml`, and nothing
between the tasks reads remote state. A host whose `host.family` is not
`"Ubuntu"` returns before its first `yield`, so it emits no requests, no
barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_kubectl.py`, against the simulated Ubuntu host
(`ubuntu`, `arch = "x86_64"`) plus a CentOS host the role never touches.
Fresh host, first pass (the inventory pins the test's canned bytes — see
the module docstring's D4; a real run uses the shipped table):

```text
(root)
  wave 0
    barrier: Arch → x86_64
  wave 1
    ubuntu: Ensure install directory /usr/local/bin exists. [changed: /usr/local/bin missing, will create]
        $ sudo mkdir -p /usr/local/bin
        $ sudo chmod 755 /usr/local/bin
    ubuntu: Download https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl [changed: content /usr/local/bin/kubectl-v1.37.2 is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /usr/local/bin && cp /tmp/reemote-install-…/content /usr/local/bin/kubectl-v1.37.2 && chmod 755 /usr/local/bin/kubectl-v1.37.2 && rm -rf /tmp/reemote-install-…'
    ubuntu: Link /usr/local/bin/kubectl-v1.37.2 to /usr/local/bin/kubectl. [changed: /usr/local/bin/kubectl missing, will create]
        $ sudo ln -s /usr/local/bin/kubectl-v1.37.2 /usr/local/bin/kubectl
```

The rerun — the `Arch` gate still closes wave 0, and all three requests
report no change:

```text
(root)
  wave 0
    barrier: Arch → x86_64
  wave 1
    ubuntu: Ensure install directory /usr/local/bin exists. [no change: directory /usr/local/bin already exists]
    ubuntu: Download https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl [no change: content already up to date (/usr/local/bin/kubectl-v1.37.2)]
    ubuntu: Link /usr/local/bin/kubectl-v1.37.2 to /usr/local/bin/kubectl. [no change: link /usr/local/bin/kubectl already exists]
```

The dry pass parks at the gate — one wave, no requests planned, nothing
fetched:

```text
(root)
  wave 0
    barrier: Arch → unknown until run
```

The CentOS host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The `include_vars` OS dispatch freezes to Ubuntu/linux: the `vars/Windows.yml` branch (with its `.exe` names) and the darwin table half are statically skipped, and `kubectl_os`/`kubectl_bin` stay module constants rather than variables | reemote drives Linux hosts only, and fixing one target collapses the dispatch to the family guard plus frozen constants |
| D2 | The binary is fetched **control-side** by `files.download` (verified, then spool-installed) | `get_url` downloads on the target; reemote's URL operations fetch control-side by design, so the control machine needs egress to the mirror and the host downloads nothing |
| D3 | Idempotence is content-only: a converged pass re-fetches the URL control-side to compare, and a pure permission drift on the installed binary is not re-applied | the install read-back short-circuits on matching content, as `get_url`'s own checksum comparison does; the pin makes the repeated fetch deterministic |
| D4 | The checksum table ships only the recent Linux releases (`LINUX_CHECKSUMS`, v1.36.x/v1.37.x); `checksums` extends or overrides it, and an unshipped version with no override raises `ValueError` before any fetch | the role's own default is a 4,300-line generated table; embedding the corpus is neither reviewable nor hashable-friendly, and the fail-fast reproduces Ansible's undefined-key error on `kubectl_checksums[ver]` |
| D5 | `kubectl_arch_map` freezes to `ARCH_MAP` (it was an overridable default); an unmapped `uname -m` raises | the map is upstream metadata, not a tuning knob; inventing a URL for an unmapped architecture is the one outcome the role never produced |
| D6 | `kubectl_privilege_escalate` maps to `sudo=` on each of the three requests | reemote has no play-level `become`; the run-as identity comes from the inventory `privilege` section, never from an operation argument |

## 9. Scoped out

* **Windows and macOS targets.** See D1 — the migration fixes Ubuntu as its
  target family; an off-target host is skipped silently rather than mapped.
* **The full `kubectl_checksums` history.** Only the recent releases ship in
  the table (D4); older or newer versions are adopted per inventory with a
  `checksums` row, which the offline suite exercises end to end.
* **`kubectl_arch_map`, `kubectl_os`, `kubectl_bin`.** Overridable defaults
  upstream; frozen here (D1/D5). A custom mirror *is* modelled (`mirror`),
  since that is the knob the role's own README recommends.
* **The `test.yml` smoke command** (`kubectl version --client`). It lives
  outside `tasks/main.yml` upstream, so it has no place in the role body; a
  program that wants it adds its own `act(Shell, ...)` after the deploy.

--->
