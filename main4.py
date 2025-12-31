import asyncio
from reemote.execute import endpoint_execute
from reemote.api.apt import Remove


async def main():
    await endpoint_execute(
        lambda: Remove(
            name="remove tree", packages=["tree", "vim"], group="all", sudo=True
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
