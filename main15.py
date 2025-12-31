import asyncio

from reemote.api.server import Shell
from reemote.execute import endpoint_execute


class A:
    async def execute(self):
        r = yield Shell(name="echo",
                        cmd="echo A1")
        print(f"{r}")
        r = yield Shell(name="echo",
                        cmd="echo A2",
                        sudo=False)
        print(f"A> {r}")


class B:
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


class C:
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

async def main():
    responses = await endpoint_execute(lambda: C())
    print(f"R {responses}")

if __name__ == "__main__":
    asyncio.run(main())