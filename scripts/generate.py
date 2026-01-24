import asyncio
from examples.sftp.get.IsDir import main as IsDir
from block_insert import block_insert

async def main():
    await IsDir()
    block_insert(source_path="~/reemote", insert_path="~/reemote/examples")


if __name__ == "__main__":
    asyncio.run(main())
