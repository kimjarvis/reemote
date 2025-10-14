# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
from typing import List, Tuple, Dict, Any


async def verify_inventory_connect(inventory: List[Tuple[Dict[str, Any], Dict[str, str]]]) -> bool:
    """Asynchronously verifies SSH connectivity for a list of hosts.

    This function iterates through an inventory of hosts and attempts to
    establish an SSH connection to each one using the `asyncssh` library.
    For every host, it uses a dictionary of connection parameters to
    initiate the connection.

    A simple `echo x` command is executed on each successful connection to
    confirm that the session is active and capable of running commands.
    If any connection fails, an error is printed to the console, and the
    function immediately returns `False`, stopping further verification.

    Args:
        inventory (List[Tuple[Dict[str, Any], Dict[str, str]]]):
            A list of tuples, where each tuple represents a host to be
            verified. Each tuple contains:

            - host_info (dict): A dictionary of keyword arguments to be
              passed to `asyncssh.connect`. Common keys include `host`,
              `username`, `port`, `password`, and `client_keys`.
            - ssh_info (dict): A dictionary containing other SSH-related
              metadata (currently unused by this function).

    Returns:
        bool: `True` if connections to all hosts in the inventory are
              successful, and `False` if any connection attempt fails.
    """
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
