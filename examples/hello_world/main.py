import asyncio
from reemote.run import run
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table

from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [({'host': 'localhost',
              'username': 'kim',  # User name
              'password': 'xnjs'  # Password
              },{})]

class Hello_world:
    def execute(self):
        r = yield "echo hello world"
        r.changed = False

async def main():
    _, responses = await run(inventory(), Hello_world())
    print(produce_table(produce_json(responses)))


if __name__ == "__main__":
    asyncio.run(main())