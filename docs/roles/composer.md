# composer — Composer role

| | |
| --- | --- |
| Role | [`geerlingguy.composer`](https://github.com/geerlingguy/ansible-role-composer) |
| Module | [`reemote.roles.composer`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/composer.py) |
| Runner | [`examples/roles/composer.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/composer.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | `composer_`-prefixed inventory keys → `ComposerVars` |

<!---
| Waves | `5 + 2P` for a fresh host with `P` global packages (7 with one package); `3 + P` once converged |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed — both templates render with `json.dumps` / an f-string) |
| Offline test | [`tests/roles/test_composer.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_composer.py) |
| Factory | plain (non-`async`) — the runner calls `composer(v)`; the control-side signature fetch is a per-host `control` barrier inside the body |

`composer` installs [Composer](https://getcomposer.org/), the PHP dependency
manager, on Ubuntu hosts: the signature-verified installer download, the
installer run, the phar move into `/usr/local/bin/composer`, the `~/.composer`
home directory, the optional GitHub OAuth `auth.json`, the globally-required
packages and the `/etc/profile.d/composer.sh` `$PATH` entry.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("composer", composer(v))(host)` and a run is one `run_waves`
pass. The Ansible role's whole task list is that single deploy, and its
`defaults/main.yml` is `ComposerVars`.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `stat: path={{ composer_path }}` + `register` | `yield fact(FileExists, path=composer_path)` | barrier |
| `~/.composer` tilde expansion in the `become_user`'s context | `yield fact(server.Home, user=home_owner)` + `resolve_home` | barrier |
| `uri: url=<installer.sig> return_content=yes` (gated on *any* host needing install) | `yield control(files.fetch_url, SIGNATURE_URL)` gated on *this* host's `need_install` | deviation (D3) |
| `get_url: url=<installer> dest=/tmp checksum=sha384:…` | `files.download(url, dest, mode, checksum)` | deviation (D2) |
| `command: php composer-installer.php chdir=/tmp` (creates-gated) | `act(Shell, "cd /tmp && php composer-installer.php --2")` | barrier (action) |
| `command: mv /tmp/composer.phar {{ composer_path }}` (creates-gated) | `files.copy(src, dest, mode, overwrite)` + `files.file(phar, present=False)` | deviation (D5) |
| `file: path={{ composer_home }} state=directory owner= group= mode=` | `files.directory(home, mode, user, group)` | covered |
| `template: auth.json.j2` (when a token is set) | `render_auth_json` + `files.install` | covered (D9) |
| `composer global require` loop (creates-gated) | per-package `fact(FileExists, vendor)` + `act(Shell, env, sudo)` | barrier (action) |
| `template: composer.sh.j2` → `/etc/profile.d` (when `add_to_path`) | `render_profile` + `files.install` | covered (D9) |
| `fail:` version xor gate | `ValueError` in a `ComposerVars` model validator | recipe |
| `composer_keep_updated` self-update branch | **rejected** at resolution time | scoped out |
| `project-bin.yml` (`composer_add_project_to_path`) branch | **rejected** at resolution time | scoped out |

The migration surfaced **no new library gaps**: `files.fetch_url` /
`files.download` (issue #20) cover the control-side signature and installer
fetch, and the `act` barrier carries the installer and require commands with
their `env` and `sudo`.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

No `reemote[templates]` — the role renders `auth.json` with `json.dumps` and the
`$PATH` entry with an f-string, so there is no Jinja2 and no PyYAML. The
signature and the installer are fetched **control-side** (`files.fetch_url` /
`files.download`), so the control machine needs outbound HTTPS to
`composer.github.io` and `getcomposer.org`; the host downloads nothing for the
install itself.

The target needs PHP >= 5.4 and `git` on its `$PATH` before the run, and — for
the global-require step — egress to `packagist.org` and `github.com`, because
that step is Composer itself downloading on the host exactly as the Ansible role
does. SSH access to each Ubuntu host with passwordless sudo (or a `privilege`
section in the inventory): every elevated request carries `sudo=True`, because
reemote has no play-level `become`.

## 3. Variables

`ComposerVars` is a frozen pydantic model with unprefixed field names; the prefix
passed to `resolve_vars` binds the inventory keys.

--->

::: reemote.roles.composer
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - composer

**Variables:**

| Field | Type | Default | Inventory key | Ansible name |
| --- | --- | --- | --- | --- |
| `path` | `str` | `/usr/local/bin/composer` | `composer_path` | `composer_path` |
| `keep_updated` | `bool` | `false` | `composer_keep_updated` | `composer_keep_updated` |
| `version` | `str` | `""` | `composer_version` | `composer_version` |
| `version_branch` | `str` | `--2` | `composer_version_branch` | `composer_version_branch` |
| `home_path` | `str` | `~/.composer` | `composer_home_path` | `composer_home_path` |
| `home_owner` | `str` | `root` | `composer_home_owner` | `composer_home_owner` |
| `home_group` | `str` | `root` | `composer_home_group` | `composer_home_group` |
| `global_packages` | `tuple[tuple[str, str], ...]` | `()` | `composer_global_packages` | `composer_global_packages` |
| `add_to_path` | `bool` | `true` | `composer_add_to_path` | `composer_add_to_path` |
| `add_project_to_path` | `bool` | `false` | `composer_add_project_to_path` | `composer_add_project_to_path` |
| `github_oauth_token` | `str` | `""` | `composer_github_oauth_token` | `composer_github_oauth_token` |

<!---

One further variable carries **no** `composer_` prefix upstream and is read raw
from `host.extra`, preserving the existing inventory contract:

| Inventory key | Type | Default |
| --- | --- | --- |
| `php_executable` | `str` | `php` |

Precedence is `model defaults < inventory extra < overrides`. `global_packages`
accepts the role's list-of-`{name, release}` JSON shape or `(name, release)`
pairs; a `before` validator flattens either to frozen pairs and defaults
`release` to `@stable` (the role's `item.release | default('@stable')`).
Validation is fail-fast: `path` must be absolute, `home_path` must be absolute or
start with `~`, the owner/group must be non-empty, and `extra="forbid"` plus
`resolve_vars`' binding report a prefixed key that names no field
(`composer_pathh` raises `RoleVarsError` rather than silently defaulting). The
two opt-in branches are rejected here too, before any host is contacted:
`keep_updated` (D8) and `add_project_to_path`, with the role's own `fail:` xor
message reproduced when a version pin and `keep_updated` are both set.

## 5. Run it

The role is library code; `examples/roles/composer.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables, builds the deploy from the `composer` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the real apply — printing the
wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import ComposerVars, composer

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        ComposerVars, "composer_", inventory,
        overrides={"global_packages": (("phpunit/phpunit", "@stable"),)},
    )
    deploy = composer(vars_set)                                    # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)            # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/composer.py
```

The runner passes one call-site `override` so the global-require and `$PATH`
halves are demonstrated; the library default for `global_packages` is the role's
own empty list (D10). The `dry=True` pass is a one-wave preview: the role's first
item is the `FileExists` stat gate on the composer binary, so the run parks there
(`barrier: FileExists → unknown until run`), reads no state, fetches nothing and
applies no request — unlike the old two-phase script, whose prepare phase fetched
the signature control-side, the wave driver's dry pass never reaches the `control`
barrier. The converged rerun below is the real preview, and it is cheap.

**Run the program twice.** The second run must report no change: the binary exists
(so the whole installer half, the signature fetch included, is skipped), every
configured package's `vendor` directory exists (so no require action re-runs), and
the home/auth/profile content matches. That is the migration's idempotence claim,
and the offline suite asserts it.

## 6. What it does

One deploy. For a fresh host with `P` global packages the wave count is
`5 + 2P` — one per barrier, then the requests between them:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(FileExists, path=composer_path)` | the role's `stat` register — the installer-half guard |
| 1 | `fact(server.Home, user=home_owner)` | the `~/.composer` tilde expansion |
| 2 … `1+P` | `fact(FileExists, path=home/vendor/<name>)` × `P` | the require loop's `creates=` gate, one wave per package |
| `2+P` | `control(files.fetch_url, SIGNATURE_URL)` | only when `need_install`; the *need* is per-host, so it cannot be hoisted into a factory (D3) |
| `3+P` | `files.download` request, closed by `act(Shell, installer)` | the download is applied before the installer action runs |
| `4+P` | `files.copy` + `files.file` (phar cleanup) + `files.directory` + `files.install` (auth) requests, closed by the first `act(Shell, require)` | the phar the installer produced is copied into place, then the first package is required |
| … | `act(Shell, require)` × `P-1` | one require action per remaining missing package |
| last | `files.install` (`/etc/profile.d/composer.sh`) | the `$PATH` entry, after the require loop exactly as the role orders it |

A converged host skips the whole installer half — the `control` fetch included —
and every require action, so it collapses to `3 + P` fact waves plus one request
wave. There is **no checkpoint and no handler**: the role ships no
`handlers/main.yml` and nothing reads an outcome, so a barrier-only wave would
report nothing. A host whose `host.family` is not `"Ubuntu"` returns before its
first `yield`, so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_composer.py`, against the simulated Ubuntu host (`ubuntu`)
with one global package (`phpunit/phpunit`) plus a CentOS host the role never
touches. Fresh host, first pass:

```text
(root)
  wave 0
    barrier: FileExists → False
  wave 1
    barrier: Home → /root
  wave 2
    barrier: FileExists → False
  wave 3
    barrier: fetch_url → e59fdf708874f940b3f7d328de1b3a738c76dc79f000c659586c890799eeb62589c526c805ac44f7b5b8f86ac6f52130
  wave 4
    ubuntu: Download Composer installer. [changed: content /tmp/composer-installer.php is absent, will install]
        $ sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-…/content /tmp/composer-installer.php && chmod 755 /tmp/composer-installer.php && rm -rf /tmp/reemote-install-…'
    barrier: Run Composer installer. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 5
    ubuntu: Move Composer into globally-accessible location. [changed: will copy /tmp/composer.phar to /usr/local/bin/composer]
        $ sudo cp -r -f /tmp/composer.phar /usr/local/bin/composer
        $ sudo chmod 755 /usr/local/bin/composer
    ubuntu: Remove the staged composer.phar from /tmp. [changed: /tmp/composer.phar present, will remove]
        $ sudo rm -f /tmp/composer.phar
    ubuntu: Ensure composer directory exists. [changed: /root/.composer missing, will create]
        $ sudo mkdir -p /root/.composer
        $ sudo chmod 755 /root/.composer
        $ sudo chown root:root /root/.composer
    barrier: Globally require phpunit/phpunit. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 6
    ubuntu: Add composer home bin directory to global $PATH. [changed: content /etc/profile.d/composer.sh is absent, will install]
        $ sudo sh -c 'mkdir -p /etc/profile.d && cp /tmp/reemote-install-…/content /etc/profile.d/composer.sh && chmod 644 /etc/profile.d/composer.sh && rm -rf /tmp/reemote-install-…'
```

The `fetch_url` line prints the barrier's raw `str` outcome — here the sha384 of
the test's canned installer; a real run interpolates the live signature fetched
from `composer.github.io`.

The rerun — wave 0 answers `True`, so the installer half and the require action
are skipped, the control fetch never happens, and no line reports a change:

```text
(root)
  wave 0
    barrier: FileExists → True
  wave 1
    barrier: Home → /root
  wave 2
    barrier: FileExists → True
  wave 3
    ubuntu: Ensure composer directory exists. [no change: directory /root/.composer already exists]
    ubuntu: Add composer home bin directory to global $PATH. [no change: content already up to date (/etc/profile.d/composer.sh)]
```

The CentOS host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request |
| D2 | The signature and the installer are fetched **control-side** (`files.fetch_url` / `files.download`) | `uri`/`get_url` fetch on the target; reemote's URL operations fetch control-side by design, so the control machine needs egress and the host downloads nothing for the install |
| D3 | The signature fetch is a per-host `control` barrier gated on this host's `need_install`, not an inventory-wide `async def` factory fetch | the role gated it on "any host needs install", an inventory-wide condition a per-host body cannot see; §18.8 recommends the per-host `control` form, which keeps the factory synchronous and never touches the network for an already-installed host |
| D4 | Every elevated request carries `sudo=True` explicitly | the role's `become_user: {{ composer_home_owner }}` maps to the inventory `privilege` section (a non-root owner needs `privilege.sudo_user`), never to an operation argument |
| D5 | `mv /tmp/composer.phar {{ composer_path }}` becomes `files.copy` (rename form, mode 0755) plus a paired `files.file(present=False)` cleanup | copy is sha1-idempotent but keeps its source, so the cleanup reproduces `mv`'s source consumption and keeps the second pass a no-op |
| D6 | The installer download and run stay **unprivileged** | the role's `get_url`/`command` tasks carry no `become` and write only to `/tmp`, which is login-writable |
| D7 | `php_executable` — the role's only **unprefixed** variable — is read raw from `host.extra` | giving it a `composer_` prefix would break the role's inventory contract (the datadog probe-config precedent) |
| D8 | `composer_keep_updated` is **rejected** at resolution time | the self-update branch is opt-in and stateless, so it never converges; the role's `fail:` xor message is reproduced when a version pin and `keep_updated` are both set |
| D9 | Both templates render control-side with `json.dumps` / an f-string | reemote ships no Jinja2 and neither template needs it, so there is no `templates` extra; the `$PATH` entry uses the *resolved absolute* home where the Jinja2 template interpolates the raw `~/.composer` |
| D10 | `composer_global_packages` defaults to the role's own `[]` | the old example's `phpunit/phpunit` demo default was an example artifact; a program that wants the global-require path demonstrated passes the packages in the inventory or as an override |

## 9. Scoped out

* **`composer_keep_updated`** (the self-update branch). See D8 — it is stateless
  and never converges.
* **`composer_add_project_to_path`** (the `project-bin.yml` branch). Opt-in and
  default-false; rejected at resolution time.
* **Non-Ubuntu families.** The role is generic-Linux upstream, but this migration
  fixes one target; a non-Ubuntu host is skipped silently rather than mapped.
* **A non-root `composer_home_owner`.** Carried as a variable, but elevating to a
  specific run-as user is the inventory `privilege.sudo_user`'s job, not the
  role's (D4).

--->
