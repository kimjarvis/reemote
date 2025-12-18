import asyncio
from reemote.inventory import get_inventory
from reemote.execute import execute
from reemote.response import validate_responses, PackageInfo
from reemote.commands.apt import GetPackages

async def main():
    responses = await execute(lambda: GetPackages(name="get packages"))
    validated_responses = await validate_responses(responses)
    print(validated_responses)

    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
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
