import asyncio
from reemote.report import report
from reemote.run import run

from reemote.operations.apk.packages import Packages
from reemote.operations.apk.update import Update

from typing import List, Tuple, Dict, Any


def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [({'host': '192.168.122.47',
              'username': 'youruser',  # User name
              'password': 'yourpassword'  # Password
              },{
              'su_password': 'youruser'})]

class Install_vim:
    def execute(self):
        r = yield "echo Installing VIM on Alpine!"
        r.changed = False
        yield Update(su=True)
        yield Packages(packages=["vim"], present=True, su=True)


async def main():
    operations, responses = await run(inventory(), Install_vim())
    print(report(operations, responses))


if __name__ == "__main__":
    asyncio.run(main())
