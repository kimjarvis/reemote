import asyncio
from examples.sftp.get.IsDir import main as get_is_dir
from examples.core.get.Call import main as get_call
from examples.core.post.Call import main as post_call
from examples.core.put.Call import main as put_call
from examples.core.get.Fact import main as get_fact
from examples.core.get.Context import main as get_context
from examples.core.get.Return import main as get_return
from examples.core.post.Return import main as post_return
from examples.core.put.Return import main as put_return
from examples.core.post.Command import main as post_command
from examples.apt.get.Packages import main as get_packages
from examples.apt.put.Packages import main as put_packages

from block_insert import block_insert

async def main():
    await get_is_dir()
    await get_call()
    await post_call()
    await put_call()
    await get_fact()
    await get_context()
    await get_return()
    await post_return()
    await put_return()
    await post_command()
    await get_packages()
    await put_packages()
    block_insert(source_path="~/reemote/", insert_path="~/reemote/")


if __name__ == "__main__":
    asyncio.run(main())
