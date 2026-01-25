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
    from reemote.context import Context
    from reemote import core

    responses = await execute(lambda: core.get.Context(), inventory)

    for response in responses:
        assert response.host in ["server104", "server105"]

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

