import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses

async def main():
    inventory = get_inventory()
    from commands.apt import APTRemove
    responses = await execute(inventory, lambda: APTRemove(name="remove tree",
                     packages =["tree","vim"],
                     group="All",
                     sudo=True))
    validated_responses = await validate_responses(responses)
    print(validated_responses)

    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        print(f"Error: {result.error}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")
        print(f"Executed: {result.executed}")

if __name__ == "__main__":
    asyncio.run(main())
