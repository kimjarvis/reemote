# ansible-role-composer

This example migrates the [geerlingguy.composer](https://github.com/geerlingguy/ansible-role-composer)
Ansible role to a Python program built on the reemote library
([`ansible-role-composer.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-composer/ansible-role-composer.py)).

The role installs [Composer](https://getcomposer.org/), the PHP dependency
manager. Its default path verifies and downloads the official installer
(`uri` signature + `get_url` with a `sha384:` pin), runs it with PHP, moves
the resulting `composer.phar` to `/usr/local/bin/composer`, ensures the
`~/.composer` home directory, optionally writes a GitHub OAuth `auth.json`,
globally requires a configured package list, and adds the global `vendor/bin`
directory to every user's `PATH` through `/etc/profile.d/composer.sh`.

**This migration targets Ubuntu only.** Hosts in the inventory group
`"ubuntu"` execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (Alpine,
CentOS) are skipped without being contacted. The role's two opt-in branches —
`composer_keep_updated` (self-update) and `composer_add_project_to_path`
(project `vendor/bin` on `PATH`), both default-false — are scoped out (§8).

The migration is complete: every task on the default path maps to a covered
operation or a documented recipe, and **no implementation gap remains open**.
The three library gaps this role's shape once hit are all closed on main and
used directly, with no workaround left in the code:
[issue #20](https://github.com/kimjarvis/reemote/issues/20) (control-side
`uri` content fetch → `files.fetch_url`),
[issue #22](https://github.com/kimjarvis/reemote/issues/22) (remote-copy
parity → `files.copy`'s rename form) and
[issue #23](https://github.com/kimjarvis/reemote/issues/23) (binary
spool-and-install → `files.download`).

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| PHP (`php-cli`, ≥ 5.4) | target host | needed to **run** the installer and Composer itself. The role does not install PHP either (its README lists it as a prerequisite); this program does not install it |
| git | target host | Composer needs it to install packages from source dists. A README prerequisite of the role, not installed here |

**reemote is the only control-side package.** No template engine, no YAML
library: the role's two templates are a plain JSON object (`auth.json.j2`,
rendered with the standard library's `json.dumps`) and a one-line `export`
(`composer.sh.j2`, an f-string), and the inventory is read with `json`. The
signature/installer fetches use the standard library too.

The control machine needs **internet egress to `composer.github.io` and
`getcomposer.org`**: the installer signature is fetched control-side by
`files.fetch_url` and the installer by `files.download`, spooled to the
host's `/tmp` (D1). The *target* needs egress to
`packagist.org`/`github.com` **only when global packages are configured** —
the `composer global require` action runs Composer on the host, and Composer
downloads the packages itself.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.

### 1.3 Target host

- Ubuntu, reachable over SSH as a user that can `sudo` (the inventory's
  `privilege` section carries any run-as user/password — never the program).
  The stock inventory entry has no `privilege` section, so passwordless sudo
  is assumed.
- PHP must already be installed (§1.1). The install itself needs nothing
  beyond a stock Ubuntu userland (`sha1sum`, `cp`, `chmod`, `test` — all
  present).
- The role's `become_user: {{ composer_home_owner }}` maps to the inventory
  `privilege` section (D2): the default owner `root` is sudo's own default
  target; a **non-root** `composer_home_owner` needs
  `"privilege": {"sudo_user": "<owner>"}` on the entry.

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
  "composer_global_packages": [
    {"name": "phpunit/phpunit", "release": "@stable"}
  ],
  "composer_github_oauth_token": "ghp_yourtoken"
}
```

With no extra keys the demo defaults apply (§3) — the entry above only
illustrates the shape; the stock `ubuntu` entry in `inventory.json` already
works as-is.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run python examples/roles/ansible-role-composer/ansible-role-composer.py
```

No `--with` is needed — reemote is the only dependency (§1.1). The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the entry's
   extra keys) and fails fast on an invalid value — before any host is
   contacted;
3. gathers `FileExists(composer_path)` (the role's `stat` register),
   `server.Home(composer_home_owner)` (the `~` expansion) and
   `FileExists(home/vendor/<name>)` per configured package (the `creates=`
   gate), and freezes them into each host's plan;
4. fetches the installer signature **once** control-side, gated on any host
   needing the install (D4);
5. runs **deploy 1** (`build_download`): the sha384-verified installer
   download, then the **installer Shell action** on each needing host;
6. runs **deploy 2** (`build_configure`): the phar copy into place plus its
   `/tmp` cleanup (D5), the composer home directory, and `auth.json` when a
   token is configured — then one **global-require Shell action** per
   missing package;
7. runs **deploy 3** (`build_profile`): the `/etc/profile.d/composer.sh`
   PATH entry, when packages are configured and `composer_add_to_path` is
   true;
8. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log, then
   the total changed count.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes and fires no action — the composer binary
exists (the stat gate skips the whole installer half, signature fetch
included), the vendor directories exist (the `creates=` gate), and the
home/auth/profile content matches.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_composer.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7).

## 3. Role variables

Every default-path variable from `defaults/main.yml` is honoured under its
original name, read from the inventory entry's extra keys.

| Variable | Default | Meaning |
| --- | --- | --- |
| `composer_path` | `"/usr/local/bin/composer"` | where the phar lands; must be absolute. Its parent directory must exist (the role's `mv` assumes the same) |
| `composer_keep_updated` | `false` | scoped out (§8); `true` fails fast (D7) |
| `composer_version` | `""` | an exact version pin, applied as the installer's `--version=` flag **only when `composer_version_branch` is `""`** (the role's own `{% if %}…{% elif %}` precedence) |
| `composer_version_branch` | `"--2"` | the installer's major-version branch flag; wins over `composer_version` |
| `composer_home_path` | `"~/.composer"` | the composer home; `~` expands to `composer_home_owner`'s home (gathered via `server.Home`), an absolute path is used as-is |
| `composer_home_owner` | `"root"` | the home's owner; a non-root owner needs the inventory `privilege.sudo_user` (D2) |
| `composer_home_group` | `"root"` | the home's group |
| `composer_global_packages` | `(("phpunit/phpunit", "@stable"),)` | **demo default (D3)**: mirrors the role's molecule converge so the global-require path is exercised; the role's own default is `[]`. Accepts the JSON list-of-dicts shape (`{"name": …, "release": …}`, release defaults to `@stable`) |
| `composer_add_to_path` | `true` | whether `/etc/profile.d/composer.sh` is written (only reached when packages are configured, as in the role) |
| `composer_add_project_to_path` | `false` | scoped out (§8); `true` fails fast (D7) |
| `composer_github_oauth_token` | `""` | when non-empty, `auth.json` is installed into the composer home |
| `php_executable` | `"php"` | the role's `set_fact` default; the PHP binary that runs the installer |

Invalid values (a non-boolean flag, an empty or relative path, a malformed
package list) raise before any host is contacted. YAML 1.1 booleans
(`yes`/`no`/`on`/`off`/`1`/`0`) are accepted for the flags.

## 4. Program shape: three deploys, two action barriers

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role's
`stat:` + `register:` + `when:`, its `creates=` gates and its `command:`
tasks all read or mutate state a deploy body may not touch, so they become
**gather → snapshot → factory** steps and **Shell actions between deploys**:

```
main()
 ├─ load + validate inventory.json; resolve role_settings per target
 ├─ gather_fact(FileExists, composer_path)   the stat register's exists gate
 │   gather_fact(server.Home, home_owner)    tilde-expansion of ~/.composer
 │   gather_fact(FileExists, home/vendor/…)  the global require creates= gate
 │                                           (hosts closed manually afterwards)
 ├─ [any host needs install]
 │   files.fetch_url(installer.sig)          the uri task (#20), frozen (D4)
 ├─ deploy 1: build_download(plans)          files.download(installer, 0755,
 │                                             checksum="sha384:<sig>")
 ├─ ACTION: Shell "cd /tmp && php composer-installer.php --2"   (per host)
 ├─ deploy 2: build_configure(plans)         [need_install] files.copy(phar →
 │                                             composer_path, 0755) +
 │                                             files.file(phar, present=False)
 │                                           files.directory(home, 0755, owner)
 │                                           [token] files.install(auth.json)
 ├─ ACTION: Shell "composer global require …" env COMPOSER_HOME, sudo
 │                                             (per missing package)
 └─ deploy 3: build_profile(plans)           [packages + add_to_path]
                                             files.install(composer.sh, 0644)
```

The barriers are load-bearing:

- `files.copy` validates its **source in both phases**, so the installer
  action must have produced `/tmp/composer.phar` before deploy 2 begins —
  the create-then-copy span is two deploys with an action between them.
- The global-require actions need the composer binary deploy 2 installed,
  and deploy 3's profile entry follows the require loop exactly as the
  role's `global-require.yml` orders it.
- A `command:` task's exit status is **data** on the returned `Result`; the
  program interprets it and raises `CommandError` on failure, so a broken
  installer or require never silently feeds the next deploy.

The content gates need no snapshot at all — `files.download`/`files.install`
resolve them in-operation through their read-back facts, and `files.copy`
through its sha1 comparison, so a converged host's requests are no-ops in
both phases.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `fail:` when `composer_version != '' and composer_keep_updated` | fail-fast `ValueError` in `role_settings`, before any host is contacted (subsumed by D7's `composer_keep_updated` rejection, with the role's own message when a version is also pinned) | covered |
| 2 | `set_fact: php_executable=php` when undefined | `ROLE_DEFAULTS["php_executable"]` overlaid with `host.extra` | covered |
| 3 | `stat: path={{ composer_path }}` `register: composer_bin` | `gather_fact(FileExists, composer_path)` frozen into the plan's `need_install` | recipe |
| 4 | `uri: url=…/installer.sig return_content=true` `when: not …exists` | `await files.fetch_url(SIGNATURE_URL)` in `main()`, gated on any host needing the install, frozen into every plan (D1, D4; issue #20, closed) | covered |
| 5 | `get_url: url=getcomposer.org/installer dest=/tmp/composer-installer.php mode=0755 checksum="sha384:{{ sig }}"` | `files.download(url, dest, mode="0755", checksum=f"sha384:{sig}")` — the pin makes the two-phase fetch deterministic (D1, D6; issue #23, closed) | covered |
| 6 | `command: {{ php_executable }} composer-installer.php …  chdir=/tmp` | `Shell` action `cd /tmp && php composer-installer.php --2` between deploys 1 and 2 (no `chdir` parameter — inlined as a `cd` prefix); `Result` interpreted, `CommandError` on failure | recipe |
| 7 | `command: mv /tmp/composer.phar {{ composer_path }} creates={{ composer_path }}` | `files.copy(src, dest, mode="0755", overwrite=True)` + paired `files.file(present=False)` cleanup in deploy 2 (D5; issue #22, closed); the `creates=` gate is the frozen `need_install` | covered |
| 8 | `command: … self-update` `when: composer_keep_updated` (default false) | scoped out (§8, D7) | out of scope |
| 9 | `file: state=directory path={{ composer_home_path }} owner/group mode=0755` `become_user` | `server.Home(owner)` gather + control-side tilde expansion, then `files.directory(home, mode="0755", user=owner, group=group, sudo=True)` (D2) | covered |
| 10 | `template: auth.json.j2` `when: token \| length > 0` | `json.dumps({"github-oauth": {"github.com": token}}, indent=4) + "\n"` rendered in `resolved_vars`, installed with `files.install(mode="0644", user/group)` (D8) | covered |
| 11 | `include_tasks: global-require.yml` `when: composer_global_packages \| length > 0` | the package list gates the require actions and deploy 3 in `main()` | covered |
| 12 | `include_tasks: project-bin.yml` `when: composer_add_project_to_path` (default false) | scoped out (§8, D7) | out of scope |

### `tasks/global-require.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 13 | `command: {{ composer_path }} global require {{ item.name }}:{{ item.release \| default('@stable') }} … creates={{ home }}/vendor/{{ item.name }}` `environment: COMPOSER_HOME` `with_items` | per-package `FileExists(home/vendor/name)` gather frozen into `require`; one `Shell` action per missing package with `env={"COMPOSER_HOME": home}`, `sudo=True`; the loop's dicts flatten to `(name, release)` tuples (hashability) | recipe |
| 14 | `template: composer.sh.j2 → /etc/profile.d/composer.sh mode=0644` `when: composer_add_to_path` | f-string render with the resolved home (D8), `files.install(dest="/etc/profile.d/composer.sh", mode="0644")` in deploy 3, after the require actions | covered |

The role ships no handlers, no `vars/` OS dispatch and no role dependencies
(`meta/main.yml` `dependencies: []`; git/PHP are README prerequisites, not
meta dependencies).

## 6. Deliberate deviations from Ansible semantics

- **D1 — control-side fetch.** `uri`/`get_url` fetch on the target;
  `files.fetch_url`/`files.download` fetch control-side and spool the bytes
  to the host's `/tmp`. The control machine needs egress to
  `composer.github.io` and `getcomposer.org`; the host never downloads the
  installer. (The global-require step *does* download on the host — that is
  Composer itself running, exactly as in the role.)
- **D2 — explicit elevation.** Every elevated request carries `sudo=True`;
  the role relies on play-level `become` and `become_user:
  {{ composer_home_owner }}`. Run-as users/passwords come from the inventory
  `privilege` section, never from an operation — a non-root
  `composer_home_owner` needs `privilege.sudo_user`.
- **D3 — demo package default.** `composer_global_packages` defaults to the
  role's molecule converge (`phpunit/phpunit @stable`) so the global-require
  path is demonstrated out of the box; the role's own default is `[]`. Set
  `"composer_global_packages": []` in the inventory for the role-faithful
  no-packages behaviour.
- **D4 — one signature fetch.** Ansible's `uri` task runs per host; the
  program fetches the signature once control-side, gated on any host needing
  the install, and freezes it into every plan. The checksum pin then makes
  every per-phase installer fetch byte-identical.
- **D5 — mv becomes copy + cleanup.** `files.copy` (issue #22) renames the
  phar into place sha1-idempotently but **keeps the source**; the paired
  `files.file(present=False)` request reproduces `mv`'s source consumption.
  `overwrite=True` is safe because the frozen exists gate means the
  destination was absent at gather time.
- **D6 — unprivileged installer.** The role's `get_url` and installer
  `command` tasks carry no `become` and write only to `/tmp`; the download
  and the installer action run as the login user. Only the move into
  `/usr/local/bin` and the home/profile writes elevate.
- **D7 — opt-in branches fail fast.** `composer_keep_updated: true` and
  `composer_add_project_to_path: true` raise `ValueError` before any host is
  contacted (§8). The role's `fail:` xor gate is subsumed: its message is
  reproduced when a version pin and `keep_updated` are both set.
- **D8 — control-side rendering.** The templates are rendered with
  `json.dumps`/an f-string (reemote ships no Jinja2); the profile entry uses
  the *resolved absolute* home path where the template interpolates the raw
  `~/.composer` — equivalent for every login shell that sources
  `/etc/profile.d`, and unambiguous for non-root owners.

## 7. Offline verification

`tests/test_roles_ansible_role_composer.py` imports the example by path,
replays its orchestration barrier-for-barrier against persistent
`SimulatedHost`s (the same host object across every `run`, so pass 2 observes
pass 1) and asserts:

- the first pass installs: the sha384-verified installer at `/tmp`, the phar
  at `composer_path` (0755), the staged phar consumed, the home directory,
  the profile entry, and both action shells fired exactly once;
- the **second pass changes nothing**: no changed log entry, no action
  re-run, no signature/installer fetch;
- the dry preview mutates no host store;
- the auth-token, no-packages, `add_to_path: false`, version-pin,
  custom-path and already-installed branches each converge;
- the pure helpers (variable resolution, package normalization, tilde
  expansion, installer-arg precedence) are unit-tested directly.

Two between-pass bridges are **simulation limits, not library bugs**: the
`shell_results` seam models no filesystem effects, so the test plants
`/tmp/composer.phar` after the installer action and each package's
`vendor/<name>` directory after its require action — what the real commands
produce. The control-side fetches are answered by monkeypatching
`files._fetch_url_bytes` with a canned installer whose sha384 matches the
canned signature.

## 8. Scoped out (opt-in branches, default-false)

- **`composer_keep_updated`** (`tasks/main.yml` task 8): the self-update
  `command` runs on every play and reports `changed` whenever it updates —
  stateless, never converging. It could ride a `Shell` action, but it is
  opt-in (default false) and was scoped out in Phase 0; setting it true
  fails fast with a message naming the branch.
- **`composer_add_project_to_path`** (`tasks/project-bin.yml`): writes a
  second profile entry for a project's `vendor/bin`. Also opt-in (default
  false), also fail-fast. Its template would map to the same
  `files.install` recipe as `composer.sh.j2` if a future pass takes it on.

Neither branch blocks the default-path migration; no library gap is involved
in either exclusion.