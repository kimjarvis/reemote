import asyncio
from reemote.execute import execute
from reemote.response import validate_responses


class Hello:
    async def execute(self):
        from reemote.commands.server import Shell
        r = yield Shell(name="echo",
                     cmd="echo Hello Kimbo!",
                     group="all",
                     sudo=False)
        r = yield Shell(name="echo",
                     cmd="echo Hello World!",
                     group="all",
                     sudo=False)
        print(f"> {r}")

class Root:
    async def execute(self):
        r = yield Hello()
        print(f"* {r}")

async def main():
    responses = await execute(lambda: Root())
    validated_responses = await validate_responses(responses)
    print(validated_responses)

    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.value}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
