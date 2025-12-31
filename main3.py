import asyncio
from reemote.execute import endpoint_execute
from reemote.api.apt import Install


async def main():
    # await endpoint_execute(
    #     lambda: Install(
    #         name="install tree", packages=["tree", "vim"], group="all", sudo=True
    #     )
    # )
    class Root:
        async def execute(self):
            r = yield Install(
                name="install tree", packages=["tree", "vim"], group="all", sudo=True, get_pty=True
            )
            print(r)
            # if r:
            #     assert r["value"]["stdout"] == 'Hello\n'
            #     assert r["changed"]

    await endpoint_execute(lambda: Root())



if __name__ == "__main__":
    asyncio.run(main())
