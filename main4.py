import asyncio
from inventory import get_inventory
from execute import execute
from utilities.validate_responses import validate_responses


async def main():
    inventory = get_inventory()
    from facts.apt import Get_packages
    responses = await execute(inventory, lambda: Get_packages(name="get packages"))
    print(responses)


if __name__ == "__main__":
    asyncio.run(main())