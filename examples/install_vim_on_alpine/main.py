import asyncio
from reemote.run import run

from reemote.operations.apk.packages import Packages
from reemote.operations.apk.update import Update
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
from typing import List, Tuple, Dict, Any


def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [({'host': '192.168.122.47',
              'username': 'kim',  # User name
              'password': 'xnjs'  # Password
              }, {
                 'su_user': 'kim',
                 'su_password': 'xnjs'})]



class Install_vim:
    def execute(self):
        r = yield f"echo Installing VIM on Alpine!"
        r.changed = False
        yield Update(sudo=True)
        yield Packages(packages=["vim"], present=True, sudo=True)


async def main():
    _, results = await run(inventory(), Install_vim())
    print(produce_table(produce_json(results)))

if __name__ == "__main__":
    asyncio.run(main())
