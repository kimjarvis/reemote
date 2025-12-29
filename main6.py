import asyncio

from reemote.execute import endpoint_execute
from reemote.operations.apt import Package


async def main():
    await endpoint_execute(
        lambda: Package(
            name="apt package tree",
            packages=["tree"],
            present=False,
            group="all",
            sudo=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
