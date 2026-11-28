# apache_php_fpm — Apache PHP-FPM (mod_proxy_fcgi) role

| | |
| --- | --- |
| Role | [`geerlingguy.apache-php-fpm`](https://github.com/geerlingguy/ansible-role-apache-php-fpm) |
| Module | [`reemote.roles.apache_php_fpm`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/apache_php_fpm.py) |
| Example | [`examples/roles/apache_php_fpm.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/apache_php_fpm.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `apache_php_fpm` role mapping → `ApachePhpFpmVars` |

<!---
| Waves | 2 fresh / 1 converged (the handler wave disappears when nothing changed) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | none — the role renders nothing, it is two symlinks and one handler |
| Offline test | [`tests/roles/test_apache_php_fpm.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_apache_php_fpm.py) |

`apache_php_fpm` enables `mod_proxy_fcgi` (and its `mod_proxy` prerequisite)
for Apache on Ubuntu hosts: the two `mods-available` → `mods-enabled`
symlinks — the Debian spelling of `a2enmod proxy proxy_fcgi` — and the
`restart apache` handler they notify.

The upstream role is the corpus's smallest: `tasks/main.yml` is a single
two-item `file` loop, and it ships no `defaults`, `vars`, `handlers`,
`templates` or `files` of its own — "Role Variables: None." Both values the
task reads, `apache_server_root` and the notified handler, belong to its
meta-dependency `geerlingguy.apache`, which is itself already migrated as
[`apache`](apache.md). The migration therefore has no new library surface to
build: one `files.link` request per item, and the play-end handler becomes an
in-stream `checkpoint` + `changed_hosts` read:

```python
yield from enable_modules(hv)(host)                     # wave 0
wave_report = yield checkpoint("configure done")
notified = changed_hosts(wave_report, notifying_names())  # notify: restart apache

yield from handler(hv, notified)(host)                  # wave 1, only if notified
```

Nothing is gathered into a snapshot first, and there is deliberately **no
fact, act or control barrier** — the role reads no remote state beyond the
`Link` fact each `files.link` request consults for itself.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **recipe** (a documented
shape change), **deviation** (a deliberate porting choice).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `when: ansible_facts.os_family == 'Debian'` | the family guard is the body's opening `if`, narrowed to `"Ubuntu"` | deviation (D1) |
| `file: src=…/mods-available/proxy.load dest=…/mods-enabled/proxy.load state=link` (item 1) | `files.link(path=…, target=…, sudo=True)` | covered |
| `file: … proxy_fcgi.load … state=link` (item 2) | `files.link(path=…, target=…, sudo=True)` | covered |
| `mode: 0644` on the link | dropped — `files.link` owns no mode; a symlink's own mode is always `0777` | deviation (D2) |
| `notify: restart apache` (the dependency role's handler) | `checkpoint("configure done")` + `changed_hosts` → `service_handler(…, action=restart_state)` | recipe (D4) |
| `apache_server_root` (the dependency role's variable) | `ApachePhpFpmVars.server_root`, defaulting to `/etc/apache2` | deviation (D3) |
| `meta/main.yml` `dependencies: geerlingguy.apache` | a documented prerequisite; compose `apache` with `scoped` for the faithful form | deviation (D3) |

No library gap blocked this migration, so none was filed: `files.link` is
fact-gated on the `Link` fact (a correct link reports no change), and
`changed_hosts` over the checkpoint is the shipped handler channel — the same
plumbing [`apache`](apache.md) uses for its own `restart apache` handler. The
README's RedHat notes ("RHEL 7 automatically installs and enables
mod_proxy_fcgi") describe state to *avoid*, not tasks to port: the role has no
`setup-RedHat.yml`.

## 2. Install

```bash
pip install reemote
```

No extra: the role renders no templates and reads no YAML. SSH access to each
Ubuntu host with the key named in its `connection`, and passwordless sudo (or
a `privilege` section in the inventory): every request carries `sudo=True`,
because the `mods-enabled` directory is root-owned and reemote has no
play-level `become`. Apache itself must already be installed — the upstream
role's meta-dependency is a *prerequisite* here (D3), most faithfully met by
running the [`apache`](apache.md) role first.

-->

::: reemote.roles.apache_php_fpm
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - apache_php_fpm

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `server_root` | `str` | `/etc/apache2` — the Debian `apache_server_root` | `apache_server_root` (dependency role) |
| `service` | `str` | `apache2` — the unit the handler acts on | `apache_service.name` (dependency role) |
| `restart_state` | `Literal` | `restarted` — also `reloaded`, `started`; a bad value is a `ValidationError` | `apache_restart_state` (dependency role) |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. The
upstream role declares no variables of its own, so all three fields carry
values the role read from `geerlingguy.apache` (D3); the model stays frozen
and hashable even though no sequence field needs the `tuple` rule.

## 5. Run it

The role is library code; `examples/roles/apache_php_fpm.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables, builds the deploy from the `apache_php_fpm`
factory and drives it with `run_waves` — a `dry=True` one-wave preview, then
the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import ApachePhpFpmVars, apache_php_fpm

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(ApachePhpFpmVars, "apache_php_fpm", inventory)
    deploy = apache_php_fpm(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/apache_php_fpm.py
```

The shipped `inventory.json` carries no `apache_php_fpm` role mapping, so the
model defaults — `/etc/apache2`, `apache2`, `restarted` — decide the site.
`main()` prints the wave tree and maps the role's failure modes (bad
inventory, `ValueError` from a rejected variable, `CommandError` — including
a handler restart of an apache2 that is absent or cannot bind its ports —
`HostConnectionError`, `PrivilegeError`, `OSError`, `TypeError`) to a
one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change: both
symlinks exist and point at their packaged targets, so the checkpoint names
nobody, the restart request is never emitted, and the handler wave
disappears. That is the migration's idempotence claim, and the offline suite
asserts it.

> **Shared demo host.** The `ubuntu` demo host also runs the other role
> examples, and this role's handler *starts* apache2 — which fails with
> "Address already in use" while nginx owns ports 80/443. Stop nginx before
> the real run and restore it afterwards; the example docstring carries the
> exact sequence.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is
not appended — so the wave count is **host-dependent**, the most so of any
role in the corpus: a converged host runs no second wave at all.

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `enable_modules`: `files.link(proxy.load)`, `files.link(proxy_fcgi.load)` — closed by `checkpoint("configure done")` | the checkpoint **is** the role's `notify: restart apache`: it reads the wave it just closed and names the hosts whose link changed |
| 1 | `handler`: `systemd.service(restarted=True)` on `service`, only on a host the checkpoint named | on a rerun nothing changed, the handler yields no request, and the wave is not appended — the tree collapses to one wave |

Two ordering rules are load-bearing:

* **The checkpoint immediately follows the notifying stage.** One placed
  after the handler wave would read the handler's own entries and notify
  nobody.
* **`restarted` is stateless** — it always reports changed on the pass it
  runs, faithful to Ansible's service module — which is exactly why it must
  stay behind the checkpoint gate rather than ride every converged rerun.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_apache_php_fpm.py::test_tree_snapshot`, against the
simulated Ubuntu host plus a RedHat host the role never touches. The host is
seeded in the prerequisite state (D3): apache2 installed, running and
enabled, neither proxy module enabled. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Enable Apache module proxy.load [changed: /etc/apache2/mods-enabled/proxy.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/proxy.load /etc/apache2/mods-enabled/proxy.load
    ubuntu: Enable Apache module proxy_fcgi.load [changed: /etc/apache2/mods-enabled/proxy_fcgi.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/proxy_fcgi.load /etc/apache2/mods-enabled/proxy_fcgi.load
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart apache2 (handler) [changed: apache2.service already running; apache2.service will restart]
        $ sudo systemctl restart apache2.service
```

The rerun — the checkpoint still runs (it is a read of the wave's real
outcomes), names nobody, and **the handler request disappears entirely**, so
the second wave is not even appended:

```text
(root)
  wave 0
    ubuntu: Enable Apache module proxy.load [no change: link /etc/apache2/mods-enabled/proxy.load already exists]
    ubuntu: Enable Apache module proxy_fcgi.load [no change: link /etc/apache2/mods-enabled/proxy_fcgi.load already exists]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on the request. The role's gate is `os_family == 'Debian'`; the corpus fixes Ubuntu as the target, and the RedHat side of the README is state notes, not tasks |
| D2 | The task's `mode: 0644` on the **symlink** is dropped | `files.link` owns no mode, and a Linux symlink's own mode is always `0777` and meaningless — Ansible's chmod follows the link to the packaged `0644` target anyway, so nothing observable changes (the [`apache`](apache.md) role's D4, verbatim) |
| D3 | `apache_server_root` and the `restart apache` handler become **this role's variables**, and Apache becomes a **prerequisite** | reemote has no role-dependency mechanism; the dependency role is already migrated, so composing `apache` with `scoped` is the faithful form of `dependencies:` |
| D4 | The handler is an in-stream **`checkpoint` + `changed_hosts` gate**, not a play-end flush | `files.link` reports changed only on a real create or re-point, so the restart rides only the pass that changed a link — and on a converged host the handler request, and its whole wave, are absent |
| D5 | `files.link`'s default `create_remote_dir=True` **mkdirs `mods-enabled`** when it is missing, where Ansible's `file` task would fail | on a host meeting the prerequisite (D3) the directory already exists and the two shapes agree; the lenient default also matches what the packaged `apache2` postinst creates |

## 9. Scoped out

* **Non-Ubuntu families.** The role's Debian gate is narrowed to Ubuntu like
  every web-tier role in the corpus; a Debian host that is not Ubuntu is
  skipped, not mapped. The RedHat/CentOS README notes (RHEL 7 ships
  mod_proxy_fcgi already enabled; RHEL 6 needs mod_fastcgi) describe other
  roles' territory, not tasks of this one.
* **Installing Apache and PHP.** The upstream role depends on
  `geerlingguy.apache` and expects PHP-FPM configured elsewhere (its README
  suggests `geerlingguy.php`). Those are the migrated
  [`apache`](apache.md) and [`php`](php.md) roles — this one only opens the
  proxy door between them.
* **The vhost `ProxyPassMatch` lines.** The README's
  `ProxyPassMatch ^/(.*\.php(/.*)?)$ "fcgi://…"` snippets are apache
  *vhost* content — they belong in the `apache` role's vhost
  `extra_parameters`, as its migrated example demonstrates, not in this role.
* **Per-host privilege.** The symlinks and the restart need root and the
  inventory says how a host elevates; there is no play-level `become` to
  reproduce.

-->
