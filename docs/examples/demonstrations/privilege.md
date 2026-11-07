# Privilege demonstrations

Scripts under `examples/demonstrations/privilege/` — scenarios that cross
module boundaries to show privilege escalation. Each reads the shared
`inventory.json` with `load_inventory`, composes an `is_centos`
(`host.group == "centos"`) guard onto its deploy with `sub_deploy(..., when=...)`
so the `dnf` work — facts included — stays on the `centos` hosts, and attaches
its own `privilege` section to the loaded `centos` entries (the shared file
carries none): the sudo and su examples supply a plain-text password, while the
doas example is passwordless. Privilege remains an *inventory* parameter, never
a deploy-script one. See the [Examples overview](../../examples.md) for the
shared "connects for real" warning and the error-reporting contract every
script follows.

## Elevating with a sudo password

The base example's deploy, unchanged, run against the `centos` hosts whose
loaded inventory entries are given a `privilege` section — the run-as user and a
plain-text `sudo_password`. The operations still only *ask* to elevate with
`sudo=True`; the credential comes from the inventory alone, and the driver
supplies it through a generated askpass helper (`sudo -A`, no TTY) without ever
logging it.
It also shows how a rejected credential is reported: `main` catches
[`PrivilegeError`][reemote5.hosts.PrivilegeError] and prints one line naming the
host, instead of surfacing sudo's three password retries as a traceback.

::: privilege_sudo_pw
    options:
      members: []

## Elevating with su

The `su` counterpart of the sudo-password example: the same deploy shape, run
against the `centos` hosts whose loaded inventory entries are given a
`privilege` section carrying a `su_user` and a plain-text `su_password`. The
operations only *ask* to elevate with `su=True`; reemote5 runs a *default*
`su <user> -c` (no login shell) and, since `su` has no askpass mechanism, pipes
the password to su's stdin through a generated helper. The credential comes
from the inventory alone and never
reaches the log, and a rejected password surfaces as a single
[`PrivilegeError`][reemote5.hosts.PrivilegeError] naming the host.

::: privilege_su
    options:
      members: []

## Elevating with doas

The `doas` counterpart of the su example: the same deploy shape, run against
the `centos` hosts whose loaded inventory entries are given a `privilege`
section carrying only a `doas_user` — no password. The operations only *ask* to
elevate with `doas=True`; reemote5 always
runs `doas -n` (non-interactive), because `doas` has no askpass mechanism and
asyncssh channels offer no TTY. doas is therefore *passwordless*: the host must
grant the login user a `permit nopass` rule in `/etc/doas.conf`, and a denied
rule surfaces as a single
[`PrivilegeError`][reemote5.hosts.PrivilegeError] naming the host. `doas` is the
*outermost* escalation, so it combines with `sudo`/`su` in the fixed order
`doas` then `sudo` then `su`.

::: privilege_doas
    options:
      members: []
