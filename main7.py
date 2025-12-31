import asyncio

from reemote.execute import endpoint_execute


async def main():
    from reemote.api.sftp import Mkdir

    await endpoint_execute(
        lambda: Mkdir(
            name="Make directory fred", path="/home/user/h6", permissions=0o555
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
