import asyncio
from reemote.operations.filesystem.get_file import Get_file
from reemote.operations.filesystem.put_file import Put_file
from reemote.run import run
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
from reemote.operation import Operation
from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
     return [
        # (
        #     {
        #         'host': '192.168.122.24',  # alpine
        #         'username': 'youruser',  # User name
        #         'password': 'yourpassword'  # Password
        #     },
        #     {
        #         'su_user': 'root',
        #         'su_password': 'rootpassword'  # Password
        #     }
        # ),
        (
            {
                'host': '192.168.122.47',  # alpine
                'username': 'youruser',  # User name
                'password': 'yourpassword'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'rootpassword'  # Password
            }
        )
    ]

async def main():
    _, responses = await run(inventory(), Hello())
    print(produce_table(produce_json(responses)))


class Hello:
    def execute(self):
        r = yield Operation("echo hello1")
        r.changed = False
        r1 = yield Get_file(path='example.txt', host=inventory()[0][0]['host'])
        # print(">>",r1)
        r2 = yield Put_file(path='example.txt', text='hello world')
        # print(">>",r2)

if __name__ == "__main__":
    asyncio.run(main())