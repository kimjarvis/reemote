import asyncio
from reemote.execute import endpoint_execute
from reemote.api.apt import GetPackages


async def main():
    # await execute(
    #     lambda: Install(
    #         name="install tree", packages=["tree", "vim"], group="all", sudo=True
    #     )
    # )
    # await execute(lambda: Remove(packages=["tree", "vim"], group="all", sudo=True))
    # await endpoint_execute(lambda: Update(sudo=True))
    # await execute(lambda: Upgrade(sudo=True))
    r = await endpoint_execute(lambda: GetPackages())
    print(r)

if __name__ == "__main__":
    asyncio.run(main())
