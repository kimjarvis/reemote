# security — SSH, sudoers, Fail2Ban and unattended-upgrades hardening role

| | |
| --- | --- |
| Role | [`geerlingguy.security`](https://github.com/geerlingguy/ansible-role-security) |
| Module | [`reemote.roles.security`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/security.py) |
| Example | [`examples/roles/security.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/security.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `security` role mapping → `SecurityVars` |

<!---
| Waves | 2 changed / 1 converged (the handler wave disappears) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | **none** — all four templates are f-string renders |
| Offline test | [`tests/roles/test_security.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_security.py) |

`security` hardens an Ubuntu host the way the role does: the SSH daemon's eight
security directives plus the optional `AllowUsers`/`AllowGroups` lines, the
passwordless and passworded `/etc/sudoers` entries, Fail2Ban (the package,
`jail.local`, `fail2ban.local`, running and enabled on boot), and
unattended-upgrades (the package, `10periodic`, `50unattended-upgrades`), followed
by the role's three handlers — `reload systemd`, `restart ssh`, `reload fail2ban`.

Like [`nginx`](nginx.md), the whole task list — the OS dispatch, the two config
files behind their validators, the four templates, the two service tasks and the
three handlers — is one per-host generator. Where nginx is the corpus's
two-checkpoint case, security is its **one-checkpoint, three-channel** case: on the
Ubuntu path nothing reads the machine between tasks, so the run is one request wave
and the entire handler mechanism is that one report read three times.

```python
yield from configure_fail2ban(plan)(host)   # wave 0: fail2ban.yml, ssh.yml,
yield from configure_ssh(plan)(host)        #         autoupdate-Debian.yml,
yield from configure_autoupdate(plan)(host)  #        in the role's file order
configure_report = yield checkpoint("configure done")
daemon_reload = changed_hosts(configure_report, daemon_reload_names(plan))
restart = changed_hosts(configure_report, restart_ssh_names(plan))
reload = changed_hosts(configure_report, reload_fail2ban_names(plan))
yield from handler(plan, daemon_reload, restart, reload)(host)   # wave 1
```

No `fact`, `act` or `control` barrier appears, because none of the role's
`ansible_facts` uses survives the Ubuntu target: the `include_vars` dispatch and the
two `distribution_major_version | int >= 12` gates are all settled statically.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **recipe** (a documented shape
change), **barrier** (a read the body consumes), **deviation** (a deliberate
difference), **dead** (never runs in the role either).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars: {{ ansible_os_family }}.yml` | the frozen `SSH_CONFIG_PATH` / `SSHD_NAME` constants, one family guard | deviation (D1) |
| `include_tasks: fail2ban.yml`, `when: security_fail2ban_enabled \| bool` | `configure_fail2ban(plan)`, whose body returns at the gate | covered |
| `include_tasks: ssh.yml` | `configure_ssh(plan)`, unguarded | covered |
| `include_tasks: autoupdate-Debian.yml`, `when: Debian and autoupdate_enabled` | `configure_autoupdate(plan)`, gated on the flag alone | covered (D1) |
| `include_tasks: autoupdate-RedHat.yml` | the family guard | scoped out (D1) |
| `apt: name=fail2ban state=present` (Debian) | `apt.packages(packages=("fail2ban",))` | covered |
| `apt: name=fail2ban enablerepo=epel` (RedHat) | the family guard | scoped out (D1) |
| `template: jail.local.j2 → /etc/fail2ban/jail.local`, notify `reload fail2ban` | control-side `render_jail_local` → `files.install(content=…)` | recipe (D5) |
| `template: fail2ban.local.j2`, `when: Debian and major >= 12`, notify `reload fail2ban` | the static `FAIL2BAN_LOCAL_CONTENT` → `files.install` | covered (D5) |
| `service: name=fail2ban state=started enabled=true` | `systemd.service(running=True, enabled=True)` | covered |
| `service: name={{ security_sshd_name }} state={{ security_sshd_state }}` (no `enabled`, no `notify`) | `systemd.service(running=/restarted=/reloaded=, enabled=None)` | covered (D9) |
| `lineinfile:` ×8 on `sshd_config`, `regexp`/`line`/`mode: 0644`/`validate: 'sshd -T -f %s'`, notify `reload systemd`, `restart ssh` | `files.transform(lineinfile_transform("^<directive>", line), mode="0644", validate="sshd -T -f {spool}")` per item, plus one `files.file(mode="0644")` | recipe (D2) |
| `lineinfile: AllowUsers {{ … \| join(' ') }}`, `create: true`, `when: … \| length > 0`, notify `restart ssh` | one more `files.transform` of the same file, emitted only for a non-empty tuple | covered (D2) |
| `lineinfile: AllowGroups {{ … \| join(' ') }}`, likewise | the same, second | covered (D2) |
| `lineinfile:` loop over `security_sudoers_passwordless`, `regexp: ^{{ item }}`, `line: "{{ item }} ALL=(ALL) NOPASSWD: ALL"`, `validate: 'visudo -cf %s'`, `mode: 0440` | `files.transform(…, path="/etc/sudoers", mode="0440", validate="visudo -cf {spool}")` per user, plus one `files.file(mode="0440")` | recipe (D2) |
| `lineinfile:` loop over `security_sudoers_passworded`, `ALL=(ALL) ALL` | the same, second loop | recipe (D2) |
| `template:` loop `[10periodic, 50unattended-upgrades]` → `/etc/apt/apt.conf.d/{{ item }}`, `root:root 0644`, no notify | two `files.install(content=…)` requests, `user="root"`/`group="root"`/`mode="0644"` | recipe (D5) |
| `apt: name=unattended-upgrades state=present` | `apt.packages(packages=("unattended-upgrades",))` | covered |
| `handlers: reload systemd` (`systemd_service: daemon_reload: true`) | `checkpoint` → `changed_hosts` → `systemd.daemon_reload()` with **no unit** | recipe (D4) |
| `handlers: restart ssh` (`state: {{ security_ssh_restart_handler_state }}`) | the same report, a second channel → `service_handler(action=…)` | recipe (D3) |
| `handlers: reload fail2ban` (`state: reloaded`) | the same report, a third channel → `service_handler(action="reloaded")` | recipe (D3) |

No library gap blocked this migration, so none was filed. `files.transform` is
already the `lineinfile` mapping (the spool-and-install edit of a root-owned file,
with a `validate` hook that accepts the spool), `apt.packages` the `apt` task,
`systemd.daemon_reload` the daemon-reload handler verb, and `changed_hosts` over a
`checkpoint` the whole `notify` mechanism — three channels need no third report. The
role's four templates need no renderer: each is a handful of substitutions.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

The role needs **no** `reemote[templates]` extra. All four templates render as
plain f-strings — `jail.local` is one variable, `fail2ban.local` and `10periodic`
are static, `50unattended-upgrades` is two scalars and two loops — and the
`sshd_config`/`sudoers` edits are pure text transforms, so nothing imports `jinja2`
(deviation D6 accepts only the bundled `jail.local.j2`).

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request carries
`sudo=True`, because reemote has no play-level `become`. Both protected files are
read *and* written elevated — `/etc/sudoers` is `0440` root-owned, which the login
user cannot read at all.

> **This role can end your SSH session.** It writes `PasswordAuthentication no` and
> friends by default, may append an `AllowUsers` line, and its `restart ssh` handler
> restarts the daemon you are connected with. Keep a live session open until a fresh
> login works, and never point `ssh_port` at a port the firewall does not allow.

## 3. Variables

`SecurityVars` is a frozen pydantic model (`extra="forbid"`,
`coerce_numbers_to_str=True`) whose fields carry bare names; the role name passed to
`resolve_vars` binds the entry's `security` role mapping, so an inventory written for
the role needs no key gymnastics.

--->

::: reemote.roles.security
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - security

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `ssh_port` | `str` | `22` (coerced) — the `Port` directive *and* the jail's `port` | `security_ssh_port` |
| `ssh_password_authentication` | `str` | `no` | `security_ssh_password_authentication` |
| `ssh_permit_root_login` | `str` | `no` | `security_ssh_permit_root_login` |
| `ssh_usedns` | `str` | `no` | `security_ssh_usedns` |
| `ssh_permit_empty_password` | `str` | `no` | `security_ssh_permit_empty_password` |
| `ssh_challenge_response_auth` | `str` | `no` | `security_ssh_challenge_response_auth` |
| `ssh_gss_api_authentication` | `str` | `no` | `security_ssh_gss_api_authentication` |
| `ssh_x11_forwarding` | `str` | `no` | `security_ssh_x11_forwarding` |
| `sshd_state` | `Literal["started","stopped","restarted","reloaded"]` | `started` | `security_sshd_state` |
| `ssh_restart_handler_state` | `Literal["started","restarted","reloaded"]` | `restarted` — `started` maps to the `"running"` action | `security_ssh_restart_handler_state` |
| `ssh_allowed_users` | `tuple[str, …]` | `()` → no `AllowUsers` line at all | `security_ssh_allowed_users` |
| `ssh_allowed_groups` | `tuple[str, …]` | `()` → no `AllowGroups` line | `security_ssh_allowed_groups` |
| `sudoers_passwordless` | `tuple[str, …]` | `()` | `security_sudoers_passwordless` |
| `sudoers_passworded` | `tuple[str, …]` | `()` | `security_sudoers_passworded` |
| `autoupdate_enabled` | `bool` | `true` — gates the whole Debian file | `security_autoupdate_enabled` |
| `autoupdate_blacklist` | `tuple[str, …]` | `()` | `security_autoupdate_blacklist` |
| `autoupdate_additional_origins` | `tuple[str, …]` | `()` — extra `Origins-Pattern` entries, verbatim | `security_autoupdate_additional_origins` |
| `autoupdate_reboot` | `str` | `"false"` — a string, not a bool (D7) | `security_autoupdate_reboot` |
| `autoupdate_reboot_time` | `str` | `03:00` | `security_autoupdate_reboot_time` |
| `autoupdate_mail_to` | `str` | `""` → both Mail directives omitted | `security_autoupdate_mail_to` |
| `autoupdate_mail_on_error` | `bool` | `true` — only read when a recipient is set | `security_autoupdate_mail_on_error` |
| `fail2ban_enabled` | `bool` | `true` — gates the whole `fail2ban.yml` | `security_fail2ban_enabled` |
| `fail2ban_custom_configuration_template` | `str` | `jail.local.j2` — **any other value raises** (D6) | `security_fail2ban_custom_configuration_template` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. Sequences are
`tuple`, never `list`: the model is frozen and hashable, and a value that reaches
`op()` must hash. The two `vars/Debian.yml` inputs — `security_ssh_config_path` and
`security_sshd_name` — are deliberately **absent** as fields: upstream
`include_vars` sets them above any inventory value, so they are module constants and
a key naming either raises `RoleVarsError` rather than silently redirecting the role
(D1).

Values that land verbatim in a config file stay **strings**, exactly as
`defaults/main.yml` spells them (`"no"`, `"false"`, `"03:00"`), with
`coerce_numbers_to_str` so an inventory writing `ssh_port: 2222` renders `Port 2222`
rather than failing. `autoupdate_reboot` is *not* a `bool`: the file needs apt's
own `"true"`/`"false"` spelling, and a Python `True` would render `True`.

The gates the model enforces are the role's implicit ones (D8): `sshd_state` and
`ssh_restart_handler_state` are `Literal`s, an `ssh_allowed_users`/`ssh_allowed_groups`
member must be one whitespace-free token (both lines are the list `| join(' ')`), a
sudoers name must be a plain name (it is interpolated into `^{{ item }}` *and* a
sudoers line, so whitespace, `:` or `#` would split or inject one), and
`fail2ban_custom_configuration_template` accepts only the bundled name. Each raises
`ValidationError` — a `ValueError` — while the inventory is resolved, before a host
is contacted.

## 5. Run it

The role is library code; `examples/roles/security.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables
(the defaults overlaid with the entry's `security` role mapping, then the call-site
`overrides`), builds the deploy from the `security` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the real apply — printing the wave
tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import SecurityVars, security

SECURITY_OVERRIDES = {"ssh_usedns": "no", "ssh_allowed_users": ("supervisor",)}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        SecurityVars, "security", inventory, overrides=SECURITY_OVERRIDES
    )
    deploy = security(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/security.py
```

The `ubuntu` entry of the shipped `inventory.json` carries no `security` role
mapping, so the program's call-site `overrides` decide the run: `UseDNS no` and
`AllowUsers supervisor` — the demo host's login user, which is the safe value,
because an `AllowUsers` line that omits the user you connect as locks you out.
Everything else keeps the upstream defaults: port `22`, the four `"no"`
authentication directives, Fail2Ban and unattended-upgrades enabled, both sudoers
lists empty.

The `dry=True` pass is a one-wave preview: it plans the entire configure stage,
parks on the `checkpoint("configure done")` barrier and stops (`barrier: configure
done → unknown until run`), applying nothing — so it deliberately says nothing about
which of the three handlers would fire.

**Run the program twice.** The second run must report no change and queue no
handler: both packages are installed, every directive line is present, both file
modes are set, `jail.local`/`fail2ban.local`/`10periodic`/`50unattended-upgrades`
are byte-identical, and both units are in their configured state. That is the
migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy, two waves on a host that needs work, one on a converged host. Each
barrier closes its own wave, and a wave with no records is not appended:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fail2ban.yml` + `ssh.yml` + `autoupdate-Debian.yml`, in the role's own order: two `apt.packages`, four `files.install`, two `systemd.service`, twelve `files.transform`, two `files.file` — closed by `checkpoint("configure done")` | nothing in the Ubuntu path reads the host between tasks, so the whole task list is one wave; the checkpoint **is** the role's `notify:` mechanism |
| 1 | `handler`: `systemd.daemon_reload`, then `service_handler(restart_notified, ("ssh",), action=…)`, then `service_handler(reload_notified, ("fail2ban",), action="reloaded")` | only on the hosts each channel selected, in handler-definition order |

Four rules are load-bearing:

* **Ten edits of one file, one wave, no barrier between them.** The driver plans and
  applies each request in its own step against a *fresh fact cache*, so every
  `files.transform` of `/etc/ssh/sshd_config` reads the file as the previous request
  left it: the eight hardening directives, `AllowUsers` and `AllowGroups` all land,
  each exactly once. Splitting them across waves would cost nine more checkpoints and
  buy nothing.
* **One checkpoint, three `changed_hosts` reads** (§18.3). `daemon_reload_names`
  (the eight directives plus the `sshd_config` mode request), `restart_ssh_names`
  (that set plus the two access lines) and `reload_fail2ban_names` (the two Fail2Ban
  files) are three name sets over the *same* report. The sudoers edits and both
  service tasks notify nothing, faithfully, so a sudoers-only change produces no
  handler request and no handler wave.
* **Handlers run in definition order, not notification order.** Ansible flushes
  handlers in the order `handlers/main.yml` defines them, so the composed deploy
  yields `reload systemd`, then `restart ssh`, then `reload fail2ban` — which is why
  this is one handler deploy yielding three sub-deploys rather than a `compose` of
  the notification order.
* **The plan is decided before the first request.** `resolve_plan` turns the
  variables into the edits, the renders and the channel names, so a bad value raises
  with nothing applied, and each request's `name=` is that name — the stable id a
  channel selects on.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`, so it
emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_security.py::test_tree_snapshot`, against the simulated
Ubuntu host plus a RedHat host the role never touches. The inventory's `security`
role mapping sets both access lists, both sudoers loops, the mail recipient and one
blacklist and origins entry each. Fresh host, first pass (abridged to one line per
request; the offline test pins the whole tree, commands included):

```text
(root)
  wave 0
    ubuntu: Install fail2ban (Debian). [changed: 1 package(s) to install]
    ubuntu: Copy jail custom configuration file into place. [changed: content /etc/fail2ban/jail.local is absent, will install]
    ubuntu: Copy fail2ban custom configuration file into place. [changed: content /etc/fail2ban/fail2ban.local is absent, will install]
    ubuntu: Ensure fail2ban is running and enabled. [changed: fail2ban.service not running (None), will start; fail2ban.service not enabled, will enable]
    ubuntu: Ensure SSH daemon is running. [changed: ssh.service not running (None), will start]
    ubuntu: Update SSH configuration to be more secure. (PasswordAuthentication) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (PermitRootLogin) [changed: content /etc/ssh/sshd_config differs, will install]
    … five more directives, each through the same spool …
    ubuntu: Add configured users allowed to connect over ssh [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Add configured groups allowed to connect over ssh [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Set sshd_config mode     [no change: file /etc/ssh/sshd_config already exists]
    ubuntu: Add configured user accounts to passwordless sudoers. (runner) [changed: content /etc/sudoers differs, will install]
        $ scp <files install> -> /tmp/reemote-install-67e88f05f330da88/content
        $ sudo visudo -cf /tmp/reemote-install-67e88f05f330da88/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-67e88f05f330da88/content /etc/sudoers && chmod 440 /etc/sudoers && rm -rf /tmp/reemote-install-67e88f05f330da88'
    ubuntu: Add configured user accounts to passworded sudoers. (admin) [changed: content /etc/sudoers differs, will install]
    ubuntu: Set sudoers mode         [no change: file /etc/sudoers already exists]
    ubuntu: Install unattended upgrades package. [changed: 1 package(s) to install]
    ubuntu: Copy unattended-upgrades configuration files in place. (10periodic) [changed: content /etc/apt/apt.conf.d/10periodic is absent, will install]
    ubuntu: Copy unattended-upgrades configuration files in place. (50unattended-upgrades) [changed: content /etc/apt/apt.conf.d/50unattended-upgrades is absent, will install]
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Reload systemd (handler) [changed: daemon-reload]
        $ sudo systemctl daemon-reload
    ubuntu: Restart ssh (handler)    [changed: ssh.service already running; ssh.service will restart]
        $ sudo systemctl restart ssh.service
    ubuntu: Reload fail2ban (handler) [changed: fail2ban.service already running; fail2ban.service will reload]
        $ sudo systemctl reload fail2ban.service
```

Every `sshd_config` edit shares one spool directory (`fbe6462bc7553466` is the
content address of `/etc/ssh/sshd_config`), and the `sudo sshd -T -f …` line is the
transform's `validate` hook running against that spool *before* the elevated `cp`
lands it — a rejected directive never reaches the file, and the run aborts with a
`CommandError` instead. The same holds for `visudo -cf` on `sudoers`, which is
`0440` root-owned and therefore read elevated too (the test pins that the login user
cannot read it).

The rerun — no facts to gather, the checkpoint finds nothing, and **the handler wave
disappears entirely** (one wave, not two):

```text
(root)
  wave 0
    ubuntu: Install fail2ban (Debian). [no change: all packages present]
    ubuntu: Copy jail custom configuration file into place. [no change: content already up to date (/etc/fail2ban/jail.local)]
    ubuntu: Copy fail2ban custom configuration file into place. [no change: content already up to date (/etc/fail2ban/fail2ban.local)]
    ubuntu: Ensure fail2ban is running and enabled. [no change: fail2ban.service already running; fail2ban.service already enabled]
    ubuntu: Ensure SSH daemon is running. [no change: ssh.service already running]
    ubuntu: Update SSH configuration to be more secure. (PasswordAuthentication) [no change: content already up to date (/etc/ssh/sshd_config)]
    … the other nine edits, all no-ops …
    ubuntu: Set sshd_config mode     [no change: file /etc/ssh/sshd_config already exists]
    ubuntu: Add configured user accounts to passwordless sudoers. (runner) [no change: content already up to date (/etc/sudoers)]
    ubuntu: Add configured user accounts to passworded sudoers. (admin) [no change: content already up to date (/etc/sudoers)]
    ubuntu: Set sudoers mode         [no change: file /etc/sudoers already exists]
    ubuntu: Install unattended upgrades package. [no change: all packages present]
    ubuntu: Copy unattended-upgrades configuration files in place. (10periodic) [no change: content already up to date (/etc/apt/apt.conf.d/10periodic)]
    ubuntu: Copy unattended-upgrades configuration files in place. (50unattended-upgrades) [no change: content already up to date (/etc/apt/apt.conf.d/50unattended-upgrades)]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in no tree: no barrier record, no request line. The two
`Set … mode` requests are the only rows that report no change on the *first* pass —
the packaged files already carry `0644` and `0440`.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all**, and with it the RedHat half of the role | the family guard is the body's opening `if`, not a `when=` on each request. `include_vars`' OS dispatch collapses to the frozen `SSH_CONFIG_PATH`/`SSHD_NAME`, so `autoupdate-RedHat.yml`, the `enablerepo: epel` Fail2Ban install and `vars/RedHat.yml`'s `sshd` unit name are dropped rather than mapped |
| D2 | `lineinfile` becomes **`files.transform` plus a paired `files.file`** | reemote ships no `lineinfile` operation by design; the spool-and-install transform of a root-owned file is its home for this shape, with each task's `regexp`/`line` compiled into a pure fixpoint. Transform idempotence is *content*-only, so the tasks' `mode:` needs the metadata companion request — and that request joins the hardening channel, because upstream each `lineinfile` reports `changed` on a mode drift. `validate: '… %s'` becomes `validate: '… {spool}'`, and `create: true` needs no analogue |
| D3 | `notify:` becomes **three `changed_hosts` channels over one checkpoint**, flushed in definition order | one report, three name sets, never a second checkpoint. Ansible runs handlers in the order they are *defined*, not notified, so the handler deploy yields `reload systemd` → `restart ssh` → `reload fail2ban`. `security_ssh_restart_handler_state` maps onto `service_handler`'s `action`, with `started` → `"running"` |
| D4 | `reload systemd` is `systemd.daemon_reload` with **no unit**, hand-gated on its channel | the handler passes `daemon_reload: true` with no unit name, i.e. an unconditional `systemctl daemon-reload`. `service_handler(daemon_reload=True)` is deliberately *not* used: it is state-gated on a unit reporting `NeedDaemonReload` (issue #50), and an `sshd_config` edit touches no unit file, so it would never fire. Convergence comes from the notification gate |
| D5 | All four templates render as **plain f-strings**, control-side | reemote ships no Jinja2, and each of these files is a handful of substitutions and loops — no filter, no whitespace trickery beyond a block tag's own dropped newline, which the render function reproduces. `${distro_codename}`/`${distro_id}` stay literal passthroughs: `unattended-upgrades` substitutes them when apt reads the file, not Ansible. The `major >= 12` gates are unconditionally true on Ubuntu (22/24), so `backend = systemd` and `fail2ban.local` are always written |
| D6 | `security_fail2ban_custom_configuration_template` accepts only the bundled `jail.local.j2` | the bundled template is inlined; any other value raises while the variables are resolved, rather than silently ignoring the operator's file. A custom template is a Jinja2 file, and this migration takes on no template dependency — `apache` and `nginx`, which do render custom templates, are the roles that carry the extra |
| D7 | The Ansible values that are *strings* stay strings | `ssh_password_authentication: "no"`, `autoupdate_reboot: "false"`, `autoupdate_reboot_time: "03:00"` so a rendered line is byte-identical to the template's; a Python `bool` would write `True`. `ssh_port` is coerced from `22`, and the list vars freeze to tuples |
| D8 | The role's implicit gates become **pydantic validation** | an unknown service or handler state, an `AllowUsers` member containing whitespace, a sudoers name containing `:`/`#`/space, a custom jail template: each raises `ValidationError` (a `ValueError`) in `resolve_vars`, before any host is contacted. Upstream, all four fail later or silently — a sudoers name with a space writes a broken line that only `visudo` catches |
| D9 | The sshd service task sets no `enabled` | the role's `service:` task passes only `state:`, so `enabled=None` leaves the boot setting alone; only the Fail2Ban task manages it. `started`/`stopped` are fact-gated and converge, `restarted`/`reloaded` are stateless (always changed), faithful to Ansible's `service` module |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat branches — `autoupdate-RedHat.yml`, the
  `enablerepo: epel` Fail2Ban install, `vars/RedHat.yml` (whose `sshd` unit name and
  `yum` package the Debian path never sees) — are not migrated. A RedHat host is
  skipped, not mapped to a different package manager.
* **A custom `jail.local` template.** `fail2ban_custom_configuration_template`
  raises on any other value (D6). A site-specific jail file is ordinary Python here:
  render the string yourself and `files.install` it, or carry the whole jail
  configuration in the Fail2Ban role you own.
* **Other Fail2Ban jails.** The role writes exactly one `[sshd]` section from four
  variables, and this migration writes exactly the same. Additional jails belong in a
  file the role never touches.
* **`ssh.socket` activation.** On Ubuntu 24.04 the daemon is socket-activated, so
  restarting `ssh.service` does not restart `ssh.socket`, and a changed `Port` may not
  take effect until the socket is restarted too. That is a behaviour of the upstream
  role, and it is reproduced rather than corrected: the role has no `ssh.socket` task.
* **`security_sshd_name` / `security_ssh_config_path`.** Frozen constants (D1), not
  variables: upstream `include_vars` overrides an inventory value for both, so
  making them settable would diverge from the role in the other direction.
* **Removing other sshd directives.** The role manages exactly ten lines of
  `sshd_config` and nothing else in it; an unrelated directive the operator added is
  left byte-identical, comment lines included.

--->
