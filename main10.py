import asyncio

from reemote.api.apt import Package
from reemote.execute import endpoint_execute
from reemote.checks import changed, flatten


class Root:
    async def execute(self):
        r = yield Package(
            name="initial remove package",
            packages=["tree"],
            present=False,
            group="all",
            sudo=True,
        )
        r = yield Package(
            name="install package",
            packages=["tree"],
            present=True,
            group="all",
            sudo=True,
        )
        for x in flatten(r):
            print(x)
        if changed(r):
            print("changed")


async def main():
    await endpoint_execute(lambda: Root())


if __name__ == "__main__":
    asyncio.run(main())
