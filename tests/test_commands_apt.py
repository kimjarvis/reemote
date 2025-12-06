import pytest
import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses

@pytest.mark.asyncio
async def test_apt_install():
    """Test that apt install command runs without errors"""
    try:
        inventory = get_inventory()
        from commands.apt import Install
        responses = await execute(inventory, lambda: Install(
            name="install tree",
            packages=["tree", "vim"],
            group="All",
            sudo=True
        ))
        validated_responses = await validate_responses(responses)
        print(f"Test completed successfully. Responses: {validated_responses}")
        assert True  # Explicitly mark test as passed
    except Exception as e:
        pytest.fail(f"Test failed with error: {e}")


@pytest.mark.asyncio
async def test_apt_remove():
    """Test that apt install command runs without errors"""
    try:
        inventory = get_inventory()
        from commands.apt import Remove
        responses = await execute(inventory, lambda: Remove(
            name="remove tree",
            packages=["tree", "vim"],
            group="All",
            sudo=True
        ))
        validated_responses = await validate_responses(responses)
        print(f"Test completed successfully. Responses: {validated_responses}")
        assert True  # Explicitly mark test as passed
    except Exception as e:
        pytest.fail(f"Test failed with error: {e}")


@pytest.mark.asyncio
async def test_apt_get_packages():
    """Test getting apt packages information without errors"""
    inventory = get_inventory()

    from commands.apt import GetPackages

    responses = await execute(inventory, lambda: GetPackages(
        name="get packages"
    ))

    # Test passes if no exceptions were raised
    assert responses is not None
    print(f"Package information: {responses}")


@pytest.mark.asyncio
async def test_apt_package():
    """Test getting apt packages information without errors"""
    inventory = get_inventory()

    from commands.apt import Package

    responses = await execute(inventory, lambda: Package(
        name="package",
        packages=["tree", "vim"],
        present=True,
        group="All",
        sudo=True,
        updata=True
    ))

    # Test passes if no exceptions were raised
    assert responses is not None
    print(f"Package information: {responses}")
