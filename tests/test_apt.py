import pytest

from reemote.apt import Install, Remove, Update, Upgrade
from reemote.apt import GetPackages
from reemote.apt import Package
from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_apt_apt_install(setup_inventory):
    class Root:
        async def execute(self):
            r = yield Install(
                name="install tree", packages=["tree", "vim"], group="all", sudo=True
            )
            if r:
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_apt_remove(setup_inventory):
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
async def test_apt_apt_update(setup_inventory):
    class Root:
        async def execute(self):
            r = yield Update(
                group="all", name=None, sudo=False, su=False
            )
            if r:
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_apt_upgrade(setup_inventory):
    class Root:
        async def execute(self):
            r = yield Upgrade(sudo=True)
            if r:
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_apt_get_packages(setup_inventory):
    class Root:
        async def execute(self):
            r = yield GetPackages(name="get packages")
            if r:
                assert not r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_apt_packages(setup_inventory):
    class Root:
        async def execute(self):
            r = yield Package(
                name="apt package tree",
                packages=["tree"],
                update=True,
                present=False,
                group="all",
                sudo=True,
            )

    await endpoint_execute(lambda: Root())
