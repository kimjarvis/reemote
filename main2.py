import asyncio
from reemote.execute import execute
from reemote.response import validate_responses
from reemote.commands.server import Shell


async def main():
    responses = await execute(lambda: Shell(name="echo",
                     cmd="echo Hello World!",
                     group="all",
                     sudo=False))
    validated_responses = await validate_responses(responses)
    print(validated_responses)

    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.value}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
