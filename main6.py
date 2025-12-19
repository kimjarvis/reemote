import asyncio

from reemote.execute import execute
from reemote.operations.apt import Package

async def main():

    await execute(lambda: Package(name="apt package tree",
                                                         packages =["tree"],
                                                         present = False,
                                                         group="all",
                                                         sudo=True))

if __name__ == "__main__":
    asyncio.run(main())
