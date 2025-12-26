import asyncio
from reemote.execute import execute
from reemote.facts.sftp import StatVfs

async def main():
    r = await execute(lambda: StatVfs(path="/home/user/testdata"))
    print(r)

if __name__ == "__main__":
    asyncio.run(main())
