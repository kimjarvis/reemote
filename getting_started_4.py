import asyncio
from reemote.execute import execute
from reemote.api.server import Shell


class Message:
    async def execute(self):
        yield Shell(cmd="echo Brave")
        yield Shell(cmd="echo New")


class Root:
    async def execute(self):
        yield Shell(cmd="echo Hello")
        yield Shell(cmd="echo World!")


async def main():
    response = await execute(lambda: Message())
    for r in response:
        print(r.stdout)
    response = await execute(lambda: Root())
    for r in response:
        print(r.stdout)

if __name__ == "__main__":
    asyncio.run(main())
