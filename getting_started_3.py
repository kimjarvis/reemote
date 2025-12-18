import asyncio
from reemote.execute import execute
from reemote.commands.server import Shell

from reemote.construction_tracker import track_construction, track_yields, get_structured_results

@track_construction
class Message:
    @track_yields
    async def execute(self):
        yield Shell(cmd="echo Brave")
        yield Shell(cmd="echo New")


@track_construction
class Root:
    @track_yields
    async def execute(self):
        response_A = yield Shell(cmd="echo Hello")
        print("A:",response_A.stdout)
        response_B = yield Message()
        print("B:",response_B.stdout)
        response_C = get_structured_results(self)
        for r in response_C:
            print("C:", r.stdout)
        yield Shell(cmd="echo World!")


async def main():
    response_D = await execute(lambda: Root())
    for r in response_D:
        print("D:",r.stdout)


if __name__ == "__main__":
    asyncio.run(main())
