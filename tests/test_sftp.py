import pytest

from commands.sftp import Isdir, Mkdir, Rmdir
from construction_tracker import track_construction, track_yields
from execute import execute
from inventory import get_inventory
from response import validate_responses
from utilities.checks import flatten


@pytest.mark.asyncio
async def test_mkdir():
    @track_construction
    class Root:
        @track_yields
        async def execute(self):
            r = yield Rmdir(name="1",path="/home/user/fred")
            r = yield Isdir(name="2",path="/home/user/fred")
            r = yield Mkdir(name="3",path="/home/user/fred")
            r = yield Isdir(name="4",path="/home/user/fred")
            assert False

    # Execute the test
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
    validated_responses = await validate_responses(responses)
    for r in validated_responses:
        if r.name=="2":
            assert not r.output
        if r.name=="4":
            assert r.output
