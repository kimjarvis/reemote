# users — account, group, SSH key and profile management role

| | |
| --- | --- |
| Role | [`singleplatform-eng/ansible-users`](https://github.com/singleplatform-eng/ansible-users) |
| Module | [`reemote.roles.users`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/users.py) |
| Example | [`examples/roles/users.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/users.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `users` role mapping → `UsersVars` |

<!---
| Waves | 1 (`2` only under the `on_create` password gate) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | **none** — the profile block is plain text, every file is written by an operation |
| Offline test | [`tests/roles/test_users.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_users.py) |

`users` manages accounts the way the role does: the groups in
`groups_to_create`, a per-user primary group for every user that names none,
then each user with shell, comment, uid, home, secondary-group membership and
optional password hash, generated SSH key pairs, authorized keys, a
`blockinfile` profile block in `~/.profile`, and last the deletions —
`userdel`, optionally `-r`/`-f`, plus the per-user group.

This is the corpus's **pure request wave**: the whole seven-task list needs no
read of the machine at all, because every Ansible `when` here — the per-user
group gate, the profile gate, the skip-missing key loop — is a function of the
variables, not of remote state. The body yields only `op()` requests, so even a
`dry=True` pass plans the entire wave (like `git` and `nodejs`), and the one
barrier that exists, a `fact(Users)` snapshot, appears *only* when a user
combines `update_password: on_create` with a password (deviation D4).

```python
yield from create_groups(plan)(host)      # Creating groups, Per-user group
yield from create_users(plan, existing)(host)  # User creation (+ Generate SSH key)
yield from authorize_keys(plan)(host)     # SSH keys
yield from write_profiles(plan)(host)     # Setup user profiles + Set profile ownership
yield from remove_deleted(plan)(host)     # Deleted user/group removal
```

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **recipe** (a documented
shape change), **deviation** (a deliberate difference).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `group: name={{ item.name }} gid={{ item.gid }}` over `groups_to_create` | `op(server.group, name="Creating groups (X)", gid=…)` | covered (gid reconcile, issue #62) |
| `group:` per-user, `when: users_create_per_user_group and item.group is not defined`, `gid: {{ item.gid \| default(item.uid) \| default(omit) }}` | `op(server.group, name="Per-user group creation (X)", …)`, gate applied in the plan | covered |
| `user:` creation, the big templated task (`shell`/`comment`/`groups`/`append`/`uid`/`home`/`createhome`/`password`/`update_password`/`generate_ssh_key`) over `users` | one `op(server.user, name="User creation (X)", …)` per account, every Jinja default frozen in the plan | covered (empty-value reconcile, issue #59) |
| the same task's `generate_ssh_key: yes` side effect | a separate `op(server.ssh_keypair, name="Generate SSH key for X", …)` request right after the account | recipe (D3) |
| `authorized_key: user={{ item.0.username }} key={{ item.1 }}` with `with_subelements: [users, ssh_key] skip_missing`, `path: {{ home }}/{{ authorized_keys_file }}` | one `op(server.user_authorized_keys, name="SSH keys (X)", public_keys=…, …)` per user, explicit directory/filename | recipe (D6) |
| `blockinfile: path: ~/.profile marker: "# {mark} ANSIBLE MANAGED BLOCK"`, `when: users_create_homedirs and item.profile is defined`, `create: true` | `op(files.transform, name="Setup user profiles (X)", …)` with a role-local block fixpoint, plus `op(files.file, name="Set profile ownership (X)", …)` | recipe (D5) |
| `user: name={{ item.username }} state=absent remove={{ item.remove \| default(omit) }} force={{ item.force \| default(omit) }}` over `users_deleted` | `op(server.user, name="Deleted user removal (X)", present=False, remove_home=…, force=…)` | covered (`userdel -r/-f`, issue #61) |
| `group: name={{ item.username }} state=absent` over `users_deleted`, `when: users_create_per_user_group` | `op(server.group, name="Deleted per-user group removal (X)", present=False)` | covered |

No library gap blocked the migration itself. The audit of this
role is exactly what grew the four `server`-namespace additions the mapping
above cites — issues #59–#62, all merged — and the two remaining constructs
(`blockinfile`, the key-file path split) already have documented recipes:
`files.transform` with a pure block fixpoint, and
`server.user_authorized_keys`' explicit directory/filename pair.

The first *live* two-pass run of `examples/roles/users.py` against the demo
Ubuntu host did, however, expose a probe gap. `useradd -m` creates the home as
`0750` (Ubuntu's `login.defs` `HOME_MODE`) and the login user is not in that
group, so every unprivileged idempotence read under it sees a present file as
*absent*: the key pair is re-planned and the fresh `ssh-keygen` collides with
the private key that is really there, exits 1 and fails the run, while the
profile block is rewritten every pass. Two issues cover that one blindness:
[issue #71](https://github.com/kimjarvis/reemote/issues/71) for the `server`
reads, now fixed, and
[issue #70](https://github.com/kimjarvis/reemote/issues/70) for the `files`
half — the profile block's `FileContents`/`File` reads — now closed: an
unprivileged read that answers *absent* is checked against the search bit of
the nearest existing ancestor directory, and a directory the login cannot
traverse turns the answer into an elevated re-probe. `SimulatedHost` models the
denial (`login_path_visible`), so the offline suite pins the gate instead of
happening to skip it.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

The role renders nothing: the profile block is plain text between the markers
and every other file is written by an operation, so no `reemote[templates]`
extra is needed.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because `groupadd`/`useradd`/`usermod`/`userdel` and any
write into another user's home are privileged. The transfers stay
unprivileged — the spool-and-install family elevates only the final `cp`.

> **Deletions are destructive.** A `deleted` entry with `remove: true` runs
> `userdel -r`: the home directory and mail spool go with the account, and
> `force: true` removes it even while logged in. Pin the `users` and `deleted`
> lists from inventory data you control, and use the `dry=True` preview first —
> with no barrier on the default path it plans every request, deletions
> included.

## 3. Variables

`UsersVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry
bare names; the role name passed to `resolve_vars` binds the entry's `users`
role mapping, so an inventory written for the role needs no key gymnastics.

--->

::: reemote.roles.users
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - users

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `create_per_user_group` | `bool` | `true` — a user with no `group` gets a group of its own name | `users_create_per_user_group` |
| `group` | `str` | `users` — the primary group when per-user groups are off | `users_group` |
| `default_shell` | `str` | `/bin/bash` | `users_default_shell` |
| `create_homedirs` | `bool` | `true` — `createhome`, and the gate on the whole profile task | `users_create_homedirs` |
| `users` | `tuple[UserSpec, …]` | `()` | `users` |
| `deleted` | `tuple[DeletedUserSpec, …]` | `()` — removed *after* every creation | `users_deleted` |
| `groups_to_create` | `tuple[GroupSpec, …]` | `()` — ensured *before* every user | `groups_to_create` |
| `authorized_keys_file` | `str` | `.ssh/authorized_keys` — relative to the home; **no prefix upstream** | `authorized_keys_file` |

Each `users` entry is a `UserSpec` — the role's per-item keys, with the
Jinja `default(...)` distinction kept (`None` means the entry omitted the key):

| Key | Type | Default → task behaviour | Upstream item key |
| --- | --- | --- | --- |
| `username` | `str` | required, non-empty | `item.username` |
| `name` | `str \| None` | `None` → `default('')`: an *explicit* empty comment, which clears one | `item.name` |
| `group` | `str \| None` | `None` → per-user group (or `group` when off) | `item.group` |
| `groups` | `tuple[str, …] \| None` | `None` → the empty string that strips every secondary group (D2) | `item.groups` |
| `append` | `bool` | `false` — replace, not add to, the secondary membership | `item.append` |
| `shell` | `str \| None` | `None` → `default_shell` | `item.shell` |
| `password` | `str \| None` | the *encrypted* hash; `None` → no password argument (D1) | `item.password` |
| `uid` | `int \| None` | `None` → system-assigned | `item.uid` |
| `gid` | `int \| None` | only read by the per-user *group* task (`item.gid \| default(item.uid)`) | `item.gid` |
| `home` | `str \| None` | `None` → `/home/<username>` | `item.home` |
| `generate_ssh_key` | `bool` | `false` — a pair is generated after the account exists (D3) | `item.generate_ssh_key` |
| `update_password` | `Literal["always", "on_create"]` | `always` — `on_create` gates the hash on the pre-run `fact(Users)` (D4) | `item.update_password` |
| `ssh_key` | `tuple[str, …]` | `()` — the `skip_missing` sub-element list | `item.ssh_key` |
| `profile` | `str \| None` | `None` → no blockinfile task for this user | `item.profile` |

`groups_to_create` entries are `GroupSpec`s (`name` required and non-empty,
optional pinned `gid` — reconciled on an existing group, issue #62); `deleted`
entries are `DeletedUserSpec`s (`username`, plus `remove: bool` for `userdel -r`
and `force: bool` for `-f`, both default `false`, matching the module).

<!---

Precedence is `model defaults < inventory role mapping < overrides`. Sequences
are `tuple`, never `list`: the model is frozen and hashable, and every value
that reaches `op()` must hash. Blank names are rejected — an empty `username`,
`group` or `groups_to_create.name` raises `ValidationError` (a `ValueError`)
while the inventory is resolved, before any host is contacted (upstream, a
blank username reaches `useradd` and fails there).

## 5. Run it

The role is library code; `examples/roles/users.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json`, resolves the variables (the entry's `users` role
mapping, overlaid with the call-site `USERS_OVERRIDES`), builds the deploy from
the `users` factory and drives it with `run_waves` — a `dry=True` preview, then
the apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import UsersVars, users

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(UsersVars, "users", inventory, overrides=USERS_OVERRIDES)
    deploy = users(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # plans the whole wave
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)            # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/users.py
```

The overrides manage a clearly namespaced demo account (`reemote-demo` — a
pinned `developers` group, an appended membership, a profile block, a
generated key pair and an authorized key) and delete a likewise namespaced
`reemote-gone`; nothing you log in as is touched.

**Run the program twice.** The second run must report no change anywhere: the
groups exist with their gids, the account carries its shell, comment, home and
membership, the key pair and authorized key are in place, the profile block is
byte-identical between its markers, and the deleted account is already gone.
That is the migration's idempotence claim, and the offline suite asserts it.
On a *live* Ubuntu host the second pass used to fail on the `server` half: the
locked `0750` home hid the key pair from the unprivileged probe, so
`ssh_keypair` re-planned a generate that collided with the existing key and
exited 1 — fixed by [issue #71](https://github.com/kimjarvis/reemote/issues/71),
whose elevated re-probe now sees a pair hidden under a locked home.
The `files` half of the same churn, the profile block's rewrite, is closed by
[issue #70](https://github.com/kimjarvis/reemote/issues/70): an absent answer
from under an untraversable directory now earns an elevated re-probe, so the
block reads as already in place. Live convergence for the key pair landed with
the #71 fix, not with a change to the role.

## 6. What it does

One deploy, one wave. Every request of the default configuration rides the
same wave, in the role's task order — and because there is no barrier, the
driver's apply-before-pull ordering *is* the ordering guarantee the role's
sequential tasks had: `developers` exists before `useradd -G` references it,
the account exists before its `.ssh` directory is created, and creations
finish before `userdel` runs.

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `Creating groups`, `Per-user group creation`, `User creation` (each followed by its `Generate SSH key`), `SSH keys`, `Setup user profiles` + `Set profile ownership`, the two deletion tasks | every gate is vars-only, so the plan is frozen before the first request and nothing needs a read |
| (0 then 1) | only when some user sets `update_password: on_create` *with* a password: wave 0 is just `fact(Users)`, wave 1 the requests above | the snapshot decides who "already existed" (D4), so it is the role's single barrier |

Three rules are load-bearing:

* **The plan is decided before the first request.** `users_plan` resolves every
  Jinja default, `when` clause and `default(omit)` fallback from the vars alone,
  so a bad value raises with nothing applied and an empty section emits no
  request at all.
* **Same-wave ordering is apply ordering.** Ten requests against two homes, one
  wave, no barrier between them: `run_waves` applies each request and refreshes
  the fact cache before pulling the next, so `authorized_keys` sees the `.ssh`
  directory the previous request created.
* **Deletions run last, like the play.** A name in both `users` and `deleted`
  ends up deleted — the same order the role's task list encodes.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_users.py::test_tree_snapshot`, against the simulated
Ubuntu host plus a RedHat host the role never touches. Fresh host, first pass
(abridged to one line per request; the offline test pins the whole tree,
commands included):

```text
(root)
  wave 0
    ubuntu: Creating groups (developers) [changed: group developers absent, will create]
    ubuntu: Creating groups (staff)  [no change: group staff already present]
    ubuntu: Per-user group creation (alice) [changed: group alice absent, will create]
    ubuntu: User creation (alice)    [changed: user alice absent, will create; home /home/alice missing, will create]
        $ sudo useradd -d /home/alice -s /bin/zsh -g alice -G sudo --uid 1500 -c 'Alice Full' -m alice
    ubuntu: User creation (bob)      [changed: user bob absent, will create; home /home/bob missing, will create]
    ubuntu: Generate SSH key for bob [changed: /home/bob/.ssh missing, will create; /home/bob/.ssh/id_rsa.pub missing, will generate]
        $ sudo sh -c 'su -s /bin/sh bob -c … ssh-keygen -q -t rsa -b 4096 -f /home/bob/.ssh/id_rsa …'
    ubuntu: SSH keys (alice)         [changed: /home/alice/.ssh missing, will create; …; 1 key(s) missing, will append]
    ubuntu: Setup user profiles (alice) [changed: content /home/alice/.profile is absent, will install]
    ubuntu: Set profile ownership (alice) [no change: file /home/alice/.profile already exists]
    ubuntu: Deleted user removal (carol) [changed: user carol present, will remove]
        $ sudo userdel -rf carol
    ubuntu: Deleted per-user group removal (carol) [changed: group carol present, will remove]
```

The rerun is the same single wave with every line a no-change — and the
RedHat host appears in no tree, no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `password: None` sends **no password argument**, not `'!'` | the role's `password: item.password \| default('!')` re-writes the lock marker every run; `useradd` locks a passwordless account by itself, and re-asserting `'!'` cannot converge because the `Users` fact's shadow read is unprivileged. A set hash is passed through and converges when the login user can read it |
| D2 | an omitted `groups` key means the **explicit empty tuple** | Ansible joins the omitted list to `''`, which *removes* every secondary group; `None` in reemote means "leave alone", so the plan passes `()` — issue #59 gives it exactly `usermod -G ''` semantics |
| D3 | `generate_ssh_key` is **its own `server.ssh_keypair` request** after the account | reemote's `server.user` is the `useradd`/`usermod` operation, not Ansible's do-everything module; the key pair rides the dedicated operation (issue #60) with the library's defaults (rsa/4096 where Ansible defaults to 2048), at the *configured* home's `.ssh/id_rsa` — which also lets a dry preview plan key generation for a brand-new account |
| D4 | `update_password: on_create` reads a **`fact(Users)` snapshot** first | the only barrier in the role, and conditional: it appears only when the configuration combines `on_create` with a password. A user present at the snapshot loses the password argument for this pass |
| D5 | `blockinfile` becomes a role-local **block transform on `files.transform`** plus a `files.file` pair | reemote ships no `lineinfile`/`blockinfile` by design; the markers read `# BEGIN/END REEMOTE BLOCK` rather than `ANSIBLE MANAGED BLOCK`, `create: true` is free (an absent file transforms from `None`), and because transform idempotence is *content*-only, the task's `owner`/`mode` need the metadata companion request |
| D6 | the per-key `authorized_key` loop becomes **one request per user** | `server.user_authorized_keys` appends a whole tuple (`delete_keys=False` — the task never removed keys); the path is split into the operation's directory/filename from the *configured* home, as the task's path template did |
| D7 | `ensure_home` is tied to **`create_homedirs`** | with `createhome: no` the role's task touches no home directory at all, and reemote's default would also converge an existing home's ownership |
| D8 | the family guard is the body's opening `if`, and the role's implicit gates are **pydantic validation** | a blank username or group name raises while the variables resolve, before any host is contacted; upstream fails later, inside `useradd` |

## 9. Scoped out

* **Non-Ubuntu families.** The upstream role runs anywhere `useradd`/`usermod`
  exist, but this migration targets Ubuntu: a host in any other family is
  skipped, not mapped to `pw`/`user`/`group` module variants.
* **Key removal.** The role's `authorized_key` tasks never pass
  `state: absent`, and neither does this migration: `delete_keys` stays off, and
  a generated pair is never regenerated over an existing one (`force` off).
* **`ssh_keypair` tuning.** Key type, size and comment are the operation's
  defaults (rsa/4096); upstream's `ssh_key_type`/`ssh_key_bits` user keys are
  not read — a site that needs them calls `server.ssh_keypair` directly.
* **Password *removal*.** Setting `password: null` on a previously passworded
  account removes the argument, not the hash — the D1 mapping. Clearing a hash
  needs an explicit `usermod -p ''`, which is outside the role's surface.
* **`update_password: on_create` mid-run presence.** Ansible compares against
  the state as the task itself sees it; here the snapshot is taken before the
  wave, so a user created earlier in the *same run* still receives its
  password. Unreachable and harmless — the creation request set it anyway.

--->
