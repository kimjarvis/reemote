# Command descriptors

`reemote5.commands` defines the base `Command`, the raw `StringCommand`, the
`TransferCommand` base for descriptors that copy bytes over the connection
instead of running shell text, and the `WaitCommand` base for descriptors that
poll the host until a condition holds; the concrete typed descriptors live
beside the operations that yield them (`reemote5.modules.dnf.dnf`,
`reemote5.modules.files.files`, `reemote5.modules.server.server`). They are dumb,
frozen value objects: an operation never executes anything, it only describes
what would have to run. The raw `StringCommand` carries shell text verbatim —
its `shell()` returns the command unchanged, so it runs (and takes the
`sudo`/`su` prefixes) exactly like a typed shell descriptor; it is the vehicle
for a demo-side operation running a one-off command such as the `locale-gen`
step of the
[locale recipe](examples/modules/server.md#managing-locales-pyinfra-serverlocale-parity)
without a library operation. The typed descriptors exist so the test framework's
`SimulatedHost` (in `tests/simulated_host.py`) can dispatch on them in its
`execute_command` and apply the equivalent mutation; the library's
[`send_command`][reemote5.drivers.send_command] forwards to that seam. On the
real transport a `TransferCommand` is dispatched on its base —
`send_command` hands the host to its `transfer`, which copies over the
connection's own protocol (asyncssh's scp) rather than through a shell, so
the `sudo`/`su` escalation flags do not apply to it. A `WaitCommand` is
dispatched the same way — `send_command` awaits its `wait`, which polls the
host until its condition holds or raises `WaitTimeoutError` — and is likewise
unprivileged (see [server module](modules/server.md#waiting-for-a-condition)).

## Reference

::: reemote5.commands
    options:
      members:
        - Command
        - StringCommand
        - TransferCommand
        - WaitCommand
        - WaitTimeoutError
