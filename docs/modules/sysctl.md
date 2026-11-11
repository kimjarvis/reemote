# sysctl module

<!---

The `sysctl` namespace is the canonical home for everything sysctl — runtime
visibility, persistent-configuration visibility and management. The `Sysctl`
runtime fact was **relocated here from `server`**, which no longer exports
it: the top-level `reemote.Sysctl` binding now points at `sysctl.Sysctl`,
whose parse keeps the baseline's `server.Sysctl` semantics verbatim.

the baseline ships no dedicated sysctl module — only `server.sysctl`, whose
runtime half is a raw `sysctl key=value` shell and whose persist half is a
`files.line` edit. This namespace supersedes that recipe (which stays as the
strict-parity demonstration in
[`server_op_sysctl.py`](../examples/modules/server.md#ensuring-a-sysctl-setting-baseline-serversysctl-parity))
with three facts and two original operations:

- `Sysctl` dumps the **running kernel's** values (`sysctl -a`), int-coercing
  simple values exactly like the baseline.
- `SysctlFile` reads the **raw text** of one config file (`str`, or `None`
  when absent — a marker distinguishes an existing-but-empty file from a
  missing one).
- `SysctlFiles` parses **every admin-managed config file** —
  `/etc/sysctl.conf` plus `/etc/sysctl.d/*.conf` — into a dict of path →
  settings, so runtime-vs-config drift is observable. Vendor directories
  (`/usr/lib/sysctl.d`, `/run/sysctl.d`) are deliberately out of scope.

`setting` manages one kernel parameter along two independent, separately
switchable halves: the runtime apply (`sysctl -w`, gated by `Sysctl` with
the baseline's value normalization — lists join with spaces, digits compare as
ints) and the persistence edit (gated by `SysctlFile`). The persistence edit
is a **keyed replacement**, not the baseline's append-only `files.line` parity:
the first active `key = ...` line is replaced in place, duplicates collapse,
the line is appended when absent, and `present=False` removes the key's
lines — commented lines are never touched. `conf` manages a **whole file**
from a tuple of `(key, value)` pairs (the `systemd.dropin` whole-file model:
stale lines are replaced, not kept), defaulting to the
`/etc/sysctl.d/99-reemote.conf` drop-in so the legacy file and other
packages' drop-ins stay untouched. A tuple, not a dict: `op()` rejects
unhashable request arguments.

Both operations write root-owned `/etc` paths through the spool-transfer +
elevated-install pattern (`systemd.dropin` / `dnf.repo`): an unprivileged scp
spools the content into a dest-derived `/tmp` directory, then an elevated `sh -c`
creates the destination directory if needed, installs the file `0644` and removes
the spool directory. A changed write is followed by a **scoped** `sysctl -p <file>`
reload (`reload=False` defers the settings to the next boot) — scoped
rather than `sysctl --system`, which would re-apply unrelated drop-ins too.
Writing and applying need root, so flag the requests `sudo=True`; the
transfer itself stays unprivileged regardless. `present=False` removes
persistence only — runtime values are left alone, since a kernel parameter
cannot be "unset" (a removed key keeps its live value until reboot).

--->

::: reemote.modules.sysctl.sysctl
    options:
      show_root_heading: false
      members:
        - Sysctl
        - SysctlFile
        - SysctlFiles
        - conf
        - setting
