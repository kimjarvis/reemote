import asyncio
from inventory import get_inventory
from execute import execute
from response import Response, validate_responses, PackageInfo


async def main():
    inventory = get_inventory()
    from commands.apt import GetPackages
    responses = await execute(inventory, lambda: GetPackages(name="get packages"))
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
        # Access package info
        for item in result.output:
            if isinstance(item, PackageInfo):
                print(f"{item.name}: {item.version}")
            elif isinstance(item, dict):
                print(f"{item.get('name')}: {item.get('version')}")

if __name__ == "__main__":
    asyncio.run(main())
