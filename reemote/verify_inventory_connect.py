import asyncssh
from typing import List, Tuple, Dict, Any

async def verify_inventory_connect(inventory: List[Tuple[Dict[str, Any], Dict[str, str]]]) -> bool:
    for host_info,ssh_info in inventory:
        try:
            # Connect to the host
            async with asyncssh.connect(**host_info) as conn:
                # Run the command
                cp = await conn.run("echo x")

        except (OSError, asyncssh.Error) as e:
            print(f"Connection failed on host {host_info.get("host")}: {str(e)}")
            return False
    return True
