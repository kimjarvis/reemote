import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from utilities.logging import reemote_logging
from construction_tracker import track_construction, track_yields, clear_all_results, \
    get_structured_results
from commands.server import Shell


@track_construction
class A:
    @track_yields
    async def execute(self):
        r = yield Shell(name="echo",
                        cmd="echo A1",
                        group="All",
                        sudo=False)
        r = yield Shell(name="echo",
                        cmd="echo A2",
                        group="All",
                        sudo=False)
        print(f"A> {r}")
        print(f"A*> {get_structured_results(self)}")


@track_construction
class B:
    @track_yields
    async def execute(self):
        r = yield Shell(name="echo",
                        cmd="echo B1",
                        group="All",
                        sudo=False)
        r = yield Shell(name="echo",
                        cmd="echo B2!",
                        group="All",
                        sudo=False)
        print(f"B> {r}")
        print(f"B*> {get_structured_results(self)}")


@track_construction
class C:
    @track_yields
    async def execute(self):
        # When we yield a class instance, we need to execute it and collect its results
        a_instance = A()
        r = yield a_instance
        # After A completes, its results are in a nested list

        b_instance = B()
        r = yield b_instance
        # After B completes, its results are in a nested list

        print(f"C> {r}")
        r = yield Shell(name="echo",
                        cmd="echo C!",
                        group="All",
                        sudo=False)
        print(f"C*> {get_structured_results(self)}")

async def main():
    clear_all_results()
    reemote_logging()
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, lambda: C())
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