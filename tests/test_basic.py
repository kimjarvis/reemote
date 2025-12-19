import pytest

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reemote.inventory import create_inventory
from reemote.execute import execute

from reemote.commands.server import Shell
from reemote.facts.sftp import Isdir
from reemote.command import Command
from reemote.commands.system import Callback, Return


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
    class Root:
        async def execute(self):
            yield Shell(cmd="echo Hello", group="192.168.1.24")

    await execute(lambda: Root())


@pytest.mark.asyncio
async def test_callback():
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
    class Child:
        async def execute(self):
            a = yield Shell(cmd="echo Hello")
            b = yield Shell(cmd="echo World")
            yield Return(value=[a, b])

    class Parent:
        async def execute(self):
            response = yield Child()
            assert strip(response.value[0].stdout) == "Hello"
            assert strip(response.value[1].stdout) == "World"

    await execute(lambda: Parent())


@pytest.mark.asyncio
async def test_isdir():
    class Root:
        async def execute(self):
            r = yield Isdir(path="/home/user", group="192.168.1.24")
            assert r.output

    await execute(lambda: Root())
