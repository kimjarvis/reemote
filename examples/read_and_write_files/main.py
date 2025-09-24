import asyncio
from reemote.operations.sftp.read_file import Read_file
from reemote.operations.sftp.write_file import Write_file
from reemote.execute import execute
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
from reemote.operation import Operation
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

async def main():
    responses = await execute(inventory(), Hello())
    print(produce_table(produce_json(responses)))


class Hello:
    def execute(self):
        r = yield Operation("Read and Write", composite=True)
        r.changed = False
        r1 = yield Read_file(path='example.txt', hosts=inventory()[0][0]['host'])
        # print(">>",r1)
        r2 = yield Write_file(path='example.txt', text='hello world')
        # print(">>",r2)

if __name__ == "__main__":
    asyncio.run(main())