import asyncio
from reemote.execute import execute
from reemote.commands.sftp import Mkdir

class Root:
    async def execute(self):
        r = yield Mkdir(path="testdata/new_dir", group="192.168.1.24")
        print(f"debug 00 {r}")

async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
