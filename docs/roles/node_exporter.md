# node_exporter — Prometheus node metrics exporter role

| | |
| --- | --- |
| Role | [`geerlingguy.node_exporter`](https://github.com/geerlingguy/ansible-role-node_exporter) |
| Module | [`reemote.roles.node_exporter`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/node_exporter.py) |
| Runner | [`examples/roles/node_exporter.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/node_exporter.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `node_exporter` role mapping → `NodeExporterVars` |

<!---
| Waves | 3 fresh / 3 converged (4 fresh when `version: "latest"` resolves) — `act` probe, request wave + checkpoint, handler + metrics fact |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed — the unit file is pure substitution, so it renders as an f-string; D12) |
| Offline test | [`tests/roles/test_node_exporter.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_node_exporter.py) |
| Factory | plain (non-`async`) — the runner calls `node_exporter(v)`; the two control-side fetches (the releases API, the tarball inside `files.unarchive`) ride barriers and the operation's own seam |

`node_exporter` installs [node_exporter](https://github.com/prometheus/node_exporter),
Prometheus' hardware-and-OS metric exporter, on Ubuntu hosts: the release
tarball unpacked in `/tmp`, the binary moved to `/usr/local/bin`, the
`node_exporter` group and user (with the optional fixed uid/gid), the rendered
`node_exporter.service` unit, the running and enabled unit, and a check that
the metrics endpoint actually answers.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("node_exporter", node_exporter(v))(host)` and a run is one
`run_waves` pass. The factory is the whole task list, and `NodeExporterVars` is
`defaults/main.yml`.

The role matters as a shape demonstration: its body carries **all four barrier
types** — an `act` probe that decides whether half the task list runs at all, a
`control` fetch that resolves `latest`, a `checkpoint` that reads the handler
notification, and a `fact` that verifies the running service.

## 1. What it is, and where it came from

The role's ten tasks, its one handler and its included `config-version.yaml`
map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `command: "{{ node_exporter_bin_path }} --version"` with `failed_when: false`, `changed_when: false`, `register` | `yield act(Shell, command=probe_command(hv))` — the `Result` is data, so a missing binary is an empty `stdout` | barrier |
| `include_tasks: config-version.yaml` (`when: version is match("latest")`) | `if version == LATEST:` around the fetch — no include file, no `when` on the request | deviation (D3) |
| `uri: url=api.github.com/…/releases/latest` with `delegate_to: localhost`, `run_once`, `until: status == 200`, `retries: 5` | `yield control(files.fetch_url, RELEASE_API_URL)` + `parse_release_tag` | deviation (D2) |
| `set_fact: node_exporter_version` / `node_exporter_download_url` | ordinary locals in the body + `download_url_for` / `resolve_download_url` | deviation (D3) |
| `unarchive: src=<url> dest=/tmp remote_src: true mode: 0755` + the probe's `when` | `files.unarchive(src=url, dest="/tmp", creates=archive_dir(…), sudo=True)`, emitted only when the probe misses | deviation (D4, D5) |
| `copy: src=/tmp/node_exporter-<ver>.linux-<arch>/node_exporter dest=<bin_path> mode: 0755 remote_src: true` + `notify` | `files.copy(…, mode="0755", overwrite=True, sudo=True)`, named `MOVE_NAME` — a notifier | deviation (D6) |
| `group: name=node_exporter gid={{ … \| default(omit) }}` `when: gid is defined` | `server.group(group="node_exporter", gid=hv.gid, sudo=True)`, yielded only when `hv.gid is not None` | covered (D11) |
| `user: name=node_exporter shell=/sbin/nologin create_home: false uid/group … \| default(omit)` | `server.user(…, shell="/sbin/nologin", ensure_home=False, group=… \| None, uid=…, sudo=True)` | covered (D10, D11) |
| `template: src=node_exporter.service.j2 dest=/etc/systemd/system/node_exporter.service mode: 0644` + `register` + `notify` | control-side `render_unit(hv)` (f-string) → `files.install(content=unit, …, mode="0644", sudo=True)`, named `UNIT_NAME` — a notifier | deviation (D12) |
| `systemd: daemon_reload: true` `when: node_exporter_service is changed` | folded into the service request's `daemon_reload=True`, state-gated on `SystemdNeedDaemonReload` | deviation (D7) |
| `service: name=node_exporter state={{ … }} enabled={{ … }}` | `systemd.service(service="node_exporter", enabled=hv.enabled, daemon_reload=True, **STATE_KEYWORDS[hv.state])` | covered |
| `handlers/main.yml`: `service: state: restarted` | `changed_hosts(yield checkpoint("configure done"), NOTIFYING)` → `service_handler(notified, ("node_exporter",), action="restarted")` | barrier + recipe |
| `uri: url=http://<host or localhost>:<port>/ return_content: true` + `failed_when: "'Metrics' not in …"` | `yield fact(server.HttpEndpoint, url=metrics_url(hv))` + the body's own `ValueError` | deviation (D8, D9) |
| `meta/main.yml`'s nine platforms (Ubuntu, Debian, Fedora, SLES, openSUSE, FreeBSD, …) | the `"Ubuntu"` family guard's early return | deviation (D1) |
| `molecule/default/converge.yml` | `tests/roles/test_node_exporter.py`, offline | scoped out |

The migration surfaced **no new library gaps**: `files.unarchive`'s URL route
is the `unarchive: remote_src: true` equivalent, `act(Shell)` carries the
`register` + `when` probe that used to need a second play, `control` carries the
`delegate_to: localhost` fetch, and the `checkpoint` is the `notify`.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

No `reemote[templates]` — the unit template is pure substitution, so
`render_unit` is an f-string (D12).

The tarball is fetched **control-side** by `files.unarchive`, and the `latest`
lookup is a control-side `files.fetch_url`, so the controller needs outbound
HTTPS to `api.github.com` and `github.com` (or to whatever `download_url`
names); the host downloads nothing (D5). SSH access to each Ubuntu host with
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`.

Two host prerequisites the role assumed and does not check. The parent of
`bin_path` (default `/usr/local/bin`) must exist — the migration issues no
`file: state=directory` task because the upstream role issues none either, and
it is on every Ubuntu image. And the verification runs `curl` **on the target**
against `localhost` when `host` is empty, so the metrics port (default `9100`)
must be reachable from the host itself.

## 3. Variables

`NodeExporterVars` is a frozen pydantic model whose fields carry bare names;
the role name passed to `resolve_vars` binds the entry's `node_exporter` role
mapping.

--->

::: reemote.roles.node_exporter
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - node_exporter

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `version` | `str` | `latest` — resolved through the releases API (D2/D3) | `node_exporter_version` |
| `arch` | `str` | `amd64` — GitHub's spelling (`arm64`, `armhf`, …) | `node_exporter_arch` |
| `download_url` | `str \| None` | `None` — derived from `version` + `arch`; set it to mirror the archive | `node_exporter_download_url` |
| `bin_path` | `str` | `/usr/local/bin/node_exporter` | `node_exporter_bin_path` |
| `host` | `str` | `""` — every interface; the check substitutes `localhost` | `node_exporter_host` |
| `port` | `int` | `9100` | `node_exporter_port` |
| `options` | `str` | `""` — extra `ExecStart` flags | `node_exporter_options` |
| `state` | `str` | `started` — one of `started`, `stopped`, `restarted`, `reloaded` | `node_exporter_state` |
| `enabled` | `bool` | `true` | `node_exporter_enabled` |
| `restart` | `str` | `on-failure` — the unit's `Restart=` policy | `node_exporter_restart` |
| `uid` | `int \| None` | `None` — let `useradd` choose | `node_exporter_uid` |
| `gid` | `int \| None` | `None` — create no group at all (D11) | `node_exporter_gid` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. Validation
is fail-fast, before any host is contacted: `version` must be a non-empty plain
release name (no `/`, no `..`) and `arch` a plain architecture name — both are
interpolated into a URL and two paths; `download_url` must be an `http(s)` URL
or `None`; `bin_path` absolute; `host` a bare address (no scheme, no port — the
port is `port`); `port` between 1 and 65535; `state` and `restart` `Literal`
enums over the values the README documents; `uid`/`gid` positive integers or
`None`; `extra="forbid"` plus `resolve_vars`' binding report a key that names no
field. Outcomes that used to fail mid-play now fail at resolution or at the
body's first wave: a releases API that answers with no usable `tag_name` raises
`ValueError` (the role's `regex_replace` would have produced an empty version
and a 404 download), and an unreachable one raises `OSError` rather than
retrying five times (D2).

## 5. Run it

The role is library code; `examples/roles/node_exporter.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables, builds the deploy from the `node_exporter`
factory and drives it with `run_waves` — a `dry=True` preview, then the apply,
then the converged rerun — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import NodeExporterVars, node_exporter

NODE_EXPORTER_OVERRIDES = {"version": "1.8.2"}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(NodeExporterVars, "node_exporter", inventory,
                            overrides=NODE_EXPORTER_OVERRIDES)
    deploy = node_exporter(vars_set)                                    # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)        # parks at the probe
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                  # the real apply
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                  # convergence
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/node_exporter.py
```

The `dry=True` pass is empty by design: the body's first item is the
`--version` probe, and a dry run cannot answer it, so it parks there
(`barrier: Check current node_exporter version. → unknown until run`) — like
`ansible --check` on a role whose second task is `when: registered.stdout`.
The converged second apply is the honest preview.

**Run the program twice.** The second pass must report no change: the probe
answers the pinned version, so both download requests are skipped entirely
(nothing is fetched), the account reads back as requested, the unit is
byte-identical, the service runs and is enabled, no notification fires, and the
metrics check passes on the running exporter. That is the migration's
idempotence claim, and the offline suite asserts it — including that a
converged `latest` host still asks the API who is latest (D3).

## 6. What it does

One deploy. Every barrier type appears in it, and the wave count is
host-dependent:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `act(Shell, "{bin_path} --version")` | the role's `register` + `when` probe. Its `Result` is data: exit 127 from a missing binary is the empty-`stdout` reading the role tested, not an error |
| 0′ | *(only when `version == "latest")* `control(files.fetch_url, RELEASE_API_URL)` | a wave of its own — the `delegate_to: localhost` lookup has to finish before the download URL can be built |
| 1 | `install`: `files.unarchive` + `files.copy` (only when the probe missed), then `configure`: `server.group` (only with `gid`), `server.user`, `files.install` (the unit), `systemd.service(daemon_reload=True, **STATE_KEYWORDS[state])`, then `checkpoint("configure done")` | the task list in task order; the checkpoint closes the wave that holds the two notifying requests and reads its report |
| 2 | `service_handler(notified, ("node_exporter",), action="restarted")` — a request only on notified hosts — then `fact(server.HttpEndpoint, url=metrics_url(hv))` | the `restart node_exporter` handler, then the `uri` verification, which follows the restart (D9) so it reads the service this deploy brought up |

Five rules are load-bearing:

* **The probe, not an operation, carries the download half's idempotence.**
  `files.copy` compares content only when the destination already exists, and
  a freshly extracted archive member has no read-back to compare against, so
  the pair is gated by `if version not in probe["stdout"]` in the body —
  exactly the role's own `when`. On a converged host the two requests are
  absent from the stream, not merely unchanged.
* **`files.unarchive`'s `creates=` gate is a second net.** It names the
  directory the tarball unpacks to, so a host that has the extraction but not
  the moved binary re-extracts nothing.
* **`checkpoint` immediately follows the notifying requests** (rule 4):
  nothing may be yielded between the service request and it, or the report
  would describe an empty wave and silently notify nobody. `NOTIFYING` is
  `(MOVE_NAME, UNIT_NAME)` — the only two tasks upstream carried `notify`.
* **The handler wave always exists, the handler request often does not.** The
  metrics `fact` closes wave 2 on every pass, so the wave count stays 3 even
  when nothing was notified; on a converged host wave 2 holds only the barrier
  line.
* **`state: stopped` takes two passes, exactly as Ansible does.** The
  configure request never starts the unit, but the new binary and unit queue a
  restart, and the handler *does* start it — the same oscillation an
  end-of-play handler flush produces after a `state: stopped` task. The next
  pass, with nobody notified, stops it for good. The offline test pins all
  three passes.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all — the
`meta/main.yml` platform list is a guard, not a dispatch (D1).

## 7. Transcript

Captured from `tests/roles/test_node_exporter.py`'s harness, against the
simulated Ubuntu host (`version` pinned to `1.8.2`, so no releases lookup) plus
a RedHat host the role never touches. The fetches are stubbed at the
control-side seam and the metrics endpoint is seeded — the simulation never
starts a daemon; only the landing-page body in the two `Verify …` barrier lines
is elided to `…`. Fresh host, first pass:

```text
(root)
  wave 0
    barrier: Check current node_exporter version. → {'exit_status': 127, 'stdout': '', 'stderr': 'command not found'}
  wave 1
    ubuntu: Download and unarchive node_exporter into temporary location. [changed: will extract https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz into /tmp]
        $ scp <files unarchive> -> /tmp/reemote-unarchive-b1878352aec43ce6-node_exporter-1.8.2.linux-amd64.tar.gz
        $ sudo tar -xz -f /tmp/reemote-unarchive-b1878352aec43ce6-node_exporter-1.8.2.linux-amd64.tar.gz -C /tmp
        $ sudo rm -rf /tmp/reemote-unarchive-b1878352aec43ce6-node_exporter-1.8.2.linux-amd64.tar.gz
    ubuntu: Move node_exporter binary into place. [changed: will copy /tmp/node_exporter-1.8.2.linux-amd64/node_exporter to /usr/local/bin/node_exporter]
        $ sudo cp -r -f /tmp/node_exporter-1.8.2.linux-amd64/node_exporter /usr/local/bin/node_exporter
        $ sudo chmod 755 /usr/local/bin/node_exporter
    ubuntu: Create node_exporter user (optional fixed UID) [changed: user node_exporter absent, will create]
        $ sudo useradd -d /home/node_exporter -s /sbin/nologin -M node_exporter
    ubuntu: Copy the node_exporter systemd unit file. [changed: content /etc/systemd/system/node_exporter.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-5e065cab18941b1a/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-5e065cab18941b1a/content /etc/systemd/system/node_exporter.service && chown root:root /etc/systemd/system/node_exporter.service && chmod 644 /etc/systemd/system/node_exporter.service && rm -rf /tmp/reemote-install-5e065cab18941b1a'
    ubuntu: Ensure node_exporter is running and enabled at boot. [changed: daemon-reload; node_exporter.service not running (None), will start; node_exporter.service not enabled, will enable]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start node_exporter.service
        $ sudo systemctl enable node_exporter.service
    barrier: configure done → 1 host notified
  wave 2
    ubuntu: Restart node_exporter (handler) [changed: node_exporter.service already running; node_exporter.service will restart]
        $ sudo systemctl restart node_exporter.service
    barrier: Verify node_exporter is responding to requests. → {'status': 200, 'body': '<html>…<a href="/metrics">Metrics</a>…</html>'}
```

The rerun — the probe now answers the installed version, so the download pair
is not in the stream at all, the checkpoint notifies nobody, and wave 2 is only
the metrics read:

```text
(root)
  wave 0
    barrier: Check current node_exporter version. → {'exit_status': 0, 'stdout': 'node_exporter, version 1.8.2 (branch: HEAD)\n', 'stderr': ''}
  wave 1
    ubuntu: Create node_exporter user (optional fixed UID) [no change: user node_exporter already as requested]
    ubuntu: Copy the node_exporter systemd unit file. [no change: content already up to date (/etc/systemd/system/node_exporter.service)]
    ubuntu: Ensure node_exporter is running and enabled at boot. [no change: node_exporter.service already running; node_exporter.service already enabled]
    barrier: configure done → 0 hosts notified
  wave 2
    barrier: Verify node_exporter is responding to requests. → {'status': 200, 'body': '<html>…<a href="/metrics">Metrics</a>…</html>'}
```

The dry pass parks at the probe — one wave, no requests planned, nothing
fetched:

```text
(root)
  wave 0
    barrier: Check current node_exporter version. → unknown until run
```

`latest` costs one extra wave: the control fetch closes wave 1 and the task
list follows in wave 2 (the rest of that pass matches the first transcript,
`/tmp/reemote-install-…` aside):

```text
(root)
  wave 0
    barrier: Check current node_exporter version. → {'exit_status': 127, 'stdout': '', 'stderr': 'command not found'}
  wave 1
    barrier: fetch_url → {"tag_name": "v1.8.2"}
  wave 2
    ubuntu: Download and unarchive node_exporter into temporary location. [changed: will extract https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz into /tmp]
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The `meta/main.yml` platform list (Ubuntu, Debian, Fedora, openSUSE, SLES, FreeBSD, Generic\*) freezes to the `"Ubuntu"` family guard; there is no OS dispatch to replace | reemote drives Linux hosts only, and the role ships no `vars/<OS>.yml` — `defaults/main.yml` is its only variable file, so the defaults *are* the frozen constants |
| D2 | `run_once: true` + `delegate_to: localhost` becomes a per-host `control` barrier, and the `until: status == 200` / `retries: 5` loop is dropped | the barrier model has no play-wide run_once; an unreachable API raises `OSError` out of the deploy instead of retrying five times |
| D3 | The `include_tasks: config-version.yaml` + `set_fact` pair disappears — the resolved version and URL are local names in the body | no inventory host variable is ever rewritten, so nothing needs a second pass to see the first pass's fact; a converged `latest` host re-asks the API, because a version is an input, not remembered state |
| D4 | The `unarchive` task's `mode: 0755` is **not** carried over; the executable bit is set by the `files.copy` instead | `files.unarchive`'s `mode` is a recursive `chmod -R` of `dest`, and the role's `dest` is the shared world-writable `/tmp` — applying it there would re-mode every other user's files |
| D5 | `remote_src: true` with a URL `src` (a target-side download) becomes a **control-side** fetch spooled to a URL-derived `/tmp` path | reemote's URL operations fetch on the controller (the `files.download`/kubectl stance), so the controller needs egress and the host downloads nothing; `download_url` is the mirror knob |
| D6 | The `copy` task becomes `files.copy` in its rename form with `overwrite=True` (Ansible's `force: yes`, which `copy` defaults to) and an explicit `mode="0755"` | a drifted binary converges once and a repeat reports no change on the sha1 comparison |
| D7 | `systemd: daemon_reload: true` `when: unit file is changed` and the `service:` task fold into **one** `systemd.service` request carrying `daemon_reload=True` | the reload is state-gated on `SystemdNeedDaemonReload`, so a converged host issues no `daemon-reload` — exactly what the role's `when` intended |
| D8 | The `uri` verification becomes `fact(server.HttpEndpoint)`, which runs `curl` **on the target**, and the body raises its own `ValueError` | `localhost` keeps the role's meaning (the host's own loopback); a fact never raises on a non-2xx answer, so the record is read and checked for status 200 plus the `Metrics` link |
| D9 | The handler runs *before* the metrics check | Ansible flushes handlers at the end of the play, so its `uri` task probed whatever was running *before* the restart; here the check reads the service this deploy just brought up |
| D10 | `create_home: false` maps to `ensure_home=False`, and `home` is left unset | reemote's `create_home` already defaults to `False`, so no `useradd -m` is emitted; the recorded `-d /home/node_exporter` is a default, not a created directory |
| D11 | `default(omit)` becomes `None` | with no `gid` there is no group request and no `group=` on the user (so `useradd` makes its own group of the same name, as upstream), and with no `uid` no `--uid` flag is rendered |
| D12 | The unit template renders as an f-string, and an empty `node_exporter_options` leaves **no** trailing space on `ExecStart=` | the template is pure substitution, so no Jinja2 and no `reemote[templates]` extra; systemd ignores the trailing space either way |

## 9. Scoped out

* **Non-Ubuntu targets.** See D1 — the role advertises Debian, Fedora, SLES,
  openSUSE and FreeBSD; the migration fixes Ubuntu, so an off-target host is
  skipped silently rather than mapped. FreeBSD has no systemd unit to install
  in the first place, which is why it is a scope decision rather than a gap.
* **The releases-API retry loop** (`until`/`retries: 5`, D2). A program that
  wants it wraps the deploy in its own retry; the role body fails loudly.
* **Remembering the resolved version.** A converged `latest` host queries the
  API again on every run (D3). Shipping a `node_exporter` state fact to avoid
  that call would make the role's own upgrade path ambiguous — the upstream
  role re-probed the binary every run too, and the probe, not the API, is what
  decides whether to download.
* **`remote_src: false`** (shipping the tarball in the role's `files/`). The
  upstream task only ever uses a URL, and `download_url` covers the mirrored
  case (D5).
* **The collector inventory.** `node_exporter_options` is the upstream role's
  own answer to enabling and disabling collectors; no per-collector typed
  fields are invented here.
* **The `molecule/` suite.** Replaced by the offline test, which pins both
  trees, the notification re-fire, the metrics gate and the vars validation.

--->
