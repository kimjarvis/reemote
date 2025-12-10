import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from utilities.logging import reemote_logging

async def main():
    reemote_logging()
    inventory = get_inventory()
    from commands.server import Shell
    responses = await execute(inventory, lambda: Shell(name="echo",
                     cmd="echo Hello World!",
                     group="All",
                     sudo=False))
    validated_responses = await validate_responses(responses)
    print(validated_responses)

    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
