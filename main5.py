import asyncio
from reemote.execute import endpoint_execute
from reemote.operations.apt import Package

class Root:
    async def execute(self):
        r = yield Package(
            name="apt package tree",
            packages=["tree"],
            update=True,
            present=True,
            group="all",
            sudo=True,
        )
        print(r)

async def main():
    await endpoint_execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
