# main.py
import asyncio
from reemote.execute import execute
from reemote.commands.server import Shell

class Hello:
    async def execute(self):
        a = yield Shell(cmd="pwd")
        print(type(a))
        b = yield Shell(name="echo",
                        cmd="echo Hello World!",
                        group="all",
                        sudo=False)
        c = yield Shell(cmd="ls -ltr")

class Parent:
    async def execute(self):
        a = yield Shell(cmd="hostname")
        r = yield Hello()

class Root:
    async def execute(self):
        r = yield Parent()
        for x in r:
            print(x)

async def main():
    # Clear construction tracker at the beginning


    await execute(lambda: Root())


if __name__ == "__main__":
    asyncio.run(main())