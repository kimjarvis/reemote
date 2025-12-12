import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
import logging
from utilities.logging import reemote_logging
from utilities.checks import flatten
from construction_tracker import track_construction, track_yields
from commands.sftp import Isdir, Isfile, Mkdir, Rmdir, Stat, Get,Put, Copy , Mcopy, Mput, Mget
from construction_tracker import track_construction, track_yields

@track_construction
class Root:
    """
    To run this test create two files in /tmp a.txt an b.txt
    """
    @track_yields
    async def execute(self):
        r = yield Isfile(path="/tmp/a.txt",group="local")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        # Put to the remote host
        r = yield Put(localpaths="/tmp/a.txt",remotepath="/home/user/",group="A")
        r = yield Isfile(path="/home/user/a.txt",group="A")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        # Get from the remote host
        r = yield Get(remotepaths="/home/user/a.txt",localpath="/tmp/b.txt",group="A")
        r = yield Isfile(path="/tmp/b.txt",group="local")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        # copy from one file to another on a remote host
        r = yield Copy(srcpaths="/home/user/a.txt",dstpath="/home/user/c.txt",group="A")
        r = yield Isfile(path="/home/user/c.txt",group="A")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        r = yield Isfile(path="/tmp/b.txt",group="local")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        # Put files to the remote host
        r = yield Mput(localpaths="/tmp/*.txt",remotepath="/home/user/",group="A")
        r = yield Isfile(path="/home/user/b.txt",group="A")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        # Make a new directory to receive the files
        r = yield Mkdir(path="/tmp/txt/",group="local")

        # Verify
        r = yield Isdir(path="/tmp/txt",group="local")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        # Get from the remote host
        r = yield Get(remotepaths="/home/user/*.txt",localpath="/tmp/txt",group="A")
        r = yield Isfile(path="/tmp/txt/b.txt",group="local")
        if r.executed and r.output == [{'value': True}]:
            print("ok")

        r = yield Stat(path="/home/user/a.txt",follow_symlinks=True,group="A")
        if r.output[0]["value"]["permissions"] == 33204:
            print("ok")

async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
    validated_responses = await validate_responses(responses)
    # Each response is now a UnifiedResult with all fields available:
    # for result in validated_responses:
    #     print(f"Host: {result.host}")
    #     print(f"Command: {result.command}")
    #     print(f"Output: {result.output}")
    #     print(f"Stdout: {result.stdout}")
    #     print(f"Stderr: {result.stderr}")
    #     print(f"Executed: {result.executed}")
    #     print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
