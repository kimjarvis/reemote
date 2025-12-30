import asyncio

from reemote.execute import endpoint_execute
from reemote.operations.apt import Package


async def main():
    r = await endpoint_execute(
        lambda: Package(
            name="apt package tree",
            packages=["tree"],
            update=True,
            present=False,
            group="all",
            sudo=True,
        )
    )
    print(r)


if __name__ == "__main__":
    asyncio.run(main())
