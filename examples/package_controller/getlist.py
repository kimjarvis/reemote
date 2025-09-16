import asyncio
from reemote.run import run
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
from reemote.operations.server.shell import Shell

from typing import List, Tuple, Dict, Any

from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {
                'host': '192.168.122.143',  # debian
                'username': 'kim',  # User name
                'password': 'userpassword'  # Password
            },
            {
                'sudo_user': 'kim',
                'sudo_password': 'userpassword',
            }
        )
    ]

class Hello_world:
    def execute(self):
        r = yield Shell("dpkg-query -l")
        print(r.cp.stdout)

async def main():
    _, responses = await run(inventory(), Hello_world())
    print(produce_table(produce_json(responses)))


if __name__ == "__main__":
    asyncio.run(main())