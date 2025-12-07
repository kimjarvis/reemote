# main.py
import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from construction_tracker import ConstructionTracker, track_construction, with_parent_context


# Final working solution - use this decorator AND modify execute.py

def track_yields(method):
    """
    Decorator that wraps async generator to track parent for ALL yields
    """

    async def wrapper(self, *args, **kwargs):
        parent_id = getattr(self, '_construction_id', None)

        # Store the original parent to restore later
        original_parent = ConstructionTracker.get_current_parent()

        # Always set our ID as parent when this generator runs
        ConstructionTracker.set_parent(parent_id)

        try:
            gen = method(self, *args, **kwargs)
            try:
                while True:
                    value = await gen.__anext__()
                    result = yield value
                    await gen.asend(result)
            except StopAsyncIteration:
                pass
            finally:
                await gen.aclose()
        finally:
            # Restore original parent
            ConstructionTracker.set_parent(original_parent)

    import types
    return types.coroutine(wrapper)


# Use it like this:
@track_construction
class Hello:
    @track_yields
    async def execute(self):
        from commands.server import Shell

        r = yield Shell(name="echo",
                        cmd="echo Hello World!",
                        group="All",
                        sudo=False)

        r = yield Shell(cmd="pwd")


@track_construction
class Root:
    @track_yields
    async def execute(self):
        yield Hello()

async def main():
    # Clear construction tracker at the beginning
    ConstructionTracker.clear()

    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, lambda: Root())
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

        # Print the construction hierarchy at the end
    print("\nConstruction Hierarchy:")
    ConstructionTracker.print()


if __name__ == "__main__":
    asyncio.run(main())