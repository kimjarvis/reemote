# htpasswd — Apache password-file role

| | |
| --- | --- |
| Role | [`geerlingguy.htpasswd`](https://github.com/geerlingguy/ansible-role-htpasswd) |
| Module | [`reemote.roles.htpasswd`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/htpasswd.py) |
| Runner | [`examples/roles/htpasswd.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/htpasswd.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `htpasswd` role mapping → `HtpasswdVars` |

<!---
| Waves | `1 + P` (`P` credential paths); 1 install-only |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed) |
| Offline test | [`tests/roles/test_htpasswd.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_htpasswd.py) |

`htpasswd` installs the Apache utilities and manages `name:hash` entries in
Apache password files: each credential names a file, a user, a password and
the ownership the file must carry. An existing entry whose stored hash already
verifies against the desired password keeps its line byte-for-byte — no new
salt, no churn — a new or changed password is rewritten with a freshly hashed
one, and lines the role does not manage are preserved.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("htpasswd", htpasswd(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list — the
`include_vars` dispatch, the package install and the `htpasswd` loop — is that
single deploy, and its `defaults/main.yml` is `HtpasswdVars`.

## 1. What it is, and where it came from

The role's two task shapes map onto reemote like this:

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars: {{ ansible_facts.os_family }}.yml` | the Ubuntu constants at the top of the module + the family guard | recipe |
| `set_fact: htpasswd_required_packages` fallback | `HtpasswdVars.required_packages` default | covered |
| `package: name={{ htpasswd_required_packages }}` | `op(apt.packages, packages=…, sudo=True)` | covered |
| `htpasswd: path/name/password` (the module's read + verify) | `yield fact(FileContents, path=…)` + control-side verify/hash through `files.htpasswd_entry` | covered ([#74](https://github.com/kimjarvis/reemote/issues/74) shipped) |
| `htpasswd: …` (the module's write + owner/group/mode) | `op(files.install, content=…, dest=…, mode/user/group, sudo=True)` + `files.file` companion | covered |

The middle row was the migration's one genuine gap: reemote shipped no
entry-management operation for htpasswd files, so the role had to compose the
behavior from the ordinary spool-and-install family — read the file with a
barrier, merge lines against the desired passwords, install the whole file
back. Issue [#74](https://github.com/kimjarvis/reemote/issues/74) has since
shipped as the first-class `files.htpasswd` operation, and the role now runs on
top of it: the merge is `files.htpasswd_entry` folded over the credentials for
one path.

The role still batches a path's credentials into one read/write pair instead of
one `files.htpasswd` request each (D6) — same-wave requests share one
`FactCache`, so two entry operations for one file would each read the pre-write
text and the second would clobber the first.

The hashing lives in the operation, not the role: pure standard-library Python
for `$apr1$` (Apache's MD5-crypt), `$1$`, `{SHA}`, `$2y$` bcrypt and traditional
DES, each verified against a reference implementation — `openssl passwd`, the
system's `crypt()` and the OpenBSD bcrypt digest (the pinning lives in
`tests/test_files_htpasswd.py`).

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

Nothing else. No `reemote[templates]`, no passlib, no bcrypt — the hashing is
standard-library Python run on the controller, so the target host needs no
Python at all, and no password ever crosses to the host as a command
argument.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The htpasswd
files are typically `0640 root:www-data`, which the spool-and-install read
back handles with its two-tier elevated re-read.

## 3. Variables

`HtpasswdVars` is a frozen pydantic model whose fields carry bare names; the
role name passed to `resolve_vars` binds the entry's `htpasswd` role mapping.

--->

::: reemote.roles.htpasswd
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - htpasswd

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `credentials` | `tuple[HtpasswdCredential, …]` | `()` | `htpasswd_credentials` |
| `required_packages` | `tuple[str, …]` | `("apache2-utils",)` | `htpasswd_required_packages` |
| `nolog` | `bool` | `true` | `htpasswd_nolog` |

<!---

Each `credentials` item is itself a frozen model: `path` (absolute), `name`
(no colon — `name:hash` is the file format), `password`, `owner`, `group`,
`mode` (octal digits, default `"0640"`) and `hash_scheme` (default
`apr_md5_crypt`, any scheme `files.htpasswd` accepts — including the upstream
`sha1` and `crypt` spellings). Validation is fail-fast: a relative path, a colon
in a name, a symbolic mode like the upstream default `u+rw,g+r` (say `"0640"`
instead) or an unsupported scheme raises in `resolve_vars`, before any host is
contacted.

`nolog` is inert (D5): reemote's plan lines render the install spool, never
its content, so there is nothing to redact per-request. Precedence is
`model defaults < inventory role mapping < overrides`.

## 5. Run it

The role is library code; `examples/roles/htpasswd.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the demo credentials arrive as call-site
`overrides`, above the entry's `htpasswd` role mapping), builds the deploy
from the `htpasswd` factory and drives it with `run_waves` — a `dry=True`
preview first, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import HtpasswdVars, htpasswd

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        HtpasswdVars, "htpasswd", inventory,
        overrides={"credentials": DEMO_CREDENTIALS},
    )
    deploy = htpasswd(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/htpasswd.py
```

The `dry=True` pass plans the package request and parks at the first
`FileContents` barrier (`barrier: FileContents → unknown until run`): no file
is read, nothing is applied. **Run the program twice.** The second apply must
report no change anywhere — and this is the interesting convergence in the
corpus, because every fresh hash draws a random salt: the read-back merge
verifies each stored hash against the desired password and reproduces matching
lines byte-for-byte, so the recomputed file equals the installed one.

`main()` maps the role's failure modes (bad inventory, `ValueError` from a
rejected credential, `CommandError`, `HostConnectionError`, `PrivilegeError`,
`OSError`, `TypeError`) to a one-line stderr message and exit 1.

## 6. What it does

One deploy; `1 + P` waves for `P` distinct credential paths — the package
request plus the first path's barrier close wave 0, and each path's write
pair follows its own read barrier:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `apt.packages` (unless `required_packages` is empty), then `fact(FileContents, path=…)` per the first path | the role's `package:` task, then the module's read of the file |
| 1..P | `files.install(content, dest, mode/user/group)` + `files.file(mode/user/group)` | the merged file in one content-idempotent write; the companion converges an owner/mode drift on an unchanged file |

There is **no checkpoint**: nothing in the role reads an outcome, and there
are no handlers. A host whose `host.family` is not `"Ubuntu"` returns before
its first `yield`, so it emits no requests and no report lines at all —
including on the RedHat and Alpine hosts where Ansible's `include_vars`
raised on a missing vars file.

## 7. Transcript

From `tests/roles/test_htpasswd.py::test_tree_snapshot`, against the
simulated Ubuntu host with two credentials (`alice` in `apr_md5_crypt`,
`bob` in `sha1`) plus a RedHat host the role never touches. Fresh host,
first pass:

```text
(root)
  wave 0
    ubuntu: Install the Apache utilities. [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install apache2-utils
    barrier: FileContents → None
  wave 1
    ubuntu: Manage htpasswd entries in /etc/apache2/.htpasswd. [changed: content /etc/apache2/.htpasswd is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-43de96e5cafcfdcb/content
        $ sudo sh -c 'mkdir -p /etc/apache2 && cp /tmp/reemote-install-43de96e5cafcfdcb/content /etc/apache2/.htpasswd && chown root:www-data /etc/apache2/.htpasswd && chmod 640 /etc/apache2/.htpasswd && rm -rf /tmp/reemote-install-43de96e5cafcfdcb'
    ubuntu: Ensure /etc/apache2/.htpasswd ownership and mode. [no change: file /etc/apache2/.htpasswd already exists]
```

Note what is *not* there: the `$ scp <files install> -> …` line names the
dest-derived spool but never the content, so no password and no hash appear
in any request line. The write pair's `chown` runs before the `chmod`, and
the freshly installed file already matches, so the metadata companion
no-ops in the same wave.

A host whose file already converges — seeded with both users' matching lines:

```text
(root)
  wave 0
    ubuntu: Install the Apache utilities. [changed: 1 package(s) to install]
    barrier: FileContents → alice:$apr1$Qux7Yh3z$Y0pHsg2yeq6DVprpMeCMb0
bob:{SHA}/EjZqc5FO6inlw9PI4BynEAxq9I=

  wave 1
    ubuntu: Manage htpasswd entries in /etc/apache2/.htpasswd. [no change: content already up to date (/etc/apache2/.htpasswd)]
    ubuntu: Ensure /etc/apache2/.htpasswd ownership and mode. [no change: file /etc/apache2/.htpasswd already exists]
```

Both managed lines verified, so the recomputed content is byte-identical
despite the random-salt hashing — the migration's idempotence claim in one
tree. The `FileContents` barrier line prints the fact's raw `str` outcome
verbatim (like `repo_epel`'s `fetch_url` line), which is the one visible
surface; see D5. The RedHat host appears in neither tree: no barrier record,
no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | Ubuntu only: the `include_vars` Debian/RedHat/Archlinux dispatch collapses to frozen module constants, and an off-target host emits **nothing at all** | the family guard is the body's opening `if`; the other families' package lists are a different role |
| D2 | `python3-passlib` is dropped from the default packages | the hashing moved control-side into pure-Python `$apr1$`/`{SHA}`/bcrypt/DES implementations, so the target needs no passlib; `required_packages` still overrides |
| D3 | The symbolic default mode `u+rw,g+r` becomes the octal `"0640"` | the spool-and-install family takes octal digits only; the value is identical |
| D4 | Closed by [#74](https://github.com/kimjarvis/reemote/issues/74): the accepted `hash_scheme` set is the operation's, `files.HTPASSWD_SCHEMES` | `plain`, `apr_md5_crypt`, `md5_crypt`, `apr_sha1`, `bcrypt` and `descrypt`, reached through the upstream `sha1`/`crypt`/`apache_md5` spellings too; only the passlib-only `sha256_crypt`/`sha512_crypt` handlers stay unimplemented, being outside what Apache or Nginx read |
| D5 | `htpasswd_nolog` is inert | request lines never render install content; the one visible surface is the `FileContents` barrier line, which echoes the file's current text like `repo_epel`'s `fetch_url` line does — prefer a hashed scheme over `plain` |
| D6 | Credentials sharing one path merge in a single read/write pair, and the **last** credential for a path decides its owner/group/mode | Ansible ran one `htpasswd` task per credential, each re-reading the file; the in-order merge reproduces the same final content in one write |
| D7 | Install idempotence is **content-only**; the companion `files.file` converges owner/group/mode drift | the install family's contract; the role's per-task `owner`/`group`/`mode` re-applied every run, so the pair reproduces it |

## 9. Scoped out

* **Removing entries.** The upstream module's `state=absent` (delete a user,
  delete the whole file if empty) is not part of the role's task list, so the
  role never removes anything. The operation ships it as
  `files.htpasswd(present=False)`; emptying the file is `files.file(present=False)`.
* **Unmanaged users.** The role never deletes a line it does not manage; a
  file may hold more users than `credentials` lists.
* **Other families.** See D1; the RedHat `httpd-tools` and Archlinux variants
  are the same two requests with different constants.
* **passlib-only schemes.** `sha256_crypt`/`sha512_crypt` and the other
  passlib handlers stay unimplemented (D4): Apache and Nginx do not read them.
* **The webserver role itself.** The upstream molecule scenarios pair this
  role with `geerlingguy.nginx`/`geerlingguy.apache` and point a vhost's
  `auth_basic_user_file` at the managed file; that wiring is a caller-side
  concern — compose the two roles in one program.

--->
