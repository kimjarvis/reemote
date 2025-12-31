import asyncio
from reemote.execute import endpoint_execute
from reemote.api.sftp import StatVfs

async def main():
    r = await endpoint_execute(lambda: StatVfs(path="/home/user/testdata"))
    print(r)

if __name__ == "__main__":
    asyncio.run(main())
