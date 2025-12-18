import asyncio

from reemote.execute import execute


async def main():
    from reemote.commands.apt import Package
    responses = await execute(lambda: Package(name="apt package tree",
                                                         packages =["tree"],
                                                         present = False,
                                                         group="all",
                                                         sudo=True))
    # validated_responses = await validate_responses(responses)
    # print(validated_responses)
    #
    # # Each response is now a UnifiedResult with all fields available:
    # for result in validated_responses:
    #     print(f"Host: {result.host}")
    #     print(f"Command: {result.command}")
    #     print(f"Output: {result.output}")
    #     print(f"Error: {result.error}")
    #     print(f"Stdout: {result.stdout}")
    #     print(f"Stderr: {result.stderr}")
    #     print(f"Changed: {result.changed}")
    #     print(f"Executed: {result.executed}")
    #
    # # Print the construction hierarchy at the end
    # print("\nConstruction Hierarchy:")
    # ConstructionTracker.print()

if __name__ == "__main__":
    asyncio.run(main())
