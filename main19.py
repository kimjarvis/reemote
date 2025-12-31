import asyncio
from reemote.execute import endpoint_execute
from reemote.server import Shell

async def main():
    responses = await endpoint_execute(lambda: Shell(cmd="echo Hello World!"))
    for r in responses:
        print(r)

if __name__ == "__main__":
    asyncio.run(main())
