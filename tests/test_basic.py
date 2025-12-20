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

    class Child:
        async def execute(self):
            a = yield Shell(cmd="echo Hello")
            b = yield Shell(cmd="echo World")
            yield Return(value=[a, b])

    class Parent:
        async def execute(self):
            response = yield Child()
            assert response.value[0].stdout.strip() == "Hello"
            assert response.value[1].stdout.strip() == "World"

    await execute(lambda: Parent())


@pytest.mark.asyncio
async def test_isdir():
    from reemote.facts.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="/home/user", group="192.168.1.24")
            assert r.output

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_mkdir():
    from reemote.facts.sftp import Isdir
    from reemote.commands.sftp import Mkdir, Rmdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="/home/user/freddy", group="192.168.1.24")
            if r and r.output:
                yield Rmdir(path="/home/user/freddy", group="192.168.1.24")
            r1 = yield Mkdir(path="/home/user/freddy", group="192.168.1.24")
            if r1:
                print(r1)

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_stat():
    from reemote.facts.sftp import Stat

    class Root:
        async def execute(self):
            r = yield Stat(path="/home/user/freddy")
            if r and r.output:
                print(r.output)

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_directory():
    from reemote.operations.sftp import Directory

    class Child:
        async def execute(self):
            print("debut 00")
            yield Directory(present=True, path="/home/user/freddy", group="192.168.1.24")

    class Parent:
        async def execute(self):
            print("Starting test")
            response = yield Child()
            print(response)

    await execute(lambda: Parent())
