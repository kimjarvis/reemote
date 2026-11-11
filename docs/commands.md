# Command descriptors

`reemote.commands` defines the base `Command`, the raw `StringCommand`, the
`TransferCommand` base for descriptors that copy bytes over the connection
instead of running shell text, and the `WaitCommand` base for descriptors that
poll the host until a condition holds; the concrete typed descriptors live
beside the operations that yield them (`reemote.modules.dnf.dnf`,
`reemote.modules.files.files`, `reemote.modules.server.server`). They are dumb,
frozen value objects: an operation never executes anything, it only describes
what would have to run. The raw `StringCommand` carries shell text verbatim —
its `shell()` returns the command unchanged, so it runs (and takes the
`doas`/`sudo`/`su` prefixes) exactly like a typed shell descriptor; it is the vehicle
for a demo-side operation running a one-off command such as the `sysctl`
step of the
[sysctl recipe](examples/modules/server.md#ensuring-a-sysctl-setting-baseline-serversysctl-parity)
without a library operation. The typed descriptors exist so the test framework's
`SimulatedHost` (in `tests/simulated_host.py`) can dispatch on them in its
`execute_command` and apply the equivalent mutation; the library's
[`send_command`][reemote.drivers.send_command] forwards to that seam. On the
real transport a `TransferCommand` is dispatched on its base —
`send_command` hands the host to its `transfer`, which copies over the
connection's own protocol (asyncssh's scp) rather than through a shell, so
the `doas`/`sudo`/`su` escalation flags do not apply to it. A `WaitCommand` is
dispatched the same way — `send_command` awaits its `wait`, which polls the
host until its condition holds or raises `WaitTimeoutError` — and is likewise
unprivileged; see [`WaitCommand`][reemote.commands.WaitCommand] here and the
[`server.wait`][reemote.modules.server.server.wait] and
[`server.wait_service`][reemote.modules.server.server.wait_service] operations
that yield the concrete poll (both through the generic `WaitForFactCommand`,
which polls a fact class until a module-level predicate holds).

## Reference

::: reemote.commands
    options:
      members:
        - Command
        - StringCommand
        - TransferCommand
        - WaitCommand
        - WaitTimeoutError
