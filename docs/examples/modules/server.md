# Module examples: server

Scripts under `examples/modules/server/` — one per fact or operation of the
[`server`](../../modules/server.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`server_arch.py`, reading `Arch`,
`server_authorized_keys.py`, reading `AuthorizedKeys` for the login user,
`server_date.py`, reading `Date`, `server_groups.py`, reading `Groups`,
`server_has_gui.py`, reading `HasGui`, `server_home.py`, reading `Home`
for the login user and for `root`, `server_hostname.py`, reading
`Hostname`, `server_kernel.py`, reading `Kernel`,
`server_kernel_modules.py`, reading `KernelModules`,
`server_kernel_version.py`, reading `KernelVersion`,
`server_last.py`, reading `Last`,
`server_linux_distribution.py`, reading `LinuxDistribution`,
`server_linux_gui.py`, reading `LinuxGui`,
`server_linux_name.py`, reading `LinuxName`,
`server_locales.py`, reading `Locales`,
`server_lsb_release.py`, reading `LsbRelease`,
`server_mounts.py`, reading `Mounts`,
`server_path.py`, reading `Path`,
`server_processes.py`, reading `Processes`,
`server_timezone.py`, reading `Timezone`,
`server_uptime.py`, reading `Uptime`,
`server_user.py`, reading `User`,
`server_users.py`, reading `Users`, and
`server_which.py`, reading `Which` for `python3` and `httpd`,
all over `gather_fact`) live alongside
the operation example in
`examples/modules/server/`. They are not rendered here — each reads a single
fact, and the facts themselves are documented on the
[`server` module page](../../modules/server.md).

## Starting a service and waiting for it

The waiting design in action: `server.service` starts and enables httpd under
`sudo` (idempotently — it reads the unit's `ActiveState`/`UnitFileState` and
yields `systemctl` commands only where the unit differs), then
`server.wait_service` yields a `WaitForFactCommand` that the driver *polls*
over SSH every two seconds until `SystemdServiceState` reports `active`, or
raises `WaitTimeoutError` after sixty. The wait is always planned as a change
— like pyinfra's `server.wait` it reads no facts, so the request sequence is
identical in both phases; whether the unit is already active is discovered by
the first poll. Both hosts finish the start step before either polls, and the
two poll loops then run concurrently.

::: wait_for_httpd
    options:
      members: []
