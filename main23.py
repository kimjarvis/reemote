from reemote.operations.sftp import Directory

import asyncio
from reemote.execute import execute
from reemote.commands.server import Shell
from reemote.inventory import add_entry, get_entry, delete_entry, create_inventory, read_inventory



class Child:
    async def execute(self):
        print("debut 00")
        # r = yield Shell(name="echo",
        #                 cmd="echo A1")
        # yield Mkdir(path="/home/user/freddy6666")
        print("debut 01")

class Parent:
    async def execute(self):
        print("Starting test")
        response = yield Child()
        print(response)


async def main():
    create_inventory([
      [
        {
          "host": "192.168.1.76",
          "username": "user",
          "password": "password"
        },
        {
          "groups": [
            "all",
            "local"
          ],
          "sudo_user": "user",
          "sudo_password": "password"
        }
      ],
      [
        {
          "host": "192.168.1.24",
          "username": "user",
          "password": "password"
        },
        {
          "groups": [
            "all",
            "local"
          ],
          "sudo_user": "user",
          "sudo_password": "password"
        }
      ]
    ])
    await execute(lambda: Parent())

if __name__ == "__main__":
    asyncio.run(main())

