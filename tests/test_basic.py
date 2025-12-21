import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reemote.inventory import create_inventory
from reemote.execute import execute
from reemote.config import Config


# Autouse fixture that runs before each test
@pytest.fixture(autouse=True)
def setup_inventory():
    """Create inventory before each test"""
    create_inventory(
        [
            [
                {"host": "192.168.1.24", "username": "user", "password": "password"},
                {
                    "groups": ["all", "192.168.1.24"],
                },
            ],
            [
                {"host": "192.168.1.76", "username": "user", "password": "password"},
                {
                    "groups": ["all", "192.168.1.76"],
                },
            ],
        ]
    )


@pytest.mark.asyncio
async def test_shell():
    from reemote.commands.server import Shell

    class Root:
        async def execute(self):
            yield Shell(cmd="echo Hello", group="192.168.1.24")

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_callback():
    from reemote.commands.system import Callback

    async def _callback(host_info, global_info, command, cp, caller):
        assert command.value == "test callback"

    class Root:
        async def execute(self):
            yield Callback(
                callback=_callback, group="192.168.1.24", value="test callback"
            )

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_return():
    from reemote.commands.system import Return
    from reemote.commands.server import Shell

    class Child:
        async def execute(self):
            a = yield Shell(cmd="echo Hello")
            b = yield Shell(cmd="echo World")
            yield Return(value=[a, b],changed=False)

    class Parent:
        async def execute(self):
            response = yield Child()
            print(response.changed)
            assert response.value[0].stdout.strip() == "Hello"
            assert response.value[1].stdout.strip() == "World"

    await execute(lambda: Parent())


@pytest.mark.asyncio
async def test_isdir():
    from reemote.facts.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="/home/user", group="192.168.1.24")
            if r:
                assert r.output

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_mkdir():
    from reemote.facts.sftp import Isdir
    from reemote.commands.sftp import Mkdir, Rmdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="/home/user/freddy")
            if r and r.output:
                yield Rmdir(path="/home/user/freddy")
            r1 = yield Mkdir(path="/home/user/freddy", permissions=0o700)
            if r1:
                print(r1)

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_stat():
    from reemote.facts.sftp import Stat

    class Root:
        async def execute(self):
            # todo: remove debug message
            r = yield Stat(path="/home/user/freddy")
            if r and r.output:
                print(r.output)

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_directory():
    from reemote.operations.sftp import Directory

    class Child:
        async def execute(self):
            yield Directory(
                present=True,
                path="/home/user/freddy",
                group="192.168.1.76",
                permissions=0o700,
                # atime=0xDEADCAFE,
                # mtime=0xACAFEDAD,
            )

    class Parent:
        async def execute(self):
            response = yield Child()
            print(response)

    await execute(lambda: Parent())


@pytest.mark.asyncio
async def test_chmod():
    from reemote.commands.sftp import Chmod

    class Root:
        async def execute(self):
            # todo: make sure directory exists, with default permissions
            # r = yield Chmod(path="/home/user/freddy", permissions=0o773)
            r = yield Chmod(path="/home/user/freddy", permissions=511)
            if r and r.output:
                print(r.output)
            # todo: assert the permissions (same for chown, utime)

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_chown():
    from reemote.commands.sftp import Chown

    # todo: note that this isn't supported on SFTPv3, we need a system version
    class Root:
        async def execute(self):
            # todo: remove test output from commands
            r = yield Chown(path="/home/user/freddy", uid=1001)
            if r and r.output:
                print(r.output)

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_utime():
    from reemote.commands.sftp import Utime

    # todo: note that this isn't supported on SFTPv3, we need a system version
    class Root:
        async def execute(self):
            yield Utime(path="/home/user/freddy", atime=0xDEADCAFE, mtime=0xACAFEDAD)

    await execute(lambda: Root())
