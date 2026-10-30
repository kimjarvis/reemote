# Module examples: server

Scripts under `examples/modules/server/` — one per operation of the
[`server`](../../modules/server.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

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
