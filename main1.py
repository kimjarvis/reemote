import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from utilities.logging import reemote_logging
from construction_tracker import track_construction, track_yields

@track_construction
class Root:
    @track_yields
    async def execute(self):
        from commands.server import Shell
        r = yield Shell(name="echo",
                     cmd="echo Hello World!",
                     group="all",
                     sudo=False)
        print(f"> {r}")

async def main():
    responses = await execute(lambda: Root())
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
