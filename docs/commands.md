# Command descriptors

`reemote5.commands` defines the base `Command` and the raw `StringCommand`;
the typed descriptors live beside the operations that yield them
(`reemote5.operations.apt`, `reemote5.operations.files`). They are dumb,
frozen value objects: an operation never executes anything, it only describes
what would have to run. The typed descriptors exist so the test framework's
`SimulatedHost` (in `tests/simulated_host.py`) can dispatch on them in its
`execute_command` and apply the equivalent mutation; the library's
[`send_command`][reemote5.drivers.send_command] forwards to that seam.

| Descriptor | Shell form | Simulated effect |
| --- | --- | --- |
| [`AptInstallCommand`][reemote5.operations.apt.AptInstallCommand] | `apt-get install -y <pkgs>` | adds packages (plus their `installs_links` side effects) |
| [`AptRemoveCommand`][reemote5.operations.apt.AptRemoveCommand] | `apt-get remove -y <pkgs>` | removes packages |
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
[`AptInstallCommand`][reemote5.operations.apt.AptInstallCommand] and
[`AptRemoveCommand`][reemote5.operations.apt.AptRemoveCommand] in
[apt operations](operations/apt.md),
[`LinkCommand`][reemote5.operations.files.LinkCommand] in
[files operations](operations/files.md).
