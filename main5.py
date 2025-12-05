import asyncio
from inventory import get_inventory
from execute import execute
from utilities.validate_responses import validate_responses


async def main():
    inventory = get_inventory()
    from commands.apt import Remove
    responses = await execute(inventory, lambda: Remove(name="remove tree",
                     packages =["tree","vim"],
                     group="All",
                     sudo=True))
    validated_responses = await validate_responses(responses)
    print(validated_responses)

if __name__ == "__main__":
    asyncio.run(main())