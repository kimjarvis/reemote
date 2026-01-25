# block insert examples/example_prefix.txt
import asyncio
import os
import sys

from scripts.code_generation.execute_example import execute_example
from scripts.code_generation.generate_example import generate_example
from scripts.code_generation.generate_responses import generate_responses
from scripts.code_generation.generate_test import generate_test

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# block end

async def example(inventory):
    from reemote.execute import execute
    from reemote import apt1

    responses = await execute(lambda: apt1.get.Packages(), inventory)

    assert any(
        response.value[i].name == "adduser"
        for response in responses
        for i in range(len(response.value))
    ), "Expected the coroutine to return a list of packages containing adduser"

    return responses


# block insert examples/example_suffix.txt
async def main():
    responses = await execute_example(example)
    generate_example(example, file=__file__)
    generate_test(example, file=__file__)
    generate_responses(responses, file=__file__)


if __name__ == "__main__":
    asyncio.run(main())
# block end

