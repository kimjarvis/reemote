import asyncio

from commands.sftp import Copy, Get, Isdir, Isfile, Mkdir, Mput, Put, Stat
from construction_tracker import track_construction, track_yields
from execute import execute


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

        # Verify
        r = yield Isdir(path="/tmp/txt",group="local")
        if r.executed and r.output != [{'value': True}]:
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

        # r = yield Stat(path="/home/user/a.txt",follow_symlinks=True,group="A")

async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
