import asyncio
from reemote.report import report
from reemote.run import run

from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [({'host': 'localhost',
              'username': 'youruser',  # User name
              'password': 'yourpassword'  # Password
              },{})]

class Hello_world:
    def execute(self):
        r = yield "echo hello world"
        r.changed = False

async def main():
    operations, responses = await run(inventory(), Hello_world())
    print(report(operations, responses))


if __name__ == "__main__":
    asyncio.run(main())