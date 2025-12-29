import asyncio
from reemote.execute import endpoint_execute
from reemote.facts.apt import GetPackages
from reemote.commands.apt import Update

async def main():
    # await execute(
    #     lambda: Install(
    #         name="install tree", packages=["tree", "vim"], group="all", sudo=True
    #     )
    # )
    # await execute(lambda: Remove(packages=["tree", "vim"], group="all", sudo=True))
    await endpoint_execute(lambda: Update(sudo=True))
    # await execute(lambda: Upgrade(sudo=True))
    r=await endpoint_execute(lambda: GetPackages())
    if r:
        print(r[-1]["value"])

if __name__ == "__main__":
    asyncio.run(main())
