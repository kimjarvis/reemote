# Drush Ansible role → reemote

This example migrates the [geerlingguy.drush](https://github.com/geerlingguy/ansible-role-drush)
Ansible role to a Python program built on the reemote library
([`ansible-role-drush.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-drush/ansible-role-drush.py)).

The role installs [Drush](https://www.drush.org/), the Drupal command-line
shell. Its default path installs the **Drush launcher**: a small phar placed
at `/usr/local/bin/drush` that finds and delegates to a project's real Drush.
Five tasks: check the path's current state (`stat` + `register`), remove an
old symlink, ensure the directory exists, download the launcher phar
(`get_url`), and make it executable.

**This migration targets Ubuntu only.** Hosts in the inventory group
`"ubuntu"` execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (Alpine,
CentOS) are skipped without being contacted. The role's two opt-in branches —
`drush_composer_global_install` and `drush_install_from_source`, both
default-false — are scoped out (§9), so the launcher path is the whole
migration.

The migration is complete: every task on the launcher path maps to a covered
operation. The one implementation gap it originally found (a binary download
into a root-owned path) was filed as
[issue #23](https://github.com/kimjarvis/reemote/issues/23) and is now
**closed on main** — the program uses the fix directly, with no workaround
left in the code (§6, D2).

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| PHP (`php-cli`) | target host | needed to **run** drush — the launcher is a phar that executes `php`. The role does not install PHP either (its README lists it as a prerequisite); this program does not install it |

**reemote is the only control-side package.** No template engine, no YAML
library: the role's only Jinja2 interpolation is the phar URL's version
substitution, which `resolved_vars()` rebuilds with an f-string, and the
inventory is read with the standard library's `json`. The phar fetch inside
`files.download` uses the standard library too.

The control machine needs **internet egress to `github.com`**: the launcher
phar is fetched control-side as binary bytes by `files.download`, spooled to
the host's `/tmp` and installed with an elevated copy (D1). The host itself
never downloads anything.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.

### 1.3 Target host

- Ubuntu, reachable over SSH as a user that can `sudo` (the inventory's
  `privilege` section carries any run-as user/password — never the program).
  The stock inventory entry has no `privilege` section, so passwordless sudo
  is assumed.
- PHP must already be installed for `drush` to run (§1.1). The launcher
  install itself needs nothing beyond a stock Ubuntu userland (`sha1sum`,
  `cp`, `chmod` — all present). The demo host had no PHP at all — see §8
  for the symptom and the one-line remedy.

### 1.4 Inventory: what you must add

The program loads the project-root
[`inventory.json`](https://github.com/kimjarvis/reemote/blob/main/inventory.json).
A target entry needs `"group": "ubuntu"`; every role variable rides beside
`host`/`connection` as an extra key — reemote's `host_vars` mechanism
(`host.extra`):

```json
{
  "host": "ubuntu",
  "family": "Ubuntu",
  "connection": {
    "address": "192.168.0.181",
    "username": "supervisor",
    "key_path": "~/.ssh/id_ed25519"
  },
  "group": "ubuntu",
  "tags": ["ubuntu_host", "apt_package_manager"],
  "drush_launcher_version": "0.10.1"
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape; the stock `ubuntu` entry in `inventory.json` already
works as-is.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run python examples/roles/ansible-role-drush/ansible-role-drush.py
```

No `--with` is needed — reemote is the only dependency (§1.1). The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the entry's
   extra keys) and fails fast on an invalid value — before any host is
   contacted; hosts with `drush_launcher_install: false` are named and
   skipped;
3. gathers `FileLink` and `File` on the launcher path — the role's `stat`
   register — and freezes `is_symlink` / `dest_exists` into each host's plan;
4. runs **the one deploy** (`build_launcher`): the symlink cleanup when
   needed, `files.directory` for the path's directory, `files.download` for
   the phar (binary, sha1-idempotent, mode 0755), and `files.file(mode="0755")`
   for an already-present destination;
5. prints the deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log, then
   the total changed count.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes — the directory matches, the phar's sha1
matches (`content already up to date`), and the mode matches. The phar is
still fetched control-side on every pass (the sha1 comparison must read the
body), but nothing on the host changes.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_drush.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7).

## 3. Role variables

Every launcher variable from `defaults/main.yml` is honoured under its
original name, read from the inventory entry's extra keys.

| Variable | Default | Meaning |
| --- | --- | --- |
| `drush_launcher_install` | `true` | whether the launcher path runs at all (YAML 1.1 booleans accepted: `yes`/`no`/`on`/`off`/`1`/`0`) |
| `drush_launcher_version` | `"0.10.1"` | the launcher release; fixes the phar URL |
| `drush_launcher_phar_url` | `null` → the release URL for the version | an explicit URL wins over the version (Ansible's variable precedence) |
| `drush_launcher_path` | `"/usr/local/bin/drush"` | where the launcher lands; must be absolute. Its dirname is the directory task's path |

Invalid values (a non-boolean flag, an empty or relative path, a non-string
URL) raise before any host is contacted. The composer/source branch
variables (`drush_composer_*`, `drush_source_*`, `drush_install_from_source`,
…) are scoped out (§9) and ignored.

## 4. Program shape: one deploy and its one barrier

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role's
`stat:` + `register: drush_path_state` + `when: …islnk` construct reads
mutable target state a deploy body may not branch on, so it becomes a
**gather → snapshot → factory** step:

```
main()
 ├─ load + validate inventory.json; resolve role_settings per target
 │    (drush_launcher_install false → host skipped, no plan)
 ├─ gather_fact(FileLink, path)        the stat's islnk gate: the symlink
 │                                     target, or None
 ├─ gather_fact(File, path)            regular-file metadata, False for a
 │                                     non-file, None when absent
 │                                     (hosts closed manually afterwards)
 │    → frozen plan per host: {version, phar_url, path, path_dir,
 │                             is_symlink, dest_exists}
 └─ deploy: build_launcher(plans)      [is_symlink] files.file(present=False,
 │                                       force=True)
 │                                     files.directory(path_dir, 0755)
 │                                     files.download(phar_url, path, 0755)
 │                                     [dest_exists] files.file(path, 0755)
```

The barrier is load-bearing: the stat reads happen **before** the deploy
because they select the cleanup and executable-bit requests. The content
gate needs no snapshot at all — `files.download` resolves it in-operation
through the `RemoteFileSha1` read-back, so a converged host's download is a
no-op in both phases. The determinism guard compares *request sequences*,
not commands: the same frozen plan yields the same requests in prepare and
apply even when the commands differ (prepare plans the install against the
pre-state; apply re-materializes after the cleanup removed the link).

One edge worth knowing: a symlink whose *target* already holds the exact
phar bytes plans a no-op download in prepare (the sha1 read follows the link)
but installs in apply (the cleanup removed the link first). The request
sequence still matches, so the run is legal — only the dry preview is
optimistic about that one request.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_tasks: install-drush-launcher.yml` `when: drush_launcher_install` | the flag gates plan creation in `main()` — a disabled host yields no requests | covered |
| 2 | `include_tasks: install-drush-composer.yml` `when: drush_composer_global_install` (default false) | scoped out (§9) | out of scope |
| 3 | `include_tasks: install-source.yml` `when: drush_install_from_source` (default false) | scoped out (§9) | out of scope |

### `tasks/install-drush-launcher.yml` → the one deploy

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 4 | `stat: path={{ drush_launcher_path }}` `register: drush_path_state` | `gather_fact(FileLink)` + `gather_fact(File)` before the deploy, frozen into the plan (D5) | recipe |
| 5 | `file: state=absent` `when: drush_path_state.stat.islnk` | `files.file(present=False, force=True)` when the frozen `is_symlink` is set (D6) | covered |
| 6 | `file: path={{ … \| dirname }} state=directory mode=0755` | `files.directory(path_dir, mode="0755")` | covered |
| 7 | `get_url: url={{ drush_launcher_phar_url }} dest={{ drush_launcher_path }}` | `files.download(url, dest, mode="0755")` — binary spool-and-install, sha1-idempotent (D1–D4; issue #23, closed) | covered |
| 8 | `file: path={{ drush_launcher_path }} mode=0755` | `files.file(path, mode="0755")` when the frozen `dest_exists` is set — metadata convergence the content-only install gate never does (D3) | covered |

The role ships no handlers, no templates, no `vars/` dispatch and no loops;
`meta/main.yml` declares no role dependencies (PHP is a README prerequisite,
not a meta dependency).

## 6. Deliberate deviations from Ansible semantics

- **D1 — control-side fetch.** `get_url` downloads on the target;
  `files.download` fetches control-side and spools the bytes to the host's
  `/tmp` (unprivileged scp), then installs with an elevated `cp`/`chmod`.
  Transfers never elevate in reemote — the spool-and-install family is the
  sanctioned shape for a root-owned destination. The control machine needs
  egress to `github.com`; the host needs none.
- **D2 — sha1 content idempotence (issue #23, closed).** `get_url` with the
  default `force: no` skips an *existing* destination regardless of content;
  `files.download` compares `RemoteFileSha1` — equal bytes no-op, a drifted
  binary converges to the release content once. This is strictly more
  convergent than the role. The gap this migration originally found (no
  binary spool-and-install operation) was filed as
  [issue #23](https://github.com/kimjarvis/reemote/issues/23) and closed by
  commits `134a122` + `da8a623`: `files.install` now accepts
  `content: str | bytes`, and `files.download` passes a non-UTF-8 body
  through as bytes. The former two-deploy `files.put` + `files.move`
  workaround is gone. **Caveat the fix documents:** the binary sha1
  read-back is unprivileged (`sha1sum` as the login user), so a
  login-unreadable destination would re-install every deploy — the
  launcher's 0755 mode keeps it login-readable.
- **D3 — mode at install time, plus a convergence request.** The role
  downloads with default permissions and chmods afterwards; this migration
  passes `mode="0755"` to `files.download` (applied on every write) and keeps
  the role's final task as `files.file(mode="0755")` for an already-present
  destination — install/download idempotence is content-only and never
  compares mode, so a drifted executable bit needs its own request.
- **D4 — no checksum pin.** The role sets none, and GitHub release assets
  are immutable. Without a pin the request sequence is still deterministic
  (the fetch result never reaches a request argument); a pin would only
  guard against the URL body changing mid-run.
- **D5 — `stat` register reconstructed control-side.** `register` +
  `when` on mutable target state is the documented gather → snapshot →
  factory shape change: `FileLink` answers the `islnk` gate, `File` answers
  whether a regular file already sits at the path, both frozen before the
  deploy.
- **D6 — symlink cleanup uses `force=True`.** The `File` fact reads a
  symlink as a non-file, so `files.file(present=False)` would raise without
  it; `force=True` removes the link itself — what Ansible's `state: absent`
  does to a symlink.
- **D7 — explicit per-request `sudo=True`.** The role relies on play-level
  `become`; reemote flags each request. Run-as users/passwords come from the
  inventory `privilege` section, never from an operation.
- **D8 — check mode.** reemote's `run(dry=True)` is Ansible's `--check` as a
  return value; the program performs a dry preview before the apply. The
  one optimistic edge (a symlink pointing at identical phar bytes) is noted
  in §4.

## 7. Offline verification

[`tests/test_roles_ansible_role_drush.py`](https://github.com/kimjarvis/reemote/blob/main/tests/test_roles_ansible_role_drush.py)
imports the example by path (its file name is not a valid module identifier),
never calls its `main()`, and replays the orchestration barrier-for-barrier
against persistent `SimulatedHost` objects (`host_factory` returns the *same*
host every run, so pass 2 observes pass 1). It proves:

- the first pass fetches the phar control-side and installs it binary at
  `/usr/local/bin/drush` with `root:root 0755`, cleaning up the `/tmp` spool;
- a non-Ubuntu host in the same inventory is never touched;
- **the second pass changes nothing** — the idempotence claim: the directory
  matches, the download reports `content already up to date`, the mode
  matches;
- the `dry=True` preview mutates nothing (the control-side fetch still
  happens — prepare must read the body to plan the sha1 comparison);
- a symlink pre-state is removed (the link itself, not its target) and the
  launcher installed in its place; the second pass is a no-op;
- content drift re-installs once (sha1-idempotent, not exists-idempotent);
  mode drift with equal content converges through the executable-bit
  request while the download no-ops;
- a missing directory is created 0755; `drush_launcher_install: false`
  deploys nothing and fetches nothing; a custom path/version resolves the
  URL and directory correctly;
- the pure helpers (`resolved_vars` defaults/overrides, `role_settings`
  validation, YAML 1.1 boolean handling) are unit-tested directly.

The phar fetch is answered offline by monkeypatching `files._fetch_url_bytes`
— the same seam the library's own drush acceptance test uses
(`tests/test_files_download.py`). **No between-pass bridge is needed**: the
`install_file` seam is bytes-generic (it stores the spooled bytes at the
destination and writes the `file_stats` row the `File` fact reads), so pass 2
observes pass 1 through the simulation's own mutators. The remaining seeding
choices are documented in the test docstring.

## 8. Verified on the demo ubuntu host

Run against the shared `ubuntu` demo host (`192.168.0.181`) on 2026-11-16
with the stock inventory entry (role defaults, no extra keys):

- **Run 1:** the plan froze `(absent)`; `Install Drush.` spooled the binary
  phar unprivileged and installed it with the elevated `cp`/`chmod` —
  **1 changed request**.
- **Run 2:** the plan froze `(present)`; `Install Drush.` reported
  `content already up to date (/usr/local/bin/drush)`, and the directory and
  executable-bit requests were no-ops — **0 changed requests**.
- On the target: `/usr/local/bin/drush` is `mode=755 owner=root:root
  size=298469`, and `drush --version` prints
  `Drush Launcher Version: 0.10.1`.

**PHP prerequisite: install `php-cli` before `drush --version` works.** The
launcher is a phar with a `#!/usr/bin/env php` shebang, and the demo host had
no PHP at all — the role does not install PHP (§1.1). The launcher *install*
succeeds without PHP, but running it fails with
`/usr/bin/env: 'php': No such file or directory` (exit 127). One-line remedy:

```bash
ssh supervisor@192.168.0.181 'sudo apt-get install -y php-cli'
```

This is host state, not a program defect — the migration installs the
launcher faithfully, and PHP is the role's documented prerequisite. With
`php-cli` installed (PHP 8.3.6), `drush --version` prints the launcher version
and then the notice `The Drush launcher could not find a Drupal site to
operate on` with **exit 1** — expected outside a Drupal project: the
launcher's job is to delegate to a site-local Drush, and the demo host has no
Drupal site. From inside a Drupal project (or with `--root=/path/to/drupal`)
it delegates instead.

## 9. Scoped out

- **The composer-global branch** (`drush_composer_global_install`, default
  false): installs Drush via a global `composer require` and symlinks
  `~/.config/composer/vendor/bin/drush`. Composer is a language package
  manager reemote does not ship; the branch would migrate as `Shell` actions
  between deploys. Opt-in, so it never blocks the default path.
- **The build-from-source branch** (`drush_install_from_source`, default
  false): `git clone` + composer install + a symlink. Same boundary — git
  checkout is covered (`git.repo`) but the composer install is an action.
- **Installing PHP** — the role's README prerequisite, not a role task
  (§1.3).

Both opt-in branches were a Phase 0 scope decision (launcher-only), not
implementation gaps; no tickets were filed for them.