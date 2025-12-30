import pytest

from reemote.commands.apt import Install, Remove, Update, Upgrade
from reemote.execute import endpoint_execute



@pytest.mark.asyncio
async def test_apt_install(setup_inventory):
    class Root:

        async def execute(self):
            r = yield Install(
                name="install tree", packages=["tree", "vim"], group="all", sudo=True
            )
            if r:
                assert r["changed"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_apt_remove(setup_inventory):
    class Root:

        async def execute(self):
            r = yield Remove(
                name="remove tree", packages=["tree", "vim"], group="all", sudo=True
            )
            if r:
                assert r["changed"]


@pytest.mark.asyncio
async def test_apt_update(setup_inventory):
    class Root:

        async def execute(self):
            r = yield Update(group='all',name=None,sudo=False,su=False,get_pty=False)
            if r:
                assert r["changed"]

@pytest.mark.asyncio
async def test_apt_upgrade(setup_inventory):
    class Root:

        async def execute(self):
            r = yield Upgrade(sudo=True)
            if r:
                assert r["changed"]




# @pytest.mark.asyncio
# async def test_apt_get_packages():
#     """Test getting apt packages information without errors"""
#     responses = await execute(lambda: GetPackages(name="get packages"))
#
#     # Test passes if no exceptions were raised
#     assert responses is not None
#     print(f"Package information: {responses}")
#
#
# @pytest.mark.asyncio
# async def test_apt_package():
#     class Test_apt_package:
#         async def execute(self):
#             r = yield Package(
#                 name="1", packages=["tree"], present=False, group="all", sudo=True
#             )
#             r = yield Package(
#                 name="2", packages=["tree"], present=True, group="all", sudo=True
#             )
#
#     """Test getting apt packages information without errors"""
#     responses = await execute(lambda: Test_apt_package())
#     validated_responses = await validate_responses(responses)
#     for r in validated_responses:
#         if r.name == "2":
#             assert r.changed
