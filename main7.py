import asyncio

from reemote.execute import execute
from reemote.response import validate_responses


async def main():
    from reemote.commands.sftp import Mkdir
    responses = await execute(lambda: Mkdir(name="Make directory fred",
                                                       path="/home/user/h5",
                                                       permissions=0o555))
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
