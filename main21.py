import asyncio
from reemote.execute import execute
from reemote.facts.apt import GetPackages
from reemote.commands.apt import Update

async def main():
    # await execute(
    #     lambda: Install(
    #         name="install tree", packages=["tree", "vim"], group="all", sudo=True
    #     )
    # )
    # await execute(lambda: Remove(packages=["tree", "vim"], group="all", sudo=True))
    await execute(lambda: Update(sudo=True))
    # await execute(lambda: Upgrade(sudo=True))
    r=await execute(lambda: GetPackages())
    print(r[-1].output)

if __name__ == "__main__":
    asyncio.run(main())
