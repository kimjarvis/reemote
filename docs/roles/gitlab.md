# gitlab — GitLab CE omnibus role

| | |
| --- | --- |
| Role | [`geerlingguy.gitlab`](https://github.com/geerlingguy/ansible-role-gitlab) |
| Module | [`reemote.roles.gitlab`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/gitlab.py) |
| Runner | [`examples/roles/gitlab.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/gitlab.py) |
| Target | **Ubuntu, RedHat** — hosts in the `"Ubuntu"` or `"RedHat"` OS family |
| Variables | the entry's `gitlab` role mapping → `GitlabVars` |

<!---
| Waves | host-dependent: `8` on a fresh host (four acts, three facts, one checkpoint), `4` once converged (three facts, one checkpoint, no handler wave) |
| Outputs | none — the deploy returns `None`; no other role reads gitlab's results |
| Extra deps | **`reemote[templates]`** (jinja2 renders `gitlab.rb.j2`) |
| Offline test | [`tests/roles/test_gitlab.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_gitlab.py) |
| Factory | plain (non-`async`) — the runner calls `gitlab(v)`; the render is control-side Python inside the body, not a barrier |

`gitlab` installs and configures the GitLab CE omnibus package on Ubuntu and
RedHat hosts: the dependency packages (plus `gnupg2`, Debian-only), the vendor
repository via its own installer script (`script.deb.sh`/`script.rpm.sh`,
fetched control-side, run elevated), the `gitlab-ce` package — optionally
version-pinned — the first-run `gitlab-ctl reconfigure` that bootstraps the
instance, the optional self-signed SSL certificate under `/etc/gitlab/ssl`, and
the rendered `/etc/gitlab/gitlab.rb` (root:root, mode `0600`); a config change
queues the role's one handler, `restart gitlab: command: 'gitlab-ctl
reconfigure'`.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("gitlab", gitlab(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list *and* its
handler are that single deploy, and its `defaults/main.yml` is `GitlabVars`.

Like nfs this is a **command-handler role**, but it is the corpus's
**fact-gated** one: the reads Ansible took *between* tasks — two `stat`
probes and a `creates=` check — live inside the body as `fact` barriers, and
the vendor-script, bootstrap and certificate runs live as `act` barriers, so
the wave count depends on what each host already has:

```python
packages_op = dnf.packages if host.family == "RedHat" else apt.packages
yield op(packages_op, ...)                    # dependencies (wave 0; apt half
                                              # merges gnupg2 + cache refresh)
installed = yield fact(FileExists, GITLAB_CTL_PATH)
if not installed:                             # tasks 6-9: repo script, package
    yield op(files.download, ...)
    yield act(Shell, f"bash {REPO_SCRIPT_SPOOL}", ...)   # raises on a non-zero exit
    yield op(packages_op, packages=plan.package_pin, ...)
bootstrapped = yield fact(FileExists, BOOTSTRAPPED_PATH)
if not bootstrapped:                            # task 10, failed_when: false
    yield act(Shell, RECONFIGURE_COMMAND, ...)  # result deliberately unchecked
...                                             # tasks 11-12: ssl dir, cert fact, openssl act
wave_report = yield checkpoint("configure done")
notified = changed_hosts(wave_report, NOTIFYING)
yield from handler(notified, hv.restart_handler_failed_when)(host)
```

## 1. What it is, and where it came from

The role's thirteen tasks — and its handler — map onto reemote requests and
seven barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: "{{ ansible_os_family }}.yml"` | frozen per-family module constants (`REPO_SCRIPT_URLS`: the `script.deb.sh`/`script.rpm.sh` URLs; one shared `=` pin spelling); `host.family` from the inventory decides, so no gather is needed (D2) | recipe |
| `stat: /etc/gitlab/gitlab.rb` register `gitlab_config_file` | **dropped** (D3): registered and never referenced by any later task or handler — dead code upstream | — |
| `stat: /usr/bin/gitlab-ctl` register `gitlab_installed` | `installed = yield fact(FileExists, GITLAB_CTL_PATH)`, gating tasks 6-10 | barrier |
| `package: name={{ gitlab_dependencies }}` + `apt: name=gnupg2` (Debian) | one family-dispatched request: `apt.packages(dependencies + ("gnupg2",), update=True, cache_valid_time=600)` on Ubuntu, plain `dnf.packages(dependencies)` on RedHat (D4) | covered |
| `get_url: .../script.deb.sh dest=/tmp/gitlab_install_repository.sh validate_certs={{ gitlab_download_validate_certs }}` | `files.download(url, dest)` — control-side fetch of the family's script URL; the TLS flag is inert (D10) | recipe |
| `command: bash /tmp/gitlab_install_repository.sh` register (unused) | `act(Shell, ...)` named `Install GitLab repository`; the body raises `CommandError` on a non-zero exit, the unused register is dropped | barrier |
| `set_fact` × 2 (`gitlab_package_version_separator`, `gitlab_package_name`) | folded into `package_pin(hv)` — `gitlab-ce` or `gitlab-ce=<version>` | recipe |
| `apt: name={{ gitlab_package_name }} update_cache=yes force=yes async=300 poll=5` | the same `packages_op` again: `apt.packages`/`dnf.packages` with `packages=plan.package_pin` — the async ceiling is dropped (D5) | covered |
| `command: gitlab-ctl reconfigure creates=/var/opt/gitlab/bootstrapped failed_when: false` | `bootstrapped = yield fact(FileExists, BOOTSTRAPPED_PATH)` gate + unchecked `act(Shell, ...)` (D7) | barrier |
| `file: path=/etc/gitlab/ssl state=directory mode=0700` when `gitlab_create_self_signed_cert` | `files.directory(SSL_DIR, mode="0700", user="root", group="root")` under the same bool gate | covered |
| `command: openssl req ... creates={{ gitlab_ssl_certificate }}` | `yield fact(FileExists, plan.ssl_certificate)` as the `creates=` read (D9) + `act(Shell, openssl_command)` raising on failure | barrier |
| `template: src={{ gitlab_config_template }} dest=/etc/gitlab/gitlab.rb owner=root group=root mode=0600` `notify: restart gitlab` | control-side Jinja2 render of the verbatim template → `files.install(mode="0600")` + `files.file` metadata companion; both names notify (D6, D11) | recipe |
| `handlers/main.yml: restart gitlab` (`command: gitlab-ctl reconfigure`, `failed_when: {{ ... \| bool }}`) | `checkpoint("configure done")` → `changed_hosts(…, NOTIFYING)` → `act(Shell, RECONFIGURE_COMMAND, name="restart gitlab")`, raising only with `restart_handler_failed_when=True` (D8) | barrier |

No library gap blocked the mapping — every task and the handler had a faithful
target, so the task audit filed no tickets (the open tracker held only #42 and
#72, neither relevant). The two `stat`/`creates=` reads the body needs between
waves are exactly what the barrier model is for; on the old `run()` shape this
role would have needed three separate phases.

## 2. Install

```bash
pip install "reemote[templates]"   # or, in a checkout: uv sync
```

`jinja2` is the only extra: `templates/gitlab.rb.j2` is a hundred-line
template with eleven `{% if %}` gates, an `{% for %}` loop and the
`to_json`/`lower` filters — no plain f-string reproduces it. The import
enters only through `reemote.roles._render`, *inside* `render_gitlab_rb`, so
`import reemote.roles` stays clean without the extra.

SSH access to each target host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
and action carries `sudo=True`, because reemote has no play-level `become`.
The script and config writes are spool-and-install (`files.download`,
`files.install`), so the transfers themselves never elevate. The vendor script
is fetched by the **controller** (egress to `packages.gitlab.com`), and the
config template renders on the controller too — a custom
`gitlab_config_template` is a controller-side path (D11).

Budget for the omnibus: the install is multiple gigabytes, the first
`gitlab-ctl reconfigure` runs for minutes, and GitLab's bundled nginx claims
ports 80/443 — stop any existing web server first.
--->

::: reemote.roles.gitlab
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - gitlab

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `domain` | `str` | `gitlab` — the host name the four derived defaults interpolate; must be non-empty | `gitlab_domain` |
| `external_url` | `str` | `""` — empty derives `https://{domain}/` | `gitlab_external_url` |
| `git_data_dir` | `str` | `/var/opt/gitlab/git-data` (the `git_data_dirs` path) | `gitlab_git_data_dir` |
| `edition` | `str` | `gitlab-ce` — the package name *and* the vendor repo URL segment; must be non-empty | `gitlab_edition` |
| `version` | `str` | `""` — empty installs the candidate; non-empty pins `gitlab-ce=<version>` (D2). Upstream's template predates GitLab 18, so pin a 17.x release (see §7's live run) | `gitlab_version` |
| `backup_path` | `str` | `/var/opt/gitlab/backups` | `gitlab_backup_path` |
| `config_template` | `str` | `gitlab.rb.j2` — the bundled sentinel renders the embedded template; any other value is a controller-side path (D11) | `gitlab_config_template` |
| `redirect_http_to_https` | `bool` | `True` (`nginx['redirect_http_to_https']`) | `gitlab_redirect_http_to_https` |
| `ssl_certificate` | `str` | `""` — empty derives `/etc/gitlab/ssl/{domain}.crt` | `gitlab_ssl_certificate` |
| `ssl_certificate_key` | `str` | `""` — empty derives `/etc/gitlab/ssl/{domain}.key` | `gitlab_ssl_certificate_key` |
| `create_self_signed_cert` | `bool` | `True` — the tasks 11-12 gate | `gitlab_create_self_signed_cert` |
| `self_signed_cert_subj` | `str` | `""` — empty derives `/C=US/ST=Missouri/L=Saint Louis/O=IT/CN={domain}` | `gitlab_self_signed_cert_subj` |
| `ldap_enabled` | `bool` | `False` — the template's LDAP block gate | `gitlab_ldap_enabled` |
| `ldap_host` | `str` | `example.com` | `gitlab_ldap_host` |
| `ldap_port` | `str` | `389` (rendered unquoted) | `gitlab_ldap_port` |
| `ldap_uid` | `str` | `sAMAccountName` | `gitlab_ldap_uid` |
| `ldap_method` | `str` | `plain` (`ssl` or `plain`) | `gitlab_ldap_method` |
| `ldap_bind_dn` | `str` | `CN=Username,CN=Users,DC=example,DC=com` | `gitlab_ldap_bind_dn` |
| `ldap_password` | `str` | `password` | `gitlab_ldap_password` |
| `ldap_base` | `str` | `DC=example,DC=com` | `gitlab_ldap_base` |
| `smtp_enable` | `bool` | `False` — the template's SMTP block gate | `gitlab_smtp_enable` |
| `smtp_address` | `str` | `smtp.server` | `gitlab_smtp_address` |
| `smtp_port` | `str` | `465` (rendered unquoted) | `gitlab_smtp_port` |
| `smtp_user_name` | `str` | `smtp user` — falsy omits the line (the template's gate) | `gitlab_smtp_user_name` |
| `smtp_password` | `str` | `smtp password` — falsy omits the line | `gitlab_smtp_password` |
| `smtp_domain` | `str` | `example.com` | `gitlab_smtp_domain` |
| `smtp_authentication` | `str` | `login` — falsy omits the line | `gitlab_smtp_authentication` |
| `smtp_enable_starttls_auto` | `bool` | `True` | `gitlab_smtp_enable_starttls_auto` |
| `smtp_tls` | `bool` | `False` | `gitlab_smtp_tls` |
| `smtp_openssl_verify_mode` | `str` | `none` | `gitlab_smtp_openssl_verify_mode` |
| `smtp_ca_path` | `str` | `/etc/ssl/certs` | `gitlab_smtp_ca_path` |
| `smtp_ca_file` | `str` | `/etc/ssl/certs/ca-certificates.crt` | `gitlab_smtp_ca_file` |
| `nginx_ssl_verify_client` | `str` | `""` — falsy omits the 2-way SSL line | `gitlab_nginx_ssl_verify_client` |
| `nginx_ssl_client_certificate` | `str` | `""` — falsy omits the line | `gitlab_nginx_ssl_client_certificate` |
| `restart_handler_failed_when` | `bool` | `False` — upstream's string coerces to `False` through `\| bool`, so the handler never fails by default; `True` raises `CommandError` on a non-zero exit (D8) | `gitlab_restart_handler_failed_when` |
| `dependencies` | `tuple[str, …]` | `openssh-server, postfix, curl, openssl, tzdata` — `gnupg2` is appended on the Ubuntu half only (D4), not carried here | `gitlab_dependencies` |
| `time_zone` | `str` | `UTC` | `gitlab_time_zone` |
| `backup_keep_time` | `str` | `604800` (rendered unquoted) | `gitlab_backup_keep_time` |
| `download_validate_certs` | `bool` | `True` — **inert** (D10): the fetch is control-side | `gitlab_download_validate_certs` |
| `default_theme` | `str` | `2` | `gitlab_default_theme` |
| `email_enabled` | `bool` | `False` — the template's email block gate | `gitlab_email_enabled` |
| `email_from` | `str` | `gitlab@example.com` | `gitlab_email_from` |
| `email_display_name` | `str` | `Gitlab` | `gitlab_email_display_name` |
| `email_reply_to` | `str` | `gitlab@example.com` | `gitlab_email_reply_to` |
| `registry_enable` | `bool` | `False` — the template's registry block gate | `gitlab_registry_enable` |
| `registry_external_url` | `str` | `https://gitlab.example.com:4567` | `gitlab_registry_external_url` |
| `registry_nginx_ssl_certificate` | `str` | `/etc/gitlab/ssl/gitlab.crt` | `gitlab_registry_nginx_ssl_certificate` |
| `registry_nginx_ssl_certificate_key` | `str` | `/etc/gitlab/ssl/gitlab.key` | `gitlab_registry_nginx_ssl_certificate_key` |
| `letsencrypt_enable` | `bool` | `False` — the template's letsencrypt block gate | `gitlab_letsencrypt_enable` |
| `letsencrypt_contact_emails` | `tuple[str, …]` | `gitlab@example.com` — rendered by `to_json` | `gitlab_letsencrypt_contact_emails` |
| `letsencrypt_auto_renew_hour` | `int` | `1` | `gitlab_letsencrypt_auto_renew_hour` |
| `letsencrypt_auto_renew_minute` | `int` | `30` | `gitlab_letsencrypt_auto_renew_minute` |
| `letsencrypt_auto_renew_day_of_month` | `str` | `*/7` | `gitlab_letsencrypt_auto_renew_day_of_month` |
| `letsencrypt_auto_renew` | `bool` | `True` | `gitlab_letsencrypt_auto_renew` |
| `nginx_listen_port` | `int \| None` | `None` — unset keeps the template's `is defined` check false, as upstream (D12) | `gitlab_nginx_listen_port` |
| `nginx_listen_https` | `bool \| None` | `None` — same gate (D12) | `gitlab_nginx_listen_https` |
| `extra_settings` | `tuple[dict[str, tuple[GitlabExtraOption, …]], …]` | `()` — one item per upstream dict; each option is `{key, value, type?}`, `type: plain` or a non-string value rendering unquoted (D12) | `gitlab_extra_settings` |

<!---
Precedence is `model defaults < inventory role mapping < overrides`. Validation
is fail-fast at `resolve_vars`, before any host is contacted: `domain` and
`edition` must be non-empty (they feed the derived paths and the repo URL —
upstream would interpolate an empty domain into `/etc/gitlab/ssl/.crt`), and
`extra="forbid"` turns a typo like `domian` into a `RoleVarsError` naming the
valid keys. JSON lists bind to tuples everywhere. `extra_settings` items are
the one structure that stays dict-shaped inside the model — they never reach
`op()` (the render consumes them control-side), so hashability is not required.

## 5. Run it

The role is library code; `examples/roles/gitlab.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`gitlab` role mapping), builds the deploy from the `gitlab` factory and drives
it with `run_waves` — a `dry=True` preview, then the real apply — printing the
wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import GitlabVars, gitlab

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(GitlabVars, "gitlab", inventory)   # no overrides
    deploy = gitlab(vars_set)                                   # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # parks at the install probe
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/gitlab.py
```

The runner passes no call-site `overrides`, so the role's own defaults apply:
`domain: gitlab` derives the external URL `https://gitlab/`, the certificate
paths `/etc/gitlab/ssl/gitlab.crt`/`.key` and the subject CN; the cert is
generated; `version` is empty, so the candidate installs. To pin or rename
anything, add keys to the inventory entry's `gitlab` role mapping — reemote's
`host_vars` mechanism:

```json
{
  "host": "ubuntu",
  "family": "Ubuntu",
  "connection": {"address": "192.168.0.181", "username": "supervisor",
                 "key_path": "~/.ssh/id_ed25519"},
  "group": "ubuntu",
  "roles": {"gitlab": {"domain": "gitlab.example.com", "version": "17.5.1-ce.0"}}
}
```

The `dry=True` pass is *thinner* here than for the checkpoint-only roles: the
role's first barrier is the **install-probe fact**, and `run_waves` never runs
a barrier in dry mode — whether the repo, package, bootstrap and certificate
stages join the stream depends on the host's answer. So the preview plans only
wave 0's dependency install and parks (`barrier: Check if GitLab is already
installed → unknown until run`). Nothing is applied.

**Run the program twice.** On a converged host the second run reports no
change: the probe finds `gitlab-ctl`, the bootstrap marker exists, the
certificate is in place, and `/etc/gitlab/gitlab.rb` is byte-identical at
root:root `0600` — so the checkpoint notifies nobody and the handler wave
never appears. That is the migration's idempotence claim, and the offline
suite asserts it.

One caveat the live run below makes concrete: the bundled template is
upstream's file verbatim, and upstream predates GitLab 18 — so with
`version: ""` against a modern candidate, the install succeeds and the
rendered `git_data_dirs` line is *rejected* by `gitlab-ctl reconfigure`. Pin
a 17.x release to have the role's configuration applied.

## 6. What it does

One deploy, fact-gated: the barriers below each close a wave, and a wave with
no requests (the acts) carries no request line — so the fresh host's stream is
eight waves and the converged host's is four. From `tests/roles/test_gitlab.py`:

| Pass | Barriers (in order) | Request waves |
| --- | --- | --- |
| fresh host | `fact` install probe → `act` repo script → `fact` bootstrap probe → `act` first reconfigure → `fact` cert probe → `act` openssl → `checkpoint("configure done")` → `act` restart gitlab | `{0, 1, 2, 4, 6}` (waves 3 and 5 are act-only) |
| converged host | `fact` install probe (True) → `fact` bootstrap probe (True) → `fact` cert probe (True) → `checkpoint("configure done")`, 0 notified | `{0, 2, 3}` (wave 1 is fact-only) |

Five rules are load-bearing:

* **The facts gate real stages, not just display.** Ansible's two `stat`
  registers and the `creates=` parameter all decide whether later tasks run,
  so the reads live *inside* the body as `fact(FileExists)` barriers — the
  certificate probe behind a mode-`0700` directory is served by the fact's
  own elevated re-probe (issue #70, D9), not a hand-rolled compound command.
* **A barrier result is data.** The repo-script and `openssl` acts have their
  results checked and the body raises the typed `CommandError` itself; the
  first-run reconfigure's result is *deliberately* unchecked, preserving
  upstream's `failed_when: false` (D7). One seam key (`sudo gitlab-ctl
  reconfigure`) covers both the tolerated first run and the handler, and the
  two tests read those halves separately.
* **The handler is an `act` barrier, like nfs.** `handlers/main.yml` is
  `command: gitlab-ctl reconfigure`, not a service action — so the handler
  wave holds a `barrier: restart gitlab → {...}` line and no request line,
  and an unnotified host yields nothing at all.
* **The checkpoint must immediately follow `configure`.** It reports only the
  wave it closes (§4.3), so the certificate acts sit *before* it (their own
  waves) and the notification read sees exactly the two `gitlab.rb` request
  names in `NOTIFYING` (D6) — nothing else in the stream may restart GitLab.
* **The dry pass stops at the first fact.** Unlike the checkpoint-only roles
  (kibana, nfs) which plan their whole request wave before parking, gitlab's
  body branches on the install probe, so a dry preview shows one line and one
  unknown barrier — the honest shape of a fact-gated deploy.

A host whose `family` is neither `"Ubuntu"` nor `"RedHat"` returns before its
first `yield`: no requests, no barriers, no report lines — Alpine entries in a
shared inventory are never contacted and no vendor script is ever fetched for
them. The two target families share every stage name and every barrier; only
the package manager and the vendor script URL differ.

## 7. Transcript

From `tests/roles/test_gitlab.py`, against the simulated fresh inventory —
`ubuntu` (apt) and `web1` (RedHat/dnf), both with every default, plus an
Alpine host the role never touches — so each barrier line appears once per
target host; the second pass bridges `existing_files` with the three paths the
real first pass leaves behind. The dry preview plans both dependency installs
and parks at the install probe:

```text
(root)
  wave 0
    ubuntu: Install GitLab dependencies [changed: apt update; 6 package(s) to install]
    web1: Install GitLab dependencies [changed: 5 package(s) to install]
    barrier: Check if GitLab is already installed → unknown until run
    barrier: Check if GitLab is already installed → unknown until run
```

The fresh apply — all eight waves, both families in one pass; every barrier
line is a read the old two-phase shape would have needed a separate `run()`
for:

```text
(root)
  wave 0
    ubuntu: Install GitLab dependencies [changed: apt update; 6 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install openssh-server postfix curl openssl tzdata gnupg2
    web1: Install GitLab dependencies [changed: 5 package(s) to install]
        $ sudo dnf install -y openssh-server postfix curl openssl tzdata
    barrier: Check if GitLab is already installed → False
    barrier: Check if GitLab is already installed → False
  wave 1
    ubuntu: Download GitLab repository installation script [changed: content /tmp/gitlab_install_repository.sh is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-a5aed7398b9bf3d8/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-a5aed7398b9bf3d8/content /tmp/gitlab_install_repository.sh && chmod 644 /tmp/gitlab_install_repository.sh && rm -rf /tmp/reemote-install-a5aed7398b9bf3d8'
    web1: Download GitLab repository installation script [changed: content /tmp/gitlab_install_repository.sh is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-a5aed7398b9bf3d8/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-a5aed7398b9bf3d8/content /tmp/gitlab_install_repository.sh && chmod 644 /tmp/gitlab_install_repository.sh && rm -rf /tmp/reemote-install-a5aed7398b9bf3d8'
    barrier: Install GitLab repository → {'exit_status': 0, 'stdout': '', 'stderr': ''}
    barrier: Install GitLab repository → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    ubuntu: Install GitLab           [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install gitlab-ce
    web1: Install GitLab           [changed: 1 package(s) to install]
        $ sudo dnf install -y gitlab-ce
    barrier: Check if GitLab is already configured → False
    barrier: Check if GitLab is already configured → False
  wave 3
    barrier: Reconfigure GitLab (first run) → {'exit_status': 0, 'stdout': '', 'stderr': ''}
    barrier: Reconfigure GitLab (first run) → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 4
    ubuntu: Create GitLab SSL configuration folder [changed: /etc/gitlab/ssl missing, will create]
        $ sudo mkdir -p /etc/gitlab/ssl
        $ sudo chmod 700 /etc/gitlab/ssl
        $ sudo chown root:root /etc/gitlab/ssl
    web1: Create GitLab SSL configuration folder [changed: /etc/gitlab/ssl missing, will create]
        $ sudo mkdir -p /etc/gitlab/ssl
        $ sudo chmod 700 /etc/gitlab/ssl
        $ sudo chown root:root /etc/gitlab/ssl
    barrier: Check if the self-signed certificate exists → False
    barrier: Check if the self-signed certificate exists → False
  wave 5
    barrier: Create self-signed certificate → {'exit_status': 0, 'stdout': '', 'stderr': ''}
    barrier: Create self-signed certificate → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 6
    ubuntu: Copy GitLab configuration file [changed: content /etc/gitlab/gitlab.rb is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-980c8e07004682bd/content
        $ sudo sh -c 'mkdir -p /etc/gitlab && cp /tmp/reemote-install-980c8e07004682bd/content /etc/gitlab/gitlab.rb && chown root:root /etc/gitlab/gitlab.rb && chmod 600 /etc/gitlab/gitlab.rb && rm -rf /tmp/reemote-install-980c8e07004682bd'
    web1: Copy GitLab configuration file [changed: content /etc/gitlab/gitlab.rb is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-980c8e07004682bd/content
        $ sudo sh -c 'mkdir -p /etc/gitlab && cp /tmp/reemote-install-980c8e07004682bd/content /etc/gitlab/gitlab.rb && chown root:root /etc/gitlab/gitlab.rb && chmod 600 /etc/gitlab/gitlab.rb && rm -rf /tmp/reemote-install-980c8e07004682bd'
    ubuntu: Set GitLab configuration file mode and owner [no change: file /etc/gitlab/gitlab.rb already exists]
    web1: Set GitLab configuration file mode and owner [no change: file /etc/gitlab/gitlab.rb already exists]
    barrier: configure done → 2 hosts notified
    barrier: configure done → 2 hosts notified
  wave 7
    barrier: restart gitlab → {'exit_status': 0, 'stdout': '', 'stderr': ''}
    barrier: restart gitlab → {'exit_status': 0, 'stdout': '', 'stderr': ''}
```

The converged rerun — every probe answers `True` on both hosts, the
repo/openssl stages are gone, and **the handler wave disappears**
(`gitlab-ctl reconfigure` ran exactly twice across each host's fresh pass and
zero times here):

```text
(root)
  wave 0
    ubuntu: Install GitLab dependencies [no change: apt update skipped, cache is fresh; all packages present]
    web1: Install GitLab dependencies [no change: all packages present]
    barrier: Check if GitLab is already installed → True
    barrier: Check if GitLab is already installed → True
  wave 1
    barrier: Check if GitLab is already configured → True
    barrier: Check if GitLab is already configured → True
  wave 2
    ubuntu: Create GitLab SSL configuration folder [no change: directory /etc/gitlab/ssl already exists]
    web1: Create GitLab SSL configuration folder [no change: directory /etc/gitlab/ssl already exists]
    barrier: Check if the self-signed certificate exists → True
    barrier: Check if the self-signed certificate exists → True
  wave 3
    ubuntu: Copy GitLab configuration file [no change: content already up to date (/etc/gitlab/gitlab.rb)]
    web1: Copy GitLab configuration file [no change: content already up to date (/etc/gitlab/gitlab.rb)]
    ubuntu: Set GitLab configuration file mode and owner [no change: file /etc/gitlab/gitlab.rb already exists]
    web1: Set GitLab configuration file mode and owner [no change: file /etc/gitlab/gitlab.rb already exists]
    barrier: configure done → 0 hosts notified
    barrier: configure done → 0 hosts notified
```

The metadata-drift pass — content untouched, stat row pushed to
`gitlab-www:644` — isolates the companion's purpose: `Copy GitLab
configuration file` stays `[no change: content already up to date]` while
`Set GitLab configuration file mode and owner [changed: …]` converges
root:root `0600` and notifies the handler. The config file is root-owned
`0600`, so its read-back rides the elevated tier (issue #69) — the converged
pass emits no command at all.

### Live run on a real Ubuntu host

Both passes were also run for real against the demo host (Ubuntu 24.04, nginx
stopped for the run), and the wave shapes above are what the live trees
showed — with real command output where the transcript above elides it; the
RedHat half is proven by the offline dnf-path tests:

* **fresh, pinned pass — eight waves**, request waves `{0, 1, 2, 4, 6}`: the
dependency install, the vendor `script.deb.sh` downloaded, spooled and run
(`barrier: Install GitLab repository → {'exit_status': 0, ...}`), the pinned
package install (`apt-get ... install gitlab-ce=17.11.7-ce.0`), the bootstrap
reconfigure, `/etc/gitlab/ssl` created `0700`, the `openssl` certificate pair,
`gitlab.rb` installed root:root `0600`, `barrier: configure done → 1 host
notified` and `barrier: restart gitlab → {'exit_status': 0, ...}`. After it,
`gitlab-ctl status` reported 15/15 services `run:`, GitLab's own nginx owned
`:80`/`:443`, `http://` answered `301` to `https://gitlab:443/` — the
template's `redirect_http_to_https` — and `https://` answered `302`.
* **converged pass — four waves**, `{0, 2, 3}`, every probe `True`,
`barrier: configure done → 0 hosts notified` and no handler wave at all. The
only change line was the gated `sudo apt-get update`: the bootstrap took long
enough to age the cache past `cache_valid_time=600`, which is D4's own gate
firing rather than a defect.

Two findings, both about the role's inputs rather than its shape:

* **An unpinned install outruns upstream's template.** With `version: ""` the
same host installed `gitlab-ce 19.6.2-ce.0`, whose cookbooks have removed
`git_data_dirs` (deprecated 17.8, gone in 18.0); the handler's reconfigure
exited `1` with *"Removed configurations found in gitlab.rb. Aborting
reconfigure."* The role recorded that result and raised nothing — precisely
upstream's `failed_when: gitlab_restart_handler_failed_when | bool` default
(D8) — but GitLab went on serving the pre-template configuration. Pinning to
17.11.7-ce.0 left the stream identical and made the handler exit `0`.
* **The runit unit has to be up for the bootstrap to finish.** A purge that
leaves `gitlab-runsvdir.service` `disabled` stalls the first-run reconfigure
inside the runit service setup — no child processes, no log lines — and since
D7 leaves that act unchecked, nothing surfaces. `systemctl enable --now
gitlab-runsvdir` resumes it; a role-managed install from the vendor package's
own postinst never hits this.

The host was torn down afterwards (package purged, `/etc/gitlab`,
`/var/opt/gitlab`, `/var/log/gitlab`, `/opt/gitlab`, the vendor apt source and
its keyring removed, `apt-get update` clean) and nginx restored to
active+enabled on `:80`.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The host-family guard is the body's opening `if`, not a `when:` on every task | an off-target host emits no requests, no barriers and no log lines at all — no `[skipped]` noise |
| D2 | `include_vars` OS dispatch collapses to a frozen per-family table | `REPO_SCRIPT_URLS` carries the `script.deb.sh`/`script.rpm.sh` URLs and the inventory's `family` picks the row, so no `LinuxDistribution` barrier exists; upstream's per-family separator (`=` on Debian, `-` on RedHat) becomes reemote's single `<pkg>=<version>` pin spelling, which `dnf.packages` renders into its own `-` NEVRA |
| D3 | Task 2's `stat` of `/etc/gitlab/gitlab.rb` is dropped | registered as `gitlab_config_file` and *never referenced* by any later task or handler — dead code in the role itself |
| D4 | The two dependency tasks become one family-dispatched request, the gated cache refresh folded into the apt half | Ubuntu installs the dependencies plus `gnupg2` through `apt.packages(..., update=True, cache_valid_time=600)` (the nfs/docker precedent; the stamp fact means a converged host runs no `apt-get update`); RedHat runs a plain `dnf.packages(dependencies)` — no `gnupg2`, no metadata refresh, because upstream's RedHat branch does neither |
| D5 | `async: 300`/`poll: 5` on the package install is dropped | a reemote request runs to completion and reports when done; the 300-second ceiling is a wall Ansible builds routinely exceed for this multi-GB package |
| D6 | The template task becomes **two** notifying requests | `files.install`'s idempotence is content-only, so a root:root `0600` drift on unchanged content would never converge; the paired `files.file` re-applies the metadata and, because Ansible's template module reports a change on owner/group/mode drift, its name joins `NOTIFYING` (the apache/nfs companion precedent) |
| D7 | Task 10's `failed_when: false` is an unchecked `act` result | the first reconfigure legitimately fails on some installs and the handler's later run is the fix; a barrier result is data, so nothing raises |
| D8 | The handler's `failed_when: gitlab_restart_handler_failed_when \| bool` becomes a real bool field | upstream's value is the string `'gitlab_restart.rc != 0'`, which `\| bool` coerces to `False` — so the handler *never fails by default*, and the migration keeps that as `False`; `True` makes the body raise its own `CommandError` |
| D9 | Task 12's `creates=` gate is a typed `fact(FileExists)` read | the certificate lives behind a mode-`0700` directory the login user cannot traverse, and `FileExists` re-probes exactly that case elevated (issue #70) — no hand-rolled elevated test command |
| D10 | `gitlab_download_validate_certs` is carried and inert | the vendor script is fetched control-side by `files.download`, where TLS verification is the controller's `urllib` policy, not a per-request knob |
| D11 | `gitlab_config_template` keeps its dual meaning controller-side | the literal `gitlab.rb.j2` renders the verbatim template text inlined in the module; any other value is a *controller* filesystem path read and rendered there (the apache vhosts-template precedent) — upstream read it from the role's own `templates/` directory, which is what this preserves |
| D12 | The two `gitlab_nginx_listen_*` gates and `gitlab_extra_settings` become `None`/empty sentinels omitted from the render context | the template's `is defined` checks then behave exactly as they do for a host where the variable was never set; `extra_settings` options keep the upstream `key`/`value`/`type` spellings, with `type: plain` (or any non-string value) rendering unquoted |

## 9. Scoped out

* **Alpine**: upstream has no Alpine branch to migrate — `include_vars` has no
  `vars/Alpine.yml` to load, no task or handler mentions `apk`, and GitLab
  omnibus ships no Alpine packages — so the family guard's exclusion is
  upstream parity, not a scope cut.
* **The role's galaxy CI scaffolding** (molecule, `.travis.yml`, the
  deprecated-namespace meta): never part of the default task path.
* **`gitlab_package_name`** (upstream documents it in the README but the role
  defines no default — `gitlab_package_name | default(gitlab_edition)`): the
  pin is spelled through `edition` + `version`, which covers every name the
  README demonstrates; an unrelated custom name is better served by composing
  `apt.packages` directly.
--->
