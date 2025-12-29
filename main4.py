import asyncio
from reemote.execute import endpoint_execute
from reemote.response import validate_responses, PackageInfo
from reemote.facts.apt import GetPackages

async def main():
    responses = await endpoint_execute(lambda: GetPackages(name="get packages"))
    print(responses)
    # Each response is now a UnifiedResult with all fields available:
    for result in responses:
        # Access package info
        for item in result["value"]:
            if isinstance(item, PackageInfo):
                print(f"{item.name}: {item.version}")
            elif isinstance(item, dict):
                print(f"{item.get('name')}: {item.get('version')}")

if __name__ == "__main__":
    asyncio.run(main())
