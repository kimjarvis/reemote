import pytest

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from reemote.commands.sftp import isdir, isfile, put
from construction_tracker import track_construction, track_yields
from reemote.execute import execute
from reemote.inventory import get_inventory


@pytest.mark.asyncio
async def test_sftp():
    @track_construction
    class Root:
        @track_yields
        async def execute(self):
            print("Starting test...")

            print("trace 00")
            r = yield await isfile(path="/tmp/a.txt", group="local")
            assert r.executed and r.output == [{'value': True}]

            # Put to the remote host
            print("trace 01")
            r = yield await put(localpaths="/tmp/a.txt", remotepath="/home/user/", group="A")
            r = yield await isfile(path="/home/user/a.txt", group="A")
            assert r.executed and r.output == [{'value': True}]

            # Get from the remote host
            print("trace 02")
            r = yield Get(remotepaths="/home/user/a.txt", localpath="/tmp/b.txt", group="A")
            r = yield Isfile(path="/tmp/b.txt", group="local")
            assert r.executed and r.output == [{'value': True}]

            print("trace 03")
            # copy from one file to another on a remote host
            r = yield Copy(srcpaths="/home/user/a.txt", dstpath="/home/user/c.txt", group="A")
            r = yield Isfile(path="/home/user/c.txt", group="A")
            assert r.executed and r.output == [{'value': True}]

            r = yield Isfile(path="/tmp/b.txt", group="local")
            assert r.executed and r.output == [{'value': True}]

            print("trace 04")
            # Put files to the remote host
            r = yield Mput(localpaths="/tmp/*.txt", remotepath="/home/user/", group="A")
            r = yield Isfile(path="/home/user/b.txt", group="A")
            assert r.executed and r.output == [{'value': True}]

            print("trace 05")
            # Make a new directory to receive the files
            r = yield Mkdir(path="/tmp/txt/", group="local")
            r = yield Isdir(path="/tmp/txt", group="local")
            assert r.executed and r.output == [{'value': True}]

            print("trace 06")
            # Get from the remote host
            r = yield Get(remotepaths="/home/user/*.txt", localpath="/tmp/txt", group="A")
            r = yield Isfile(path="/tmp/txt/b.txt", group="local")
            assert r.executed and r.output == [{'value': True}]

            r = yield Stat(path="/home/user/a.txt", follow_symlinks=True, group="A")
            assert r.output[0]["value"]["permissions"] == 33204

            assert False

    # Execute the test
    await execute(lambda: Root())
