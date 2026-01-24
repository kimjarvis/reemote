# examples/sftp/IsDir.py
import asyncio
import os
import sys

from scripts.execute_example import execute_example
from scripts.generate_example import generate_example
from scripts.generate_responses import generate_responses
from scripts.generate_test import generate_test

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def example(inventory):
    from reemote.execute import execute
    from reemote import sftp1

    responses = await execute(lambda: sftp1.get.IsDir(path="."), inventory)
    for item in responses:
        assert item.value, (
            "Expected the coroutine to report that the current working directory exists on all hosts."
        )

    return responses


async def main():
    responses = await execute_example(example)
    generate_example(example, file=__file__)
    generate_test(example, file=__file__)
    generate_responses(responses, file=__file__)


if __name__ == "__main__":
    asyncio.run(main())
