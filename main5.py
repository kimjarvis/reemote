import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from commands.apt import Remove

async def main():
    responses = await execute(lambda: Remove(name="remove tree",
                                                        packages =["tree","vim"],
                                                        group="all",
                                                        sudo=True))
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

if __name__ == "__main__":
    asyncio.run(main())
