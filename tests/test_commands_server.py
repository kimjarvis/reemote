import pytest

from execute import execute
from inventory import get_inventory
from unifiedresult import validate_responses


@pytest.mark.asyncio
async def test_server_shell():
    """Test basic apt install command execution without errors"""

    # Execute the test
    inventory = get_inventory()
    from commands.apt import Install
    responses = await execute(inventory, lambda: Install(
        name="install tree",
        packages=["tree", "vim"],
        group="All",
        sudo=True
    ))
    validated_responses = await validate_responses(responses)

    # Test passes if no exceptions were raised
    assert validated_responses is not None

@pytest.mark.asyncio
async def test_server_shell_nested():
    """Test nested server shell command execution without errors"""

    # Define the Root class locally within the test
    class Root:
        async def execute(self):
            from commands.server import Shell
            r = yield Shell(
                name="echo",
                cmd="echo Hello World!",
                group="All",
                sudo=False
            )

    # Execute the test
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
    validated_responses = await validate_responses(responses)

    # Test passes if no exceptions were raised
    assert validated_responses is not None

@pytest.mark.asyncio
async def test_server_shell_double_nested():
    """Test server shell command execution without errors"""

    # Define the classes locally within the test
    class Hello:
        async def execute(self):
            from commands.server import Shell
            r = yield Shell(
                name="echo",
                cmd="echo Hello World!",
                group="All",
                sudo=False
            )

    class Root:
        async def execute(self):
            yield Hello()

    # Execute the test
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
    validated_responses = await validate_responses(responses)

    # Test passes if no exceptions were raised
    assert validated_responses is not None
    # Optional: Add more assertions if you want to verify specific responses