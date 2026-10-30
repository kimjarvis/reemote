# Privilege demonstrations

Scripts under `examples/demonstrations/privilege/` — scenarios that cross
module boundaries to show privilege escalation. Each targets a single
`centos9-*` host whose inventory carries a plain-text password. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

## Elevating with a sudo password

The base example's deploy, unchanged, run against a single host whose
inventory carries a `privilege` section — the run-as user and a plain-text
`sudo_password`. The operations still only *ask* to elevate with `sudo=True`;
the credential comes from the inventory alone, and the driver supplies it
through a generated askpass helper (`sudo -A`, no TTY) without ever logging it.
It also shows how a rejected credential is reported: `main` catches
[`PrivilegeError`][reemote5.hosts.PrivilegeError] and prints one line naming the
host, instead of surfacing sudo's three password retries as a traceback.

::: privilege_sudo_pw
    options:
      members: []

## Elevating with su

The `su` counterpart of the sudo-password example: the same deploy shape, run
against a single host whose inventory `privilege` section carries a `su_user`
and a plain-text `su_password`. The operations only *ask* to elevate with
`su=True`; reemote5 runs a *default* `su <user> -c` (no login shell) and, since
`su` has no askpass mechanism, pipes the password to su's stdin through a
generated helper. The credential comes from the inventory alone and never
reaches the log, and a rejected password surfaces as a single
[`PrivilegeError`][reemote5.hosts.PrivilegeError] naming the host.

::: privilege_su
    options:
      members: []
