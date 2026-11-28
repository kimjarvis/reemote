# mailhog — mailhog role

| | |
| --- | --- |
| Role | [`geerlingguy.mailhog`](https://github.com/geerlingguy/ansible-role-mailhog) |
| Module | [`reemote.roles.mailhog`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/mailhog.py) |
| Runner | [`examples/roles/mailhog.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/mailhog.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family, systemd only |
| Variables | the entry's `mailhog` role mapping → `MailhogVars` |

<!---
| Waves | exactly 2 — the `Arch` fact gate, then the five requests (a dry pass parks at the gate, 1 wave) |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed — the unit renders as an f-string) |
| Offline test | [`tests/roles/test_mailhog.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_mailhog.py) |
| Factory | plain (non-`async`) — the runner calls `mailhog(v)`; the binary fetches live *inside* `files.download`, not in a `control` barrier |

`mailhog` installs [MailHog](https://github.com/mailhog/MailHog), a Go-based
SMTP server and web UI that captures email instead of delivering it, plus its
`mhsendmail` sendmail shim, on Ubuntu hosts: the two pinned release binaries
downloaded into `/opt/mailhog` and the `mailhog.service` systemd unit that
runs and enables the daemon.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("mailhog", mailhog(v))(host)` and a run is one `run_waves`
pass. The Ansible role's whole task list is that single deploy, and its
`defaults/main.yml` is `MailhogVars`.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_tasks` `service_mgr != systemd` init branch (+ `geerlingguy.daemonize` dependency) | family guard + systemd-only constants — the branch is statically skipped | deviation (D1) |
| `ansible_architecture` → `'arm' if … == 'aarch64' else 'amd64'` | `yield fact(server.Arch)` + `arch_for` | barrier |
| `file: path={{ mailhog_install_dir }} state: directory owner=root group=root mode=0755` | `files.directory(install_dir, mode="0755", sudo=True)` | covered |
| `get_url:` `with_items` loop over the two binaries (`mode: 0755`) | two `files.download(url, dest, mode="0755", sudo=True)` requests, one per item | deviation (D2/D3) |
| `template: src=mailhog.init.j2` (non-systemd only) | not shipped — the target family runs systemd | scoped out (D1) |
| `template: src=mailhog.unit.j2 dest=/etc/systemd/system/mailhog.service mode=0755` | control-side f-string render (`render_unit`) → `files.install(content, dest, mode="0755", sudo=True)` | recipe (D5/D6) |
| `service: name=mailhog state=started enabled=yes` | `systemd.service(running=True, enabled=True, daemon_reload=True, sudo=True)` | deviation (D7) |
| `handlers/main.yml` | absent upstream — no checkpoint, no handler | n/a (D8) |

The migration surfaced **no new library gaps**: `files.download` is the
`get_url` equivalent (control-side fetch, binary spool-install), the `fact`
barrier carries the architecture read that used to come from Ansible's
implicit fact gathering, and `systemd.service`'s `daemon_reload` is
state-gated, which reproduces what Ansible's service module does behind the
scenes after a unit-file change.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

No `reemote[templates]` — the unit template is pure substitution (one
interpolation of the install directory), so `render_unit` reproduces
`mailhog.unit.j2` byte-for-byte with an f-string.

The binaries are fetched **control-side** by `files.download`, so the control
machine needs outbound HTTPS to `github.com` (or to the mirrors named by the
`binary_url` overrides); the host downloads nothing. SSH access to each
Ubuntu host with passwordless sudo (or a `privilege` section in the
inventory): every request carries `sudo=True`, because reemote has no
play-level `become`. The installed binaries are read back unprivileged for
the idempotence comparison, so the `0755` mode the role installs at is also
what keeps the second pass cheap — a login-unreadable `install_dir` mode
would break convergence, exactly as it would break Ansible's `get_url` read.

## 3. Variables

`MailhogVars` is a frozen pydantic model whose fields carry bare names; the
role name passed to `resolve_vars` binds the entry's `mailhog` role mapping.

--->

::: reemote.roles.mailhog
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - mailhog

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `install_dir` | `str` | `/opt/mailhog` | `mailhog_install_dir` |
| `version` | `str` | `1.0.0` | `mailhog_version` |
| `binary_url` | `str \| None` | `None` (derived) | `mailhog_binary_url` |
| `mhsendmail_version` | `str` | `0.2.0` | `mhsendmail_version` |
| `mhsendmail_binary_url` | `str \| None` | `None` (derived) | `mhsendmail_binary_url` |

<!---

`mailhog_daemonize_bin_path` is deliberately absent: it only feeds the init
script, which is scoped out (D1).

Precedence is `model defaults < inventory role mapping < overrides`.
Validation is fail-fast, before any host is contacted: `install_dir` must be
a non-empty absolute path (a trailing `/` is stripped), each version a plain
non-empty release name (a leading `v` is stripped, so the URL formula keeps
its single-`v` convention), each full URL override an `http(s)` URL, and
`extra="forbid"` plus `resolve_vars`' binding report a key that names no
field. Unlike `kubectl` there is no fail-fast architecture map: the role's
own ternary maps every non-`aarch64` machine to `amd64`, which is what
MailHog publishes (D4), so no architecture raises.

## 5. Run it

The role is library code; `examples/roles/mailhog.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables, builds the deploy from the `mailhog` factory
and drives it with `run_waves` — a `dry=True` one-wave preview, then the
apply, then a converged rerun — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import MailhogVars, mailhog

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(MailhogVars, "mailhog", inventory)
    deploy = mailhog(vars_set)                                    # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)            # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/mailhog.py
```

The `dry=True` pass parks at the `Arch` gate (`barrier: Arch → unknown
until run`): the plan needs the machine's architecture before it can name
the two download URLs, so the dry preview shows no request at all — like
`ansible --check` without facts. The rerun against a converged host is the
honest preview.

**Run the program twice.** The second run must report no change: the
directory exists, both binaries' content matches what the URLs serve, the
unit file is the rendered text, the service is up and enabled — and no
`daemon-reload` is issued, because the reload is state-gated (D7). That is
the migration's idempotence claim, and the offline suite asserts it —
including that the converged pass still fetches both URLs control-side to
make the comparison (D3).

## 6. What it does

One deploy, exactly two waves on every non-dry pass:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(server.Arch)` | the role's `ansible_architecture` read — the one barrier; its answer feeds `arch_for` and both download URLs |
| 1 | `files.directory` + `files.download` ×2 + `files.install` + `systemd.service` requests | the role's five systemd-path tasks in task order; the body ends, so the wave closes with the stream |

The wave count does not move with the host: fresh and converged passes both
run two waves, and a dry pass parks after wave 0. There is **no checkpoint
and no handler**: the role ships no `handlers/main.yml`, and nothing between
its tasks reads remote state — replacing a running binary does not restart
the service upstream either (D8). A host whose `host.family` is not
`"Ubuntu"` returns before its first `yield`, so it emits no requests, no
barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_mailhog.py`, against the simulated Ubuntu host
(`ubuntu`, `arch = "x86_64"`) plus a CentOS host the role never touches.
Fresh host, first pass (the two `files.download` spool-installs are named
after their destinations; the unit install carries the rendered text):

```text
(root)
  wave 0
    barrier: Arch → x86_64
  wave 1
    ubuntu: Ensure mailhog install directory exists. [changed: /opt/mailhog missing, will create]
        $ sudo mkdir -p /opt/mailhog
        $ sudo chmod 755 /opt/mailhog
        $ sudo chown root:root /opt/mailhog
    ubuntu: Download the MailHog binary. [changed: content /opt/mailhog/mailhog is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /opt/mailhog && cp /tmp/reemote-install-…/content /opt/mailhog/mailhog && chown root:root /opt/mailhog/mailhog && chmod 755 /opt/mailhog/mailhog && rm -rf /tmp/reemote-install-…'
    ubuntu: Download the mhsendmail binary. [changed: content /opt/mailhog/mhsendmail is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /opt/mailhog && cp /tmp/reemote-install-…/content /opt/mailhog/mhsendmail && chown root:root /opt/mailhog/mhsendmail && chmod 755 /opt/mailhog/mhsendmail && rm -rf /tmp/reemote-install-…'
    ubuntu: Copy mailhog systemd unit file into place. [changed: content /etc/systemd/system/mailhog.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-…/content /etc/systemd/system/mailhog.service && chown root:root /etc/systemd/system/mailhog.service && chmod 755 /etc/systemd/system/mailhog.service && rm -rf /tmp/reemote-install-…'
    ubuntu: Ensure mailhog is enabled and will start on boot. [changed: daemon-reload; mailhog.service not running (None), will start; mailhog.service not enabled, will enable]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start mailhog.service
        $ sudo systemctl enable mailhog.service
```

The rerun — the `Arch` gate still closes wave 0, all five requests report no
change, and the `daemon-reload` is gone because nothing marked the unit
pending:

```text
(root)
  wave 0
    barrier: Arch → x86_64
  wave 1
    ubuntu: Ensure mailhog install directory exists. [no change: directory /opt/mailhog already exists]
    ubuntu: Download the MailHog binary. [no change: content already up to date (/opt/mailhog/mailhog)]
    ubuntu: Download the mhsendmail binary. [no change: content already up to date (/opt/mailhog/mhsendmail)]
    ubuntu: Copy mailhog systemd unit file into place. [no change: content already up to date (/etc/systemd/system/mailhog.service)]
    ubuntu: Ensure mailhog is enabled and will start on boot. [no change: mailhog.service already running; mailhog.service already enabled]
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
| D1 | The `service_mgr` dispatch freezes to systemd: the `mailhog.init.j2` branch, the `mailhog_daemonize_bin_path` default and the `geerlingguy.daemonize` role dependency are scoped out | the target family's servers run systemd; an Ubuntu host without systemd would need the init script, which the migration does not ship |
| D2 | The binaries are fetched **control-side** by `files.download` (spool-installed) | `get_url` downloads on the target; reemote's URL operations fetch control-side by design, so the control machine needs egress to GitHub and the host downloads nothing |
| D3 | Idempotence is content-only and **unchecksummed**, exactly as the role's `get_url` (no `checksum` argument): a converged pass re-fetches each URL to compare, and a pure permission drift on an installed binary is not re-applied | the role pins no digests; adding checksum pins would refuse releases the role accepts. To pin, set the full `binary_url` against a mirrored, digested copy in your own deploy |
| D4 | The `mailhog_arch` default freezes to `arch_for`, the role's literal ternary: `aarch64` → `arm`, everything else → `amd64` | MailHog publishes only those two Linux archives; unlike `kubectl` there is no fail-fast map to invent, and an unmapped machine gets the role's own answer |
| D5 | The unit template renders as an f-string (`render_unit`), byte-identical to the Jinja2 output including the single trailing newline | the template interpolates exactly one variable, so no Jinja2 — and no `reemote[templates]` extra — is needed |
| D6 | The unit file keeps the role's unusual `mode: 0755` rather than normalizing to `0644` | byte-level fidelity to what the role leaves on disk; systemd reads `/etc/systemd/system` itself, so the executable bit is meaningless but harmless |
| D7 | `service: state=started enabled=yes` becomes one `systemd.service` request carrying `daemon_reload=True` | Ansible's service module re-reads unit files behind the scenes after a template task wrote one; reemote's reload is state-gated on `SystemdNeedDaemonReload`, so a converged host issues no `daemon-reload` — the same net behaviour in one request |
| D8 | No handlers: changing the unit or replacing a running binary does not restart a service that is already up | the upstream role ships no `handlers/main.yml`; adding a restart handler would be an improvement, not a migration |
| D9 | The role's tasks carry no per-task `become` (plays are expected to escalate); here every request is flagged `sudo=True` | reemote has no play-level `become`; the run-as identity comes from the inventory `privilege` section, never from an operation argument |

## 9. Scoped out

* **The init-script branch** (`mailhog.init.j2`, upstart/sysvinit) and with it
  the `geerlingguy.daemonize` dependency and `mailhog_daemonize_bin_path`.
  See D1 — the migration fixes Ubuntu-with-systemd as its target.
* **Checksum-pinned downloads.** The role pins no digests (D3); a digested
  mirror is expressible today via the full `binary_url` knobs plus whatever
  pinning your own deploy adds around it.
* **MailHog's own runtime knobs** (`mailhog_api_addr`, `-smtp-bind-addr`
  flags and the like). The upstream unit hardcodes the defaults — the
  shipped `ExecStart` carries no arguments — so they are not variables here
  either; customising them means overriding the unit in a parent deploy.
* **Non-Ubuntu families.** The family guard drops them silently, as every
  shipped role does; the role's own support matrix is wider.

--->
