import asyncio
from reemote.execute import endpoint_execute
from reemote.commands.apt import Install


async def main():
    await endpoint_execute(
        lambda: Install(
            name="install tree", packages=["tree", "vim"], group="all", sudo=True
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
