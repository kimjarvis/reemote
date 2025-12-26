import asyncio
from reemote.execute import execute
from reemote.commands.server import Shell

async def main():
    responses = await execute(lambda: Shell(cmd="echo Hello World!"))
    for r in responses:
        print("*",r)

if __name__ == "__main__":
    asyncio.run(main())
