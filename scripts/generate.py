import asyncio
from examples.sftp.get.IsDir import main as get_IsDir
from examples.core.get.Call import main as get_Call
from examples.core.post.Call import main as post_Call
from examples.core.put.Call import main as put_Call
from examples.core.get.Fact import main as get_Fact
from block_insert import block_insert

async def main():
    await get_IsDir()
    await get_Call()
    await post_Call()
    await put_Call()
    await get_Fact()
    block_insert(source_path="~/reemote/", insert_path="~/reemote/")


if __name__ == "__main__":
    asyncio.run(main())
