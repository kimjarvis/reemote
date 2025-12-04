import asyncio
from inventory import get_inventory
from execute import execute
import logging
from response import Response

async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="asyncssh_debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    from commands.server import Shell
    responses = await execute(inventory, lambda: Shell(name="echo",
                     cmd="echo Hello World!",
                     group="All",
                     sudo=False))
    # print(f"Responses: {responses}")
    try:
        validated_responses = [
            Response(
                host=r.host,
                name=r.op.name,
                command=r.op.command,
                stdout=r.cp.stdout,
                stderr=r.cp.stderr,
                changed=r.changed,
                return_code=r.cp.returncode,
                error=r.error
            )
            for r in responses
        ]
        print("Validation successful!")
    except ValidationError as e:
        print(f"Validation failed: {e}")
    print(validated_responses)

if __name__ == "__main__":
    asyncio.run(main())