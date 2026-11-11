# Module examples: sysctl

Scripts under `examples/modules/sysctl/` — one per fact or operation of the
[`sysctl`](../../modules/sysctl.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`sysctl_sysctl.py` reading `Sysctl`
for three named keys, `sysctl_sysctl_file.py` reading `SysctlFile` for
`/etc/sysctl.conf` and `sysctl_sysctl_files.py` reading `SysctlFiles` across
the standard persistence paths, all over `gather_fact`) are not rendered
here — they read a single fact, and the facts themselves are documented on
the [`sysctl` module page](../../modules/sysctl.md). The runtime fact's
example relocated here from `examples/modules/server/server_sysctl.py` when
the `Sysctl` fact moved to this namespace.

Both operation examples read the shared project-root `inventory.json`,
guard every request with `when=is_centos` (the web1/web2 hosts) and flag
their requests `sudo=True` — writing root-owned `/etc`
configuration and applying kernel settings needs root, while the spool
transfer itself stays unprivileged. They write kernel tuning and reload it,
so they are compile-checked and exercised by the offline example harness
rather than run against shared hosts; point `INVENTORY_PATH` at your own
file of throwaway machines to execute them for real.

**Host-policy prerequisite.** `sysctl` (from `procps-ng`) is already
installed and the requests are correctly flagged `sudo=True`, but applying
a kernel key (`sysctl -w`) and writing `/etc` both require an **unconfined
sudo domain**. On the demo hosts sudo lands in the confined `staff_t`
domain, so `sysctl -w vm.swappiness` fails with "permission denied on key"
and the drop-in install fails reading its `/tmp` spool / writing
`/etc/sysctl.d` — a host-policy prerequisite, not a script gap. Map the
login's sudo role to an unconfined domain (e.g. `unconfined_t`) before
these examples can apply; the read-only fact gathers are unaffected.

## Managing a single kernel parameter

The setting operation: `sysctl.setting` is a reemote original superseding
the baseline's `server.sysctl` recipe. It manages one parameter along two
independent halves — the runtime apply (`sysctl -w`, gated by the
[`Sysctl`][reemote.modules.sysctl.sysctl.Sysctl] fact with the baseline's value
normalization) and the persistence edit (a keyed line replacement gated by
[`SysctlFile`][reemote.modules.sysctl.sysctl.SysctlFile]) — each switchable
via `sysctl_set` and `persist`. `present=False` removes the key from the
persistence file (runtime values stay), `sysctl_file` targets a
`/etc/sysctl.d` drop-in instead of the legacy `/etc/sysctl.conf`, and
`ignore_errors=True` tolerates unknown or read-only keys on heterogeneous
fleets. The script yields the same request twice to show idempotence across
the two phases: prepare plans both as changes, while apply writes on the
first and reports the second as a no-op.

::: sysctl_setting
    options:
      members: []

## Managing a whole drop-in file

The conf operation: `sysctl.conf` is a reemote original for bulk
persistence — the whole file's content is the rendered `settings` pairs,
one `key = value` line each in the given order, written only when it differs
from what [`SysctlFile`][reemote.modules.sysctl.sysctl.SysctlFile] reads
back (the `systemd.dropin` whole-file model, not `setting`'s keyed edit).
The default target is the `/etc/sysctl.d/99-reemote.conf` drop-in, and a
changed write is followed by a scoped `sysctl -p <sysctl_file>` reload so
the settings apply immediately (`reload=False` defers them to the next
boot; `present=False` removes the file instead). `settings` is a tuple of
pairs, not a dict: `op()` rejects unhashable request arguments. The write
follows the same spool-and-install pattern — an unprivileged scp to `/tmp`,
then an elevated install creating `/etc/sysctl.d` if needed and landing the
file `0644`.

::: sysctl_conf
    options:
      members: []