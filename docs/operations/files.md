# files operations

`reemote5.operations.files` follows the same operation contract as every
other namespace module (see [dnf operations](dnf.md)): each operation is an
async generator taking an [`OpContext`][reemote5.core.OpContext], awaiting
facts via `ctx.facts`, setting `ctx.changed`/`ctx.reason` where a change is
needed, and yielding one [`Command`][reemote5.commands.Command] descriptor
per mutation.

## Reference

::: reemote5.operations.files
    options:
      members:
        - link
        - LinkCommand
        - file
        - FileCommand
