import pytest

from reemote.execute import execute
from reemote.inventory import get_inventory
from reemote.response import validate_responses


@pytest.mark.asyncio
async def test_server_shell():
    """Test basic apt install command execution without errors"""

    # Execute the test
    from reemote.commands.server import Shell
    responses = await execute(lambda: Shell(
        name="echo",
        cmd="echo Hello World!",
        group="all",
        sudo=False
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
            from reemote.commands.server import Shell
            r = yield Shell(
                name="echo",
                cmd="echo Hello World!",
                group="all",
                sudo=False
            )

    # Execute the test
    responses = await execute(lambda: Root())
    validated_responses = await validate_responses(responses)

    # Test passes if no exceptions were raised
    assert validated_responses is not None

@pytest.mark.asyncio
async def test_server_shell_double_nested():
    """Test server shell command execution without errors"""

    # Define the classes locally within the test
    class Hello:
        async def execute(self):
            from reemote.commands.server import Shell
            r = yield Shell(
                name="echo",
                cmd="echo Hello World!",
                group="all",
                sudo=False
            )

    class Root:
        async def execute(self):
            yield Hello()

    # Execute the test
    responses = await execute(lambda: Root())
    validated_responses = await validate_responses(responses)

    # Test passes if no exceptions were raised
    assert validated_responses is not None
    # Optional: Add more assertions if you want to verify specific responses
