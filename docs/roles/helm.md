# helm — helm role

| | |
| --- | --- |
| Role | [`geerlingguy.helm`](https://github.com/geerlingguy/ansible-role-helm) |
| Module | [`reemote.roles.helm`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/helm.py) |
| Runner | [`examples/roles/helm.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/helm.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `helm` role mapping → `HelmVars` |

<!---
| Waves | 2 fresh (the `FileExists` gate, then the two requests) / 2 converged (the gate, then the version probe) / 3 with a stale binary — a dry pass parks at the gate, 1 wave |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed — nothing is templated) |
| Offline test | [`tests/roles/test_helm.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_helm.py) |
| Factory | plain (non-`async`) — the runner calls `helm(v)`; the tarball fetch lives *inside* `files.unarchive`, not in a `control` barrier |

`helm` installs [helm](https://helm.sh/), the Kubernetes package manager, on
Ubuntu hosts: it downloads the pinned release tarball, extracts the `helm`
binary from it, and copies that binary to the managed path — but only when the
binary is absent or reports a different version.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("helm", helm(v))(host)` and a run is one `run_waves` pass.
The Ansible role's whole task list is that single deploy, and its
`defaults/main.yml` is `HelmVars`.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars` OS dispatch (`vars/Darwin.yml`, `vars/Windows.yml`) | family guard + frozen Linux constants (D1); the URL/path formulas become `helm_url`, `staging_for`, `staged_binary` | deviation (D1) |
| `stat: path={{ helm_bin_path }}` + `register` | `yield fact(files.FileExists, bin_path)` | barrier |
| `command: {{ helm_bin_path }} version` + `register` + `failed_when: false` + `when` | `yield act(Shell, ...)` read as `helm_version in result["stdout"]`, only when the gate says the binary exists | deviation (D2) |
| `unarchive: src={{ helm_repo_path }}/helm-….tar.gz remote_src: yes dest=/tmp creates=…` | `files.unarchive(url, staging, creates=…, mode="0755", checksum="sha256:…")` — control-side fetch (D3), dedicated version-suffixed staging dir (D4) | deviation |
| the pinned `sha256` (absent upstream — the role verified nothing) | `HELM_CHECKSUMS` + the `checksums` var, `resolve_checksum` fail-fast before any fetch | deviation (D5) |
| `copy: src=<staged> dest={{ helm_bin_path }} remote_src: yes mode: '0755' become: true when: helm_download is changed` | `files.copy(staged, bin_path, overwrite=True, mode="0755", sudo=…)`; the `when:` collapses into copy's own sha1 content gate (D6), `become:` into `sudo=` (D7) | deviation |
| `handlers/main.yml` | empty upstream — no checkpoint, no handler | n/a |

The migration surfaced **no new library gaps**: `files.unarchive` (the
checksum-verified, control-side fetch-and-extract) and `files.copy` (the
`remote_src` rename) are the direct equivalents, and the `fact`/`act` barriers
carry the `stat` and version probe that used to come from Ansible's
`register`. The one behaviour the role lacked — verifying the download — is
**closed** rather than reproduced: the digest table fail-fasts (D5).

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

No `reemote[templates]` — the role renders nothing; the URL, the staging
directory and the member path are plain f-strings.

The tarball is fetched **control-side** by `files.unarchive`, so the control
machine needs outbound HTTPS to `get.helm.sh` (or the configured mirror); the
host downloads nothing. SSH access to each Ubuntu host with passwordless sudo
(or a `privilege` section in the inventory): the copy into `bin_path` carries
`sudo=hv.privilege_escalate`, because reemote has no play-level `become`. The
extraction into the staging directory stays unprivileged (it lands under
world-writable `/tmp`), exactly as the role's `unarchive` task did.

## 3. Variables

`HelmVars` is a frozen pydantic model whose fields carry bare names; the role
name passed to `resolve_vars` binds the entry's `helm` role mapping.

--->

::: reemote.roles.helm
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - helm

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `version` | `str` | `v4.3.2` | `helm_version` |
| `platform` | `str` | `linux` | `helm_platform` |
| `arch` | `str` | `amd64` | `helm_arch` |
| `repo_path` | `str` | `https://get.helm.sh` | `helm_repo_path` |
| `bin_path` | `str` | `/usr/local/bin/helm` | `helm_bin_path` |
| `staging_dir` | `str` | `/tmp` | *(the role's raw `/tmp`; D4)* |
| `privilege_escalate` | `bool` | `true` | *(the copy's `become`; D7)* |
| `checksums` | `tuple[HelmChecksum, ...]` | `()` | *(no upstream — D5)* |

<!---

Each `checksums` entry is a `HelmChecksum` row — the flat, hashable form of a
`(version, arch)` digest cell:

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | `str` | the release the digest belongs to (`v4.3.2` form) |
| `arch` | `str` | a `get.helm.sh` architecture name: `amd64`, `arm`, `arm64`, `s390x`, `ppc64le` |
| `sha256` | `str` | the published tarball's 64-character hex digest, no `sha256:` prefix |

Precedence is `model defaults < inventory role mapping < overrides`.
Validation is fail-fast, before any host is contacted: `version`, `platform`
and `arch` must be plain name components (non-empty, no `/`, no leading `.`),
`repo_path` non-empty (a trailing `/` is stripped), `bin_path` and
`staging_dir` absolute (trailing `/` stripped) with `bin_path` naming a file,
each `checksums` row a complete 64-hex digest, and `extra="forbid"` plus
`resolve_vars`' binding report a key that names no field. Outcomes that used to
fail mid-play fail at resolution or at the body's first step: an unshipped,
unpinned `(version, arch)` pair raises `ValueError` before a single byte is
fetched (D5).

Unlike `kubectl`, `arch` is a **plain default**, not a fact-derived value — the
role reads no `ansible_architecture`, so the body needs no architecture barrier.

## 5. Run it

The role is library code; `examples/roles/helm.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables, builds the deploy from the `helm` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the apply, then a converged
rerun — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import HelmVars, helm

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(HelmVars, "helm", inventory)
    deploy = helm(vars_set)                                       # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)            # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/helm.py
```

The `dry=True` pass parks at the `FileExists` gate (`barrier: FileExists →
unknown until run`): whether the host needs an install at all is a fact the dry
preview cannot know, so it shows no request — like `ansible --check` without
facts. The rerun against a converged host is the honest preview.

**Run the program twice.** The second run must report no change: the binary now
exists and `helm version` answers the pinned release, so the body returns
before emitting an install request — and the control-side fetch does not happen
a second time. That is the migration's idempotence claim, and the offline suite
asserts it (including that the *stale*-version branch re-installs, D2).

## 6. What it does

One deploy; the wave count moves with the host:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(files.FileExists)` | the role's `stat` of the binary path — the opening gate; its answer decides whether a version probe is worth running |
| 1 (fresh) | `files.unarchive` + `files.copy` requests | the gate said absent, so the install tasks run in task order; the body ends, so the wave closes with the stream |
| 1 (converged) | `act(Shell, "… version")` | the gate said present, so the version probe runs; a matching release returns and the wave ends with no request |
| 2 (stale) | `files.unarchive` + `files.copy` requests | the probe reported a different release, so the install tasks follow the probe's wave |

Fresh and converged passes both run two waves; a host whose binary reports the
wrong version runs three (the extra `act` closes a wave on its own). A dry pass
parks after wave 0. There is **no checkpoint and no handler**: the role ships no
`handlers/main.yml`, and nothing between the tasks needs a notification read. A
host whose `host.family` is not `"Ubuntu"` returns before its first `yield`, so
it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_helm.py`, against the simulated Ubuntu host (`ubuntu`)
plus a CentOS host the role never touches. Fresh host, first pass (the
inventory pins the test's canned bytes — see the module docstring's D5; a real
run uses the shipped table):

```text
(root)
  wave 0
    barrier: FileExists → False
  wave 1
    ubuntu: Unpack https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz into /tmp/helm-v4.3.2. [changed: /tmp/helm-v4.3.2 missing, will create; will extract …; will set mode 755 of /tmp/helm-v4.3.2 recursively]
        $ mkdir -p /tmp/helm-v4.3.2
        $ scp <files unarchive> -> /tmp/reemote-unarchive-…-helm-v4.3.2-linux-amd64.tar.gz
        $ tar -xz -f /tmp/reemote-unarchive-…-helm-v4.3.2-linux-amd64.tar.gz -C /tmp/helm-v4.3.2
        $ rm -rf /tmp/reemote-unarchive-…-helm-v4.3.2-linux-amd64.tar.gz
        $ chmod -R 755 /tmp/helm-v4.3.2
    ubuntu: Copy /tmp/helm-v4.3.2/linux-amd64/helm to /usr/local/bin/helm. [changed: will copy /tmp/helm-v4.3.2/linux-amd64/helm to /usr/local/bin/helm]
        $ sudo cp -r -f /tmp/helm-v4.3.2/linux-amd64/helm /usr/local/bin/helm
        $ sudo chmod 755 /usr/local/bin/helm
```

The rerun — now the binary exists, so wave 0's gate answers `True` and wave 1
is the version probe, which short-circuits the install. No request runs, so no
second fetch happens:

```text
(root)
  wave 0
    barrier: FileExists → True
  wave 1
    barrier: helm version → {'exit_status': 0, 'stdout': 'version.BuildInfo{Version:"v4.3.2", …}\n', 'stderr': ''}
```

The dry pass parks at the gate — one wave, no requests planned, nothing
fetched:

```text
(root)
  wave 0
    barrier: FileExists → unknown until run
```

The CentOS host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The `include_vars` OS dispatch freezes to Ubuntu/linux: the `vars/Darwin.yml`/`vars/Windows.yml` branches are statically skipped, `helm_platform` stays a module constant default, and an off-target host is dropped by the family guard | reemote drives Linux hosts only, and fixing one target collapses the dispatch to the guard plus frozen constants |
| D2 | The `stat` + `command … version` + `when` trio becomes a `fact(FileExists)` gate and, **only when the binary is present**, an `act(Shell)` probe whose `Result` the body reads; Ansible ran the version command unconditionally with `failed_when: false`, so a missing binary short-circuits to the install here — one fewer barrier | the barrier model makes both reads in-stream; skipping the guaranteed-to-fail probe is the same decision, cheaper |
| D3 | The tarball is fetched **control-side** by `files.unarchive` (verified, then spooled and extracted); Ansible's `unarchive` with `remote_src: true` downloads on the target | reemote's URL operations fetch control-side by design, so the control machine needs egress and the host downloads nothing |
| D4 | The extraction targets a dedicated, version-suffixed staging directory (`<staging_dir>/helm-<version>`) rather than the raw `/tmp` the role names, and `creates` is the staged binary | `unarchive`'s `mode` is a recursive `chmod -R` of `dest` (the issue #58 shape) and must not touch a shared directory; the version-scoped path means a version change re-extracts |
| D5 | Every `(version, arch)` must have a `sha256` digest, from the shipped `HELM_CHECKSUMS` table or a `checksums` override row; an unshipped, unpinned pair raises `ValueError` before any fetch | Ansible's role verified nothing — the pin is the one gap the migration **closes** rather than reproduces; an unverified download is the outcome this role refuses |
| D6 | `when: helm_download is changed` on the copy task collapses into `files.copy`'s own sha1 content gate | a staged binary already matching the destination reports no change without consulting the unarchive's result, so the register-and-when indirection is redundant |
| D7 | `helm_privilege_escalate` maps to `sudo=` on the copy request only (the extraction into `/tmp` stays unprivileged) | reemote has no play-level `become`; the run-as identity comes from the inventory `privilege` section, never from an operation argument |
| D8 | The default release is modernised from the role's 2020 `v3.2.1` to the current `v4.3.2`, and `HELM_CHECKSUMS` ships the recent Helm 4 and last Helm 3 releases rather than one old pin | a fresh install should land on a maintained release; the table is extendable per inventory (D5) |

## 9. Scoped out

* **Darwin and Windows targets.** See D1 — the migration fixes Ubuntu as its
  target family; an off-target host is skipped silently rather than mapped.
* **The historical `helm_version` set.** Only the recent releases ship in the
  table (D5/D8); older or newer versions are adopted per inventory with a
  `checksums` row, which the offline suite exercises end to end.
* **`helm_platform` and `helm_os` dispatch.** Overridable defaults upstream;
  frozen here (D1). A custom mirror *is* modelled (`repo_path`), since that is
  the knob the role's own README recommends.
* **The per-role CI playbook.** Upstream's `test.yml`/molecule config lives
  outside `tasks/main.yml`, so it has no place in the role body; a program that
  wants a post-install smoke adds its own `act(Shell, ...)` after the deploy.

--->
