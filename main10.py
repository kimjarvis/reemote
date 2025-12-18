import asyncio

from reemote.operations.apt import Package
from reemote.construction_tracker import track_construction, track_yields
from reemote.execute import execute
from reemote.checks import changed, flatten


@track_construction
class Root:
    @track_yields
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
    responses = await execute(lambda: Root())
    # assert changed(responses)


if __name__ == "__main__":
    asyncio.run(main())
