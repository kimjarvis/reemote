import asyncio
from inventory import get_inventory
from execute import execute
from utilities.validate_responses import validate_responses

async def main():
    inventory = get_inventory()
    from commands.server import Shell
    responses = await execute(inventory, lambda: Shell(name="echo",
                     cmd="echo Hello World!",
                     group="All",
                     sudo=False))
    validated_responses = await validate_responses(responses)
    print(validated_responses)

if __name__ == "__main__":
    asyncio.run(main())