import asyncio

from reemote.api.sftp import Copy, Get, Mkdir, Mput, Put
from reemote.api.sftp import Isdir, Isfile
from reemote.execute import endpoint_execute


class Root:
    """
    To run this test create two files in /tmp a.txt an b.txt
    """

    async def execute(self):
        r = yield Isfile(
            path="/tmp/a.txt", group="local", name="local Isfile /tmp/a.txt"
        )
        if r and r["value"]:
            print("ok")

        # Put to the remote host
        r = yield Put(
            localpaths="/tmp/a.txt",
            remotepath="/home/user/",
            group="A",
            name="A Put /home/user",
        )
        r = yield Isfile(
            path="/home/user/a.txt", group="A", name="local Isfile /tmp/a.txt"
        )
        if r and r["value"]:
            print("ok")

        # Get from the remote host
        r = yield Get(remotepaths="/home/user/a.txt", localpath="/tmp/b.txt", group="A")
        r = yield Isfile(path="/tmp/b.txt", group="local")
        if r and r["value"]:
            print("ok")

        # copy from one file to another on a remote host
        r = yield Copy(
            srcpaths="/home/user/a.txt", dstpath="/home/user/c.txt", group="A"
        )
        r = yield Isfile(path="/home/user/c.txt", group="A")
        if r and r["value"]:
            print("ok")

        r = yield Isfile(path="/tmp/b.txt", group="local")
        if r and r["value"]:
            print("ok")

        # Put files to the remote host
        r = yield Mput(localpaths="/tmp/*.txt", remotepath="/home/user/", group="A")
        r = yield Isfile(path="/home/user/b.txt", group="A")
        if r and r["value"]:
            print("ok")

        # Verify
        r = yield Isdir(path="/tmp/txt", group="local", name="local Isdir /tmp/txt")
        if r and not r["value"]:
            # Make a new directory to receive the files
            r = yield Mkdir(
                path="/tmp/txt/", group="local", name="local Mkdir /tmp/txt/"
            )

        # Verify
        r = yield Isdir(path="/tmp/txt", group="local", name="local Isdir /tmp/txt")
        if r and r["value"]:
            print("ok")

        # Get from the remote host
        r = yield Get(
            remotepaths="/home/user/b.txt",
            localpath="/tmp/txt",
            group="A",
            name="A Get /home/user/*.txt /tmp/txt",
        )
        r = yield Isfile(path="/tmp/txt/b.txt", group="local")
        if r and r["value"]:
            print("ok")

        # r = yield Stat(path="/home/user/a.txt",follow_symlinks=True,group="A")


async def main():
    await endpoint_execute(lambda: Root())


if __name__ == "__main__":
    asyncio.run(main())
