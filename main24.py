import asyncio
from reemote.execute import endpoint_execute
from reemote.api.sftp import Mkdir

class Root:
    async def execute(self):
        r = yield Mkdir(path="testdata/new_dir", group="192.168.1.24")
        print(r)

async def main():
    await endpoint_execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
