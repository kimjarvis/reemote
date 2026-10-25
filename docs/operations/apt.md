# apt operations

Operations live in namespace modules so a deploy reads `apt.packages` and
`files.link`, mirroring pyinfra's API. Every operation is an async generator
with the same fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where it
decides a change is needed, and yields one descriptor per command that would
mutate the host.

## Reference

::: reemote5.operations.apt
    options:
      members:
        - packages
        - AptInstallCommand
        - AptRemoveCommand
