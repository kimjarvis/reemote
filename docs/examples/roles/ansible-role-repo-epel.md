# EPEL repository Ansible role → reemote

| | |
| --- | --- |
| Role | [`geerlingguy.repo-epel`](https://github.com/geerlingguy/ansible-role-repo-epel) |
| Program | [`ansible-role-repo-epel.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-repo-epel/ansible-role-repo-epel.py) |
| Target | **RedHat** — hosts in the `"RedHat"` OS family (RHEL family) |
| Inventory | [`inventory.json`](https://github.com/kimjarvis/reemote/blob/main/inventory.json) at the project root |
| Scope | the role's whole default path — all four tasks |
| Offline test | [`tests/test_roles_ansible_role_repo_epel.py`](https://github.com/kimjarvis/reemote/blob/main/tests/test_roles_ansible_role_repo_epel.py) |

This program installs the EPEL (Extra Packages for Enterprise Linux)
repository the way the Ansible role does: import the EPEL GPG key, install the
`epel-release` RPM from the Fedora download URL, then set `enabled=` in the
`[epel]` section of `/etc/yum.repos.d/epel.repo` — preserving the RPM's other
sections, comments and options. It is a reemote **deploy program**, not a CLI
wrapper: the role's task graph becomes two deploys with a fact gather between
them and the control machine.

Every role variable is read from the inventory entry's extra keys (reemote's
`host_vars` mechanism), so `epel_repo_disable` and friends live beside
`host`/`connection` in `inventory.json`. The role's URLs interpolate
`ansible_facts.distribution_major_version`; that fact read moves to `main()`
(`LinuxDistribution`), so EPEL 7/8/9/10 resolve from one frozen table.

---

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Why | How it is supplied |
| --- | --- | --- |
| **reemote** | the library itself | `uv run` resolves the project environment (reemote is built from this source tree). `uv sync` if you want it prepared up front. |
| Python ≥ 3.13 | `requires-python` in `pyproject.toml` | uv picks the project's interpreter (`.venv`). |

**Nothing else.** No PyYAML, no Jinja2, no Ansible: the role has no templates,
and the two URL fetches use reemote's standard-library control-side helpers
(`files.fetch_url` for the armored key, `files.download` for the binary RPM).
reemote's own runtime dependencies — `asyncssh`, `distro`, `pydantic` — are
installed automatically.

### 1.2 Control machine

- **Outbound HTTPS to `dl.fedoraproject.org`.** The EPEL GPG key and the
  `epel-release` RPM are fetched **control-side** (see deviation D2); a
  network failure aborts the run before the corresponding host request.
- **SSH access** to each RedHat host with the key named in its `connection`
  (`~/.ssh/id_ed25519` for the shipped `web1`/`web2` entries).

### 1.3 Target host

- A RHEL-family distribution with `rpm`/`dnf` — the shipped RedHat-family
  hosts are CentOS Stream 9. `main()` verifies the gathered
  `LinuxDistribution` family is `RedHat` and fails fast otherwise.
- **Elevation.** Every request carries `sudo=True`. The login user needs
  passwordless sudo (`NOPASSWD`), or the inventory entry must supply a
  password via a `privilege` section — privilege settings come from the
  inventory, never from the program:

  ```json
  "privilege": { "sudo_password": "…" }
  ```

- The host itself needs **no internet egress** for this role: nothing is
  downloaded on the target (deviation D2).

### 1.4 Inventory: what you must add

**Nothing is mandatory.** The shipped `inventory.json` already has two
RedHat-family hosts (`web1`, `web2`); the role defaults apply as-is. (Their
`tags` still say `ubuntu_host`/`apt_package_manager` from an earlier demo —
tags are irrelevant here: the `when` guard reads the **family**, not tags.)

Role variables are optional extra keys on a RedHat-family entry, e.g. to disable
the repo after installing it:

```json
{
  "host": "web1",
  "family": "RedHat",
  "connection": {
    "address": "192.168.0.159",
    "username": "supervisor",
    "key_path": "~/.ssh/id_ed25519"
  },
  "group": "centos",
  "epel_repo_disable": true
}
```

The other entries (`alpine`, `ubuntu`) need no changes. They are not in the
RedHat family, so `when=is_redhat` skips them and **they are never
contacted** — a skipped request is not materialized at all.

---

## 2. Running it against `inventory.json` with uv

From the project root:

```bash
uv run python examples/roles/ansible-role-repo-epel/ansible-role-repo-epel.py
```

`uv run` builds/refreshes the project environment (reemote from source); no
`--with` flag is needed because the program has no extra dependencies. The
program reads `inventory.json` from the project root by path resolution
(`Path(__file__).resolve().parents[3]`), so it works from any working
directory as long as the file lives in the repository.

What you get, per deploy:

```
== Inventory loaded from /home/kim/reemote/inventory.json ==
alpine, web1, web2, ubuntu

== Skipped (when guard is_redhat): alpine, ubuntu ==
== Frozen EPEL plans ==
web1: EPEL 9 -> /etc/yum.repos.d/epel.repo (present), enabled=1
web2: EPEL 9 -> /etc/yum.repos.d/epel.repo (present), enabled=1

== Deploy 1: import the EPEL key and install epel-release -- plan (prepare phase, --dry) ==
…format_plan…
== Deploy 1: import the EPEL key and install epel-release -- run log (apply phase) ==
…format_log…
== Deploy 2: set the EPEL repo enabled flag -- plan (prepare phase, --dry) ==
…format_plan…
== Deploy 2: set the EPEL repo enabled flag -- run log (apply phase) ==
…format_log…

== Done: 0 changed request(s) across 2 deploys ==
```

Each deploy is **previewed then applied** in the same invocation:
`run(..., dry=True)` stops after the prepare phase (Ansible's `--check` as a
return value) and prints `format_plan`, then the real `run` prints
`format_log`. On a fresh host, deploy 1 imports the key, stages and installs
the RPM and removes the spool; deploy 2 then edits the file deploy 1 created.

**Run it twice.** The second invocation must report `0 changed request(s)`:
the repo file exists (the install guard skips), the key id is in the rpm
keyring, the `[epel]` enabled line matches and the mode matches. That is the
migration's idempotence claim.

Failures are typed and printed to stderr with exit status 1 — no traceback:

| Error | Meaning |
| --- | --- |
| `OSError` / `JSONDecodeError` | `inventory.json` missing/unreadable/not JSON, or a control-side fetch failed |
| `ValueError` | a role variable is invalid, the distribution is not RHEL-family, or the EPEL major has no pinned key id |
| `TypeError` | the gathered distribution facts carry no integer major version |
| `InventoryError` | the document parsed but violates the inventory schema (names the entry index) |
| `HostConnectionError` | SSH could not be established |
| `PrivilegeError` | sudo rejected the credential |
| `CommandError` | a command exited non-zero (fail-fast, names host + command + stderr) |

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_repo_epel.py -q   # plan → apply → no-op re-apply
uv run pytest tests/ -q                                       # whole suite
uv run ruff check .
```

---

## 3. Role variables

`role_settings(host)` freezes `ROLE_DEFAULTS ⊕ host.extra`:

| Variable | Default | Effect |
| --- | --- | --- |
| `epel_repo_url` | templated | the `epel-release` RPM URL; default `https://dl.fedoraproject.org/pub/epel/epel-release-latest-<major>.noarch.rpm` |
| `epel_repo_gpg_key_url` | templated | the GPG key URL; default `https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-<major>` |
| `epel_repofile_path` | `/etc/yum.repos.d/epel.repo` | the file the install guard stats and the enabled toggle edits |
| `epel_repo_disable` | `false` | `true` writes `enabled=0` in `[epel]` (the role's `ternary(0, 1)`); Ansible YAML booleans accepted |

`<major>` is the gathered `LinuxDistribution` major version. The pinned key
ids (the `dnf.key(keyid=…)` idempotence gate, verified against the published
armored keys):

| EPEL major | Key fingerprint | rpm short id |
| --- | --- | --- |
| 7 | `91E97D7C4A5E96F17F3E888F6A2FAEA2352C64E5` | `352c64e5` |
| 8 | `94E279EB8D8F25B21810ADF121EA45AB2F86D6A1` | `2f86d6a1` |
| 9 | `FF8AD1344597106ECE813B918A3872BF3228467C` | `3228467c` |
| 10 | `7D8D15CBFC4E62688591FB2633D98517E37ED158` | `e37ed158` |

A major outside this table is rejected before any host is contacted
(deviation D6).

---

## 4. Program shape: two deploys and their barriers

reemote runs a deploy **twice** (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role's
`stat:` + `register:` + `when:` guard reads mutable target state a deploy body
may not branch on, so it becomes **gather → snapshot → factory**.

```
load_inventory + validate_inventory ──► Host.from_inventory ──► targets = family "RedHat"
role_settings(host)                                   (no host contacted)
│
├─ gather: LinuxDistribution (family/major guard + URL resolution)
│          FileExists(epel_repofile_path)             ← the role's stat register
├─ files.fetch_url(key URL) for hosts whose repo file is absent
│          → one io.BytesIO per host, built ONCE      (control-side HTTPS)
│
├─ run 1  build_install(plans)    gated on frozen repo_exists=False:
│                                 dnf.key(keyid=…) → files.download(RPM → /tmp spool)
│                                 → dnf.rpm(spool) → files.file(spool, present=False)
│
└─ run 2  build_configure(plans)  every RedHat host, unconditional:
                                  files.ini_option([epel] enabled=<v>, create=False)
                                  → files.file(mode="0644")
```

Why this order:

- **install before configure** — on a fresh host the repo file does not exist
  until `epel-release` is installed, and `ini_option(create=False)` (the
  role's `ini_file` default) raises on an absent file. The edit must run in a
  later barrier.
- **the guard is frozen, not re-read** — `repo_exists` is gathered once in
  `main()`; both deploy phases branch on the same frozen boolean.
- **streams are built once** — a stream is hashable but compared by
  *identity*, so the key `io.BytesIO` is created in `main()` and closed over
  by the factory; a fresh stream per phase would trip `DeterminismError`.

---

## 5. Coverage table

Status vocabulary: **covered** (direct operation) · **recipe** (documented
shape change) · **deviation** (possible with a recorded semantic change).

| Ansible task | reemote target | Status |
| --- | --- | --- |
| `stat: path={{ epel_repofile_path }}` + `register` | `gather_fact(FileExists, …)` in `main()`, frozen into the plan | recipe |
| `rpm_key: key={{ epel_repo_gpg_key_url }} state=present` (`when: not …stat.exists`) | `files.fetch_url` control-side → frozen `io.BytesIO` → `dnf.key(src=stream, keyid=…, sudo=True)` in the gated deploy | covered (D2, D6) |
| `yum: name={{ epel_repo_url }} state=present` (same `when`) | `files.download(url, /tmp spool)` + `dnf.rpm(src=spool)` + `files.file(spool, present=False)` cleanup | deviation (D2, D3) |
| `ini_file: path=… section=epel option=enabled value={{ epel_repo_disable \| ternary(0,1) }} no_extra_spaces=true mode=0644` | `files.ini_option(section="epel", option="enabled", value=…, no_extra_spaces=True, create=False, mode="0644")` + `files.file(mode="0644")` | covered (D7) |
| `until` / `retries: 5` / `delay: 10` on the key/RPM tasks | dropped — fail-fast | deviation (D4) |
| `ignore_errors: "{{ ansible_check_mode }}"` | the `dry=True` preview never mutates | deviation (D5) |
| URL defaults interpolating `distribution_major_version` | `gather_fact(LinuxDistribution)` + frozen URL/key-id tables | recipe |
| play-level `become` | `sudo=True` per request; `privilege` from inventory | deviation (D8) |

The role has no handlers, no templates, no loops and no OS dispatch beyond
the major-version interpolation — all four tasks are migrated.

---

## 6. Implementation gaps found — both resolved

The migration proved two genuine library gaps, filed them, and both were
fixed on `main` before this program landed — so it uses the native operations
directly, with no workaround code left to retire:

| Issue | Gap | Fix |
| --- | --- | --- |
| [#24](https://github.com/kimjarvis/reemote/issues/24) | no section-scoped INI option edit (`ini_file` parity): `dnf.repo` constructs a whole single-section file (it would clobber the RPM's `[epel-debuginfo]`/`[epel-source]` sections), `files.edit` is line-scoped | `files.ini_option` (commit `91962d1`): pure section-scoped edit over `FileContents`, spool-and-install write, content-idempotent |
| [#25](https://github.com/kimjarvis/reemote/issues/25) | `dnf.key(present=True)` was stateless (always changed) despite the `RpmGpgKeys` fact — Ansible's `rpm_key state=present` is idempotent | `dnf.key(keyid=…)` gates the import on `RpmGpgKeys` (commit `17cf140`, the `gpg.import_key` precedent) |

---

## 7. Deliberate deviations from Ansible semantics

Every one is recorded in a comment at its site in the program.

| # | Deviation | Why |
| --- | --- | --- |
| D1 | Non-RedHat-family hosts are **skipped**, not failed | the migration targets one OS; the shared inventory holds Alpine and Ubuntu hosts that must not be touched |
| D2 | The key and RPM are fetched **control-side** (`files.fetch_url` / `files.download`); `rpm_key`/`yum` download on the target | no controller filesystem paths inside a deploy; reemote's URL operations fetch control-side by design. The control machine needs egress to `dl.fedoraproject.org`; the host needs none |
| D3 | `yum: name=<URL>.rpm` becomes download-to-spool + `dnf.rpm` + a spool-cleanup request | `dnf.rpm` installs a `.rpm` that already exists on the host (a URL `src` raises `ValueError`) — the documented staging boundary. The cleanup has no Ansible counterpart (yum leaves no local file) and keeps a later pass from installing a stale staged RPM |
| D4 | `until`/`retries: 5`/`delay: 10` are dropped | reemote is fail-fast with no per-request retry wrapper; a transient failure aborts the run — re-run to retry |
| D5 | `ignore_errors: "{{ ansible_check_mode }}"` is out of scope | the dry preview never mutates a host, so there is nothing to ignore |
| D6 | The key id is **pinned** per EPEL major; an unsupported major is rejected | `rpm_key` derives the id from the key material host-side; reemote's gate takes an explicit `keyid` (no control-side OpenPGP parsing). A custom `epel_repo_gpg_key_url` still imports correctly — the pinned id matches nothing and the import proceeds |
| D7 | `ini_option` idempotence is **content-only**; a companion `files.file(mode="0644")` converges mode drift | the install family's contract; the role's `ini_file: mode=0644` re-applies the mode every run |
| D8 | Every request carries `sudo=True` explicitly | the role relies on play-level `become`; reemote has none — run-as users/passwords come from the inventory `privilege` section |
| D9 | `dnf.rpm` is **version-aware**: a different installed `epel-release` version than the staged file's would attempt `rpm -i` (and fail), where `yum state=present` no-ops on any installed version | contained by the role's own guard: the install deploy only runs when the repo file is absent, and a host with `epel-release` installed ships that file. The pathological "file deleted, package installed at another version" state is unreachable in normal operation |

---

## 8. Offline verification

[`tests/test_roles_ansible_role_repo_epel.py`](https://github.com/kimjarvis/reemote/blob/main/tests/test_roles_ansible_role_repo_epel.py)
imports the example by path and replays `main()` barrier-for-barrier against
persistent `SimulatedHost`s — the real driver, prepare + apply, plus a
`dry=True` preview per deploy. It proves: the first pass installs (key
imported, RPM staged/installed, spool cleaned, repo file present), non-RedHat
hosts are never touched, **the second pass reports `changed=False`
everywhere and fetches nothing**, a converged host is a full no-op, a dry
preview mutates nothing, the keyid gate skips an already-imported key, the
disable flag rewrites **only** the `[epel]` enabled line (the real
three-section `epel.repo` fixture, captured from a live CentOS Stream 9 host,
comes back byte-identical apart from that one line), and content/mode drift
each converge once.

Three simulation limits are bridged explicitly (each a simulation limit, not
a library bug): `rpm_files` seeds the staged RPM's metadata (the simulation
parses no rpm binary); the RPM *payload* — the repo file a real
`epel-release` install writes — is planted between the two deploys; and the
keyid-gate test seeds `rpm_gpg_keys` with the real EPEL 9 short id (the
import seam appends a content-hash stub). The control-side fetches are
answered by monkeypatching `files._fetch_url_bytes`; the RPM body is
deliberately non-UTF-8 to drive the binary install branch.

`tests/test_examples.py` also scans the program statically (no
mutable-container literals in `op()`) and drains both factories with
`SNAPSHOT_ARGS` fixtures.

**Not yet run against a real host.** Doing so mutates the target and needs
explicit approval; the recipe is [§2](#2-running-it-against-inventoryjson-with-uv)
— run it, inspect the plans, then run it again and confirm
`0 changed request(s)`. (Both shipped RedHat hosts already have
`epel-release-9-11.el9` installed, so the expected outcome there is a full
no-op.)