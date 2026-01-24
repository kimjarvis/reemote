import asyncio
from block_insert import block_insert

async def main():
    block_insert(source_path="~/reemote/", insert_path="~/reemote/")

if __name__ == "__main__":
    asyncio.run(main())
