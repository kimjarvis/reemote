import os
import sys
from typing import AsyncGenerator

import pytest

from reemote.context import Context
from reemote.core.response import ResponseElement
from reemote.execute import endpoint_execute

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

@pytest.mark.asyncio
async def test_apt_apt_getpackages(setup_inventory):
    from reemote.apt1 import GetPackages

    class Root:
        async def execute(self) -> AsyncGenerator[Context, ResponseElement]:
            r = yield GetPackages(name="get packages")
            if r:
                assert not r["changed"]

    r = await endpoint_execute(lambda: Root())
    print(r)
    assert len(r) == 2


@pytest.mark.asyncio
async def test_apt_apt_update(setup_inventory):
    from reemote.apt1 import Update

    class Root:
        async def execute(self) -> AsyncGenerator[Context, ResponseElement]:
            r = yield Update(group="all", name=None, sudo=False, su=False)
            if r:
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    print(r)
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_apt_upgrade(setup_inventory):
    from reemote.apt1 import Upgrade

    class Root:
        async def execute(self):
            r = yield Upgrade(sudo=True)

    r = await endpoint_execute(lambda: Root())
    print(r)
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_apt_install(setup_inventory):
    from reemote.apt1 import Install

    class Root:
        @staticmethod
        async def execute():
            r = yield Install(
                name="install tree", packages=["tree", "vim"], group="all", sudo=True
            )
            print(r)

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_apt_remove(setup_inventory):
    from reemote.apt1 import Remove

    class Root:
        async def execute(self):
            r = yield Remove(
                name="remove tree", packages=["tree", "vim"], group="all", sudo=True
            )
            if r:
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_package(setup_inventory):
    from reemote.apt1 import Package

    class Root:
        async def execute(self):
            r = yield Package(
                name="apt package tree",
                packages=["tree"],
                update=True,
                present=True,
                group="all",
                sudo=True,
            )

    r = await endpoint_execute(lambda: Root())
    print(r)