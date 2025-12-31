import asyncio

from reemote.execute import endpoint_execute
from reemote.api.apt import Update


async def main():
    await endpoint_execute(lambda: Update(name="apt package tree", sudo=True))


if __name__ == "__main__":
    asyncio.run(main())
