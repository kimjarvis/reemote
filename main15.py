import asyncio

from commands.server import Shell
from construction_tracker import (
    clear_all_results,
    get_structured_results,
    track_construction,
    track_yields,
)
from execute import execute


@track_construction
class A:
    @track_yields
    async def execute(self):
        r = yield Shell(name="echo",
                        cmd="echo A1")
        print(f"{r}")
        r = yield Shell(name="echo",
                        cmd="echo A2",
                        sudo=False)
        print(f"A> {r}")
        print(f"A*> {get_structured_results(self)}")


@track_construction
class B:
    @track_yields
    async def execute(self):
        r = yield Shell(name="echo",
                        cmd="echo B1",
                        group="all",
                        sudo=False)
        r = yield Shell(name="echo",
                        cmd="echo B2!",
                        group="all",
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
                        group="all",
                        sudo=False)
        print(f"C*> {get_structured_results(self)}")

async def main():
    clear_all_results()
    responses = await execute(lambda: C())
    print(f"R {responses}")

if __name__ == "__main__":
    asyncio.run(main())