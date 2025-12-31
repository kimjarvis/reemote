import asyncio
from reemote.execute import endpoint_execute


class Hello:
    async def execute(self):
        from reemote.api.server import Shell

        r = yield Shell(name="echo", cmd="echo Hello Kimbo!", group="all", sudo=False)
        r = yield Shell(name="echo", cmd="echo Hello World!", group="all", sudo=False)
        print(f"> {r}")


class Root:
    async def execute(self):
        r = yield Hello()
        print(f"* {r}")


async def main():
    await endpoint_execute(lambda: Root())


if __name__ == "__main__":
    asyncio.run(main())
