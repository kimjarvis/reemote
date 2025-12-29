import asyncio
from reemote.execute import endpoint_execute
from reemote.commands.server import Shell


async def main():
    await endpoint_execute(
        lambda: Shell(name="echo", cmd="echo Hello World!", group="all", sudo=False)
    )


if __name__ == "__main__":
    asyncio.run(main())
