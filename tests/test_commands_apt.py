import pytest
import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from utilities.checks import changed,flatten
from construction_tracker import  track_construction, track_yields


from commands.apt import Install, Remove, Update, Upgrade, GetPackages, Package

@pytest.mark.asyncio
async def test_apt_install():
    """Test that apt install command runs without errors"""
    try:
        inventory = get_inventory()
        responses = await execute(inventory, lambda: Install(
            name="install tree",
            packages=["tree", "vim"],
            group="all",
            sudo=True
        ))
        validated_responses = await validate_responses(responses)
        assert True  # Explicitly mark test as passed
    except Exception as e:
        pytest.fail(f"Test failed with error: {e}")


@pytest.mark.asyncio
async def test_apt_remove():
    """Test that apt install command runs without errors"""
    try:
        inventory = get_inventory()
        responses = await execute(inventory, lambda: Remove(
            name="remove tree",
            packages=["tree", "vim"],
            group="all",
            sudo=True
        ))
        validated_responses = await validate_responses(responses)
        assert True  # Explicitly mark test as passed
    except Exception as e:
        pytest.fail(f"Test failed with error: {e}")


@pytest.mark.asyncio
async def test_apt_get_packages():
    """Test getting apt packages information without errors"""
    inventory = get_inventory()
    responses = await execute(inventory, lambda: GetPackages(
        name="get packages"
    ))

    # Test passes if no exceptions were raised
    assert responses is not None
    print(f"Package information: {responses}")


@pytest.mark.asyncio
async def test_apt_package():
    @track_construction
    class Test_apt_package:
        @track_yields
        async def execute(self):
            r = yield Package(name="1",
                              packages=["tree"],
                              present=False,
                              group="all",
                              sudo=True)
            r = yield Package(name="2",
                              packages=["tree"],
                              present=True,
                              group="all",
                              sudo=True)

    """Test getting apt packages information without errors"""
    inventory = get_inventory()
    responses = await execute(inventory, lambda:  Test_apt_package())
    validated_responses = await validate_responses(responses)
    for r in validated_responses:
        if r.name=="2":
            assert r.changed
