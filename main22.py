import asyncio
from reemote.commands.inventory import create_inventory, read_inventory


async def main():
    # add_entry(
    #     [
    #         {"host": "192.168.1.24", "username": "user", "password": "password"},
    #         {
    #             "groups": ["all", "local"],
    #             "sudo_user": "user",
    #             "sudo_password": "password",
    #         },
    #     ]
    # )
    # print(get_entry(host="192.168.1.24"))
    # delete_entry(host="192.168.1.24")
    print(read_inventory())
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
    # await execute(lambda: Shell(name="test", sudo=True, cmd="ls /root"))


if __name__ == "__main__":
    asyncio.run(main())