# certbot — Certbot role

| | |
| --- | --- |
| Role | [`geerlingguy.certbot`](https://github.com/geerlingguy/ansible-role-certbot) |
| Module | [`reemote.roles.certbot`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/certbot.py) |
| Runner | [`examples/roles/certbot.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/certbot.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `certbot` role mapping → `CertbotVars` |

<!---
| Waves | `2 + 2C` with `C` configured certificates (four with one cert, fresh or converged); `2` with cert creation off |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed — both hook templates render with an f-string) |
| Offline test | [`tests/roles/test_certbot.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_certbot.py) |
| Factory | plain (non-`async`) — the runner calls `certbot(v)` |

`certbot` installs [Certbot](https://certbot.eff.org/), the Let's Encrypt
client, on Ubuntu hosts: the release-dispatched apt package, the configured
certificates (standalone method with the pre/post service hooks, or webroot
method), and the automatic-renewal cron entry.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("certbot", certbot(v))(host)` and a run is one `run_waves`
pass. The Ansible role's whole task list is that single deploy, and its
`defaults/main.yml` plus the `vars/` dispatch are `CertbotVars`.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: with_first_found Ubuntu-<ver>.yml → default.yml` | `yield fact(LinuxDistribution)` + frozen `PACKAGE_BY_RELEASE` table | barrier (D2) |
| `setup-RedHat.yml` (`when: os_family == 'RedHat'`) | family guard — emits nothing on an Ubuntu-targeted run | scoped out |
| `package: name={{ certbot_package }}` | `apt.packages(packages=(package,), sudo=True)` | covered |
| `set_fact: certbot_script` | a body local (the package name *is* the script) | covered |
| `install-with-snap.yml` (six tasks) | **rejected** at resolution time | scoped out |
| `install-from-source.yml` (three tasks) | **rejected** at resolution time | scoped out |
| `set_fact: cert_item_name` | pure `cert_name()` helper | covered |
| `stat: /etc/letsencrypt/live/<name>/cert.pem` + `register` | `yield fact(FileExists, path=…)` | barrier |
| `file: renewal-hooks/{pre,post} state=directory mode=0755` | two `files.directory(sudo=True)` requests | covered |
| `template: stop/start_services.j2` (0750, gated on the service list) | `render_service_hook` f-string + `files.install` | covered (D4) |
| `shell: certbot certificates … \| grep … \| cut …` (`changed_when: false`, `when: exists`) | `act(Shell, sudo=True)` barrier + `parse_domains`; body raises `CommandError` | barrier (action) |
| `set_fact` domain cleanup / `domains_changed` | pure `parse_domains` / `desired_domains` comparison | covered |
| `command: {{ certbot_create_command }}` (`when: not exists or changed`) | conditional `act(Shell, certonly …, sudo=True)` | barrier (D5) |
| webroot `file: state=directory` (no mode) | `files.directory(path, sudo=True)` — mode/user/group left `None` | covered |
| `cron: name=… job=… minute hour user` | `crontab.crontab(command, cron_name, minute, hour, sudo=…)` | covered (D6) |
| `handlers` / `notify` | none exist upstream — no checkpoint in the stream | covered |

The migration surfaced **no new library gaps**: the renewal cron rides the
`crontab` module shipped for issue #46 (Ansible `cron` parity), and the
mid-play reads — the release dispatch, the `stat` gate, the domain probe — are
`fact`/`act` barriers inside the body.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

No `reemote[templates]` — the two hook templates are a shebang, a header and
one `systemctl` call per service, so a plain f-string is the whole renderer;
there is no Jinja2 and no PyYAML.

SSH access to each Ubuntu host with passwordless sudo (or a `privilege`
section in the inventory): every elevated request carries `sudo=True`,
because reemote has no play-level `become` (D1). Certificate issuance needs
the host's egress to the ACME endpoint; the standalone method binds port 80
for the validation, which is what the pre/post hooks (stopping and starting
`create_standalone_stop_services` around the issue) are for — the webroot
method leaves the server serving.

## 3. Variables

`CertbotVars` is a frozen pydantic model whose fields carry bare names; the role
name passed to `resolve_vars` binds the entry's `certbot` role mapping.

--->

::: reemote.roles.certbot
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - certbot

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `auto_renew` | `bool` | `true` | `certbot_auto_renew` |
| `auto_renew_user` | `str` | `""` | `certbot_auto_renew_user` |
| `auto_renew_hour` | `str` | `3` | `certbot_auto_renew_hour` |
| `auto_renew_minute` | `str` | `30` | `certbot_auto_renew_minute` |
| `auto_renew_options` | `str` | `--quiet` | `certbot_auto_renew_options` |
| `testmode` | `bool` | `false` | `certbot_testmode` |
| `hsts` | `bool` | `false` | `certbot_hsts` |
| `expand` | `bool` | `false` | `certbot_expand` |
| `create_if_missing` | `bool` | `false` | `certbot_create_if_missing` |
| `create_method` | `str` | `standalone` | `certbot_create_method` |
| `create_extra_args` | `str` | `""` | `certbot_create_extra_args` |
| `admin_email` | `str` | `email@example.com` | `certbot_admin_email` |
| `webroot` | `str` | `/var/www/letsencrypt` | `certbot_webroot` |
| `certs` | `tuple[Cert, ...]` | `()` | `certbot_certs` |
| `create_standalone_stop_services` | `tuple[str, ...]` | `("nginx",)` | `certbot_create_standalone_stop_services` |
| `install_method` | `str` | `package` | `certbot_install_method` |
| `package` | `str` | `""` | `certbot_package` |

<!---
Each `certbot_certs` entry is a `Cert` model:

| Field | Type | Default |
| --- | --- | --- |
| `name` | `str` | `""` (derived: the first domain with `*.` stripped) |
| `email` | `str` | `""` (falls back to `admin_email`) |
| `webroot` | `str` | `""` (falls back to the global `webroot`) |
| `domains` | `tuple[str, ...]` | required, non-empty |
| `deploy_hook` | `str` | `""` (D7) |

Precedence is `model defaults < inventory role mapping < overrides`. `certs`
accepts the role's list-of-objects JSON shape; `extra="forbid"` plus
`resolve_vars`' binding report a key that names no field. The scoped-out
branches are rejected at resolution too, before any host is contacted:
`install_method: snap` and `install_method: source` (D3), and a
`create_method` outside `standalone`/`webroot`.

## 5. Run it

The role is library code; `examples/roles/certbot.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables, builds the deploy from the `certbot` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the real apply — printing the
wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import CertbotVars, certbot

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        CertbotVars, "certbot", inventory,
        overrides={
            "create_if_missing": True,
            "certs": [{"domains": ["example.com", "www.example.com"]}],
        },
    )
    deploy = certbot(vars_set)                                     # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)            # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/certbot.py
```

The runner passes call-site `overrides` so the certificate half is demonstrated;
the library default for `create_if_missing` is the role's own `false`. **The
apply phase issues real production certificates** — point the inventory at
machines you own, or set `testmode` for staging first. The `dry=True` pass is a
one-wave preview: the role's first item is the `LinuxDistribution` fact behind
the `include_vars` dispatch, so the run parks there (`barrier:
LinuxDistribution → unknown until run`), reads no further state and applies no
request. The converged rerun below is the real preview, and it is cheap.

**Run the program twice.** The second run must report no change: the package is
installed, the hook files and webroot match, every configured certificate
exists with the domains it was issued with (the probe re-runs but is a read),
and the cron entry is already in the table. That is the migration's idempotence
claim, and the offline suite asserts it.

## 6. What it does

One deploy. With `C` configured certificates the wave count is `2 + 2C` — four
with one cert, fresh and converged alike:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | the `include_vars` release dispatch (D2); also the family proof |
| 1 | `apt.packages` request, closed by `fact(FileExists, cert.pem)` | the install lands before the cert gate reads (D1: the `stat` stays unprivileged — `test -e`, world-readable parents) |
| 2 | hook-directory + hook-file (standalone) or webroot-directory requests, closed by `act(Shell, certonly …)` | a fresh cert: the create action closes the hooks' wave |
| 2′ | (existing cert) the same requests closed by `act(Shell, certificates …)` | the probe is a `changed_when: false` read; drifted domains add one more action wave |
| last | `crontab.crontab` request | the renewal entry, in root's or the connecting user's table per D6 |

A host that only installs (`create_if_missing` off) collapses to two waves; an
off-target host returns before its first `yield` and appears in no wave. There
is **no checkpoint and no handler**: the role ships no `handlers/main.yml` and
nothing reads an outcome, so a barrier-only wave would report nothing.

## 7. Transcript

From `tests/roles/test_certbot.py`, against the simulated Ubuntu host (`ubuntu`,
Ubuntu 22.04) with one standalone cert (`example.com`, `www.example.com`) plus a
RedHat host the role never touches. Fresh host, first pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': 22, 'minor': 4, 'release_meta': {'VERSION_ID': '22.04', 'VERSION_CODENAME': 'jammy'}}
  wave 1
    ubuntu: Install Certbot.         [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install certbot
    barrier: FileExists → False
  wave 2
    ubuntu: Ensure pre hook folder exists. [changed: /etc/letsencrypt/renewal-hooks/pre missing, will create]
        $ sudo mkdir -p /etc/letsencrypt/renewal-hooks/pre
        $ sudo chmod 755 /etc/letsencrypt/renewal-hooks/pre
        $ sudo chown root:root /etc/letsencrypt/renewal-hooks/pre
    ubuntu: Ensure post hook folder exists. [changed: /etc/letsencrypt/renewal-hooks/post missing, will create]
        $ sudo mkdir -p /etc/letsencrypt/renewal-hooks/post
        $ sudo chmod 755 /etc/letsencrypt/renewal-hooks/post
        $ sudo chown root:root /etc/letsencrypt/renewal-hooks/post
    ubuntu: Create pre hook to stop services. [changed: content /etc/letsencrypt/renewal-hooks/pre/stop_services is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/letsencrypt/renewal-hooks/pre && cp /tmp/reemote-install-…/content /etc/letsencrypt/renewal-hooks/pre/stop_services && chown root:root /etc/letsencrypt/renewal-hooks/pre/stop_services && chmod 750 /etc/letsencrypt/renewal-hooks/pre/stop_services && rm -rf /tmp/reemote-install-…'
    ubuntu: Create post hook to start services. [changed: content /etc/letsencrypt/renewal-hooks/post/start_services is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/letsencrypt/renewal-hooks/post && cp /tmp/reemote-install-…/content /etc/letsencrypt/renewal-hooks/post/start_services && chown root:root /etc/letsencrypt/renewal-hooks/post/start_services && chmod 750 /etc/letsencrypt/renewal-hooks/post/start_services && rm -rf /tmp/reemote-install-…'
    barrier: Generate new certificate for example.com. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 3
    ubuntu: Add cron job for certbot renewal. [changed: crontab entry certbot renew --quiet absent, will add]
        $ scp <crontab> -> /tmp/reemote-crontab-…/crontab
        $ sh -c 'crontab /tmp/reemote-crontab-…/crontab && rm -rf /tmp/reemote-crontab-…'
```

The cert the create action "issued" is planted in-stream by the test host (the
composer precedent: a raw `Shell` action mutates no file store), so the rerun
sees it. The rerun — `FileExists` answers `True`, so the probe runs instead of
the create, its domains match, and no line reports a change:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': 22, 'minor': 4, 'release_meta': {'VERSION_ID': '22.04', 'VERSION_CODENAME': 'jammy'}}
  wave 1
    ubuntu: Install Certbot.         [no change: all packages present]
    barrier: FileExists → True
  wave 2
    ubuntu: Ensure pre hook folder exists. [no change: directory /etc/letsencrypt/renewal-hooks/pre already exists]
    ubuntu: Ensure post hook folder exists. [no change: directory /etc/letsencrypt/renewal-hooks/post already exists]
    ubuntu: Create pre hook to stop services. [no change: content already up to date (/etc/letsencrypt/renewal-hooks/pre/stop_services)]
    ubuntu: Create post hook to start services. [no change: content already up to date (/etc/letsencrypt/renewal-hooks/post/start_services)]
    barrier: Check if domains have changed. → {'exit_status': 0, 'stdout': ' example.com www.example.com', 'stderr': ''}
  wave 3
    ubuntu: Add cron job for certbot renewal. [no change: crontab entry certbot renew --quiet already up to date]
```

The renewal entry the cron wave leaves in root-less (connecting-user) crontab:

```text
# reemote-name=Certbot automatic renewal.
30 3 * * * certbot renew --quiet
```

The RedHat host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The play-level `become` becomes an explicit `sudo=True` on every elevated request; the cert `stat` stays unprivileged | reemote has no play-level become; `FileExists` is a `test -e`, and `/etc/letsencrypt` is world-readable, so the read needs no elevation |
| D2 | The `include_vars` `with_first_found` dispatch becomes the `fact(LinuxDistribution)` barrier plus a frozen `PACKAGE_BY_RELEASE` table (only `Ubuntu-16.04.yml` ships upstream: `letsencrypt`); a gathered distribution that is not Ubuntu raises | the dispatch is release-keyed static data, and a barrier result is data — the raise is the role's, not the driver's (the java precedent) |
| D3 | The snap install (six tasks), the source install (three tasks) and the RedHat setup are out of migration scope: `install_method != "package"` raises at resolution, and the source knobs (`certbot_repo`, `certbot_version`, `certbot_keep_updated`, `certbot_dir`) are not modeled; a non-Ubuntu host emits nothing | the Phase 0 scope fixed the package install + both cert methods; `extra="forbid"` rejects the absent knobs rather than silently ignoring them |
| D4 | The two hook templates render as f-strings with the `service_mgr` branch frozen to `systemctl`, and `{{ ansible_managed }}` becomes a static reemote header line | every Ubuntu the role supports runs systemd; reemote ships no Jinja2, so there is no `templates` extra (the java precedent) |
| D5 | The create command is rebuilt in Python with whitespace normalized and `--expand` emitted once (the upstream template emits it twice); the full `certbot_create_command` override knob is not modeled — `create_extra_args` covers additive flags | the YAML `>-` folding left runs of spaces around every false conditional; a readable builder beats a string-compatible one |
| D6 | The cron task's `user=` becomes a privilege mapping: `""` (the upstream default, the connecting user) → unprivileged request, `"root"` → `sudo=True`, any third user raises | `crontab.crontab` deliberately has no `user` parameter (issue #46) — which table to edit follows the request's privilege, never an operation argument |
| D7 | An explicitly empty `deploy_hook: ""` skips the flag | the role tests `'deploy_hook' in cert_item`, so an empty string would still emit `--deploy-hook ''` — an unusable command nobody writes on purpose |

## 9. Scoped out

* **The snap install branch** (snapd enable, the `/snap` link, `classic`
  install, the `/usr/bin/certbot` symlink). Rejected at resolution (D3);
  reemote ships a `snap` module, but the branch was cut by the Phase 0 scope.
* **The source install branch** (`git` clone of `certbot-auto` plus a chmod).
  Rejected at resolution (D3); its knobs are absent from the model entirely.
* **Non-Ubuntu families.** The role is multi-OS upstream (its `include_vars`
  chain and `setup-RedHat.yml`), but this migration fixes one target; a
  non-Ubuntu host is skipped silently rather than mapped.
* **`certbot_create_command`'s full override.** Upstream lets you replace the
  whole command template; the migrated role exposes the additive
  `create_extra_args` knob and the flag toggles instead (D5).
* **A third-user renewal crontab.** `auto_renew_user` maps to the request's
  privilege (D6); managing some other system user's table is not expressible
  without an operation-level `user=` by design.

--->
