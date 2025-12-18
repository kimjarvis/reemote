import asyncio
from reemote.execute import execute
from reemote.construction_tracker import (
    clear_all_results,
    get_structured_results,
    track_construction,
    track_yields,
)


@track_construction
class Hello:
    @track_yields
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
        print(f"& {get_structured_results(self)}")

@track_construction
class Root:
    @track_yields
    async def execute(self):
        r = yield Hello()
        print(f"* {r}")
        print(f"! {get_structured_results(self)}")

async def main():
    clear_all_results()
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
