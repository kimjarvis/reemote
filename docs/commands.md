# Command descriptors

`reemote5.commands` defines the base `Command`, the raw `StringCommand` and
the `TransferCommand` base for descriptors that copy bytes over the
connection instead of running shell text; the concrete typed descriptors live
beside the operations that yield them (`reemote5.modules.dnf.dnf`,
`reemote5.modules.files.files`). They are dumb,
frozen value objects: an operation never executes anything, it only describes
what would have to run. The typed descriptors exist so the test framework's
`SimulatedHost` (in `tests/simulated_host.py`) can dispatch on them in its
`execute_command` and apply the equivalent mutation; the library's
[`send_command`][reemote5.drivers.send_command] forwards to that seam. On the
real transport a `TransferCommand` is dispatched on its base —
`send_command` hands the host to its `transfer`, which copies over the
connection's own protocol (asyncssh's scp) rather than through a shell, so
the `sudo`/`su` escalation flags do not apply to it.

| Descriptor | Shell form | Simulated effect |
| --- | --- | --- |
| [`DnfInstallCommand`][reemote5.modules.dnf.dnf.DnfInstallCommand] | `dnf install -y <pkgs>` | adds packages (plus their `installs_links` side effects) |
| [`DnfRemoveCommand`][reemote5.modules.dnf.dnf.DnfRemoveCommand] | `dnf remove -y <pkgs>` | removes packages |
| [`LinkCommand`][reemote5.modules.files.files.LinkCommand] | `ln -sf <target> <src>` | sets `links[src] = target` |
| [`LinkCommand`][reemote5.modules.files.files.LinkCommand] with `target=None` | `rm -f <src>` | removes `links[src]` |
| [`PutCommand`][reemote5.modules.files.files.PutCommand] | none — scp transfer of an IO-like source, logged as `scp <source> -> <dest>` | records `remote_files[dest]` = the source content |
| [`GetCommand`][reemote5.modules.files.files.GetCommand] | none — scp transfer into an IO-like destination, logged as `scp <src> -> <dest>` | writes `remote_files[src]` into the destination stream |
| [`StringCommand`][reemote5.commands.StringCommand] | the raw string | none (accepted, not simulated) |

## Reference

::: reemote5.commands
    options:
      members:
        - Command
        - StringCommand
        - TransferCommand

The typed descriptors are documented with their operations:
[`DnfInstallCommand`][reemote5.modules.dnf.dnf.DnfInstallCommand] and
[`DnfRemoveCommand`][reemote5.modules.dnf.dnf.DnfRemoveCommand] in
[dnf module](modules/dnf.md),
[`LinkCommand`][reemote5.modules.files.files.LinkCommand],
[`PutCommand`][reemote5.modules.files.files.PutCommand] and
[`GetCommand`][reemote5.modules.files.files.GetCommand] in
[files module](modules/files.md).
