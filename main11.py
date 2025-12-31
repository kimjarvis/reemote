import asyncio
from reemote.execute import endpoint_execute
from reemote.api.sftp import Isdir


async def main():
    await endpoint_execute(lambda: Isdir(path="/home/user/fred"))

if __name__ == "__main__":
    asyncio.run(main())
