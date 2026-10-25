# Command descriptors

`reemote5.commands` defines the base `Command` and the raw `StringCommand`;
the typed descriptors live beside the operations that yield them
(`reemote5.operations.dnf`, `reemote5.operations.files`). They are dumb,
frozen value objects: an operation never executes anything, it only describes
what would have to run. The typed descriptors exist so the test framework's
`SimulatedHost` (in `tests/simulated_host.py`) can dispatch on them in its
`execute_command` and apply the equivalent mutation; the library's
[`send_command`][reemote5.drivers.send_command] forwards to that seam.

| Descriptor | Shell form | Simulated effect |
| --- | --- | --- |
| [`DnfInstallCommand`][reemote5.operations.dnf.DnfInstallCommand] | `dnf install -y <pkgs>` | adds packages (plus their `installs_links` side effects) |
| [`DnfRemoveCommand`][reemote5.operations.dnf.DnfRemoveCommand] | `dnf remove -y <pkgs>` | removes packages |
| [`LinkCommand`][reemote5.operations.files.LinkCommand] | `ln -sf <target> <src>` | sets `links[src] = target` |
| [`LinkCommand`][reemote5.operations.files.LinkCommand] with `target=None` | `rm -f <src>` | removes `links[src]` |
| [`StringCommand`][reemote5.commands.StringCommand] | the raw string | none (accepted, not simulated) |

## Reference

::: reemote5.commands
    options:
      members:
        - Command
        - StringCommand

The typed descriptors are documented with their operations:
[`DnfInstallCommand`][reemote5.operations.dnf.DnfInstallCommand] and
[`DnfRemoveCommand`][reemote5.operations.dnf.DnfRemoveCommand] in
[dnf operations](operations/dnf.md),
[`LinkCommand`][reemote5.operations.files.LinkCommand] in
[files operations](operations/files.md).
