import asyncio
from reemote.execute import execute
from reemote.api.server import Shell



class Message:
    async def execute(self):
        yield Shell(cmd="echo Brave")
        yield Shell(cmd="echo New")


class Root:
    async def execute(self):
        response_A = yield Shell(cmd="echo Hello")
        print("A:",response_A.stdout)
        response_B = yield Message()
        print("B:",response_B.stdout)
        yield Shell(cmd="echo World!")


async def main():
    response_D = await execute(lambda: Root())
    for r in response_D:
        print("D:",r.stdout)


if __name__ == "__main__":
    asyncio.run(main())
