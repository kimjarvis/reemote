import asyncio
from reemote.execute import execute

from reemote.operations.apk.packages import Packages
from reemote.operations.apk.update import Update
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
from typing import List, Tuple, Dict, Any

from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
     return [
        (
            {
                'host': '10.156.135.16',  # alpine
                'username': 'user',  # User name
                'password': 'user'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'root'  # Password
            }
        ),
        (
            {
                'host': '10.156.135.19',  # alpine
                'username': 'user',  # User name
                'password': 'user'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'root'  # Password
            }
        )
    ]

class Install_vim:
    def execute(self):
        r = yield f"echo Installing VIM on Alpine!"
        r.changed = False
        yield Update(sudo=True)
        yield Packages(packages=["vim"], present=True, sudo=True)


async def main():
    results = await execute(inventory(), Install_vim())
    print(produce_table(produce_json(results)))

if __name__ == "__main__":
    asyncio.run(main())
