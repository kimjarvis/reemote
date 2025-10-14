# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncio
from reemote.execute import execute
from reemote.operations.apk.packages import Operation_packages
from reemote.operations.apk.update import Update
from reemote.utilities.produce_json import produce_json
from reemote.utilities.convert_to_df import convert_to_df
from reemote.utilities.convert_to_tabulate import convert_to_tabulate

from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {
                'host': '192.168.1.56',  # images:debian/13 debian-0
                'username': 'kim',  # Kim Jarvis
                'password': 'kim',  # Password
            },
            {
                'sudo_user': 'kim',  # Sudo user
                'sudo_password': 'kim',  # Password
            }
        )
    ]


class Install_vim:
    def execute(self):
        yield Update(sudo=True)
        yield Operation_packages(packages=["vim", "nano"], present=True, sudo=True)


async def main():
    responses = await execute(inventory(), Install_vim())
    json = produce_json(responses)
    df = convert_to_df(json, columns=["command", "host", "guard", "executed", "changed"])
    table = convert_to_tabulate(df)
    # print(table)
    df = convert_to_df(json, columns=["command", "host", "returncode", "stdout", "stderr", "error"])
    table = convert_to_tabulate(df)
    # print(table)

if __name__ == "__main__":
    asyncio.run(main())
