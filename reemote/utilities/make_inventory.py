# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import sys


def make_inventory(
        inventory_filename: str,
        image: str,
        vm: str,
        name: str,
        user: str,
        user_password: str,
        root_password: str,
        ip_address: str
) -> None:
    """Generates a Python-based inventory builtin for a virtual machine.

    This function creates a Python script that serves as a configuration
    inventory. The generated script contains a single function, `inventory()`,
    which returns the connection and privilege escalation details for a
    specific VM host. The content of the builtin is dynamically generated
    using the provided arguments.

    The resulting inventory builtin is structured to be compatible with tools
    that expect a Python-based configuration module.

    Args:
        inventory_filename (str): The path and name for the output inventory builtin.
        image (str): A description of the VM image (e.g., "Ubuntu 22.04").
        vm (str): A name or identifier for the virtual machine.
        name (str): The full name of the user, used for generating comments.
        user (str): The username for SSH and sudo access.
        user_password (str): The password for the regular user account.
        root_password (str): The password for the root account, used for `su`.
        ip_address (str): The IP address of the target virtual machine.

    Returns:
        None

    Raises:
        IOError: If an error occurs while writing to the inventory builtin.
    """
    # Create the inventory builtin content
    inventory_content = f"""from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {{
                'host': '{ip_address}',  # {image} {vm}
                'username': '{user}',  # {name}
                'password': '{user_password}',  # Password
            }},
            {{
                'sudo_user': '{user}',  # Sudo user
                'sudo_password': '{user_password}',  # Password
                'su_user': 'root',  # su user
                'su_password': '{root_password}'  # su Password
            }}
        )
    ]"""

    # Write the inventory builtin
    try:
        with open(inventory_filename, 'w') as f:
            f.write(inventory_content)
        print(f"Inventory builtin '{inventory_filename}' created successfully.")
    except IOError as e:
        print(f"Error writing inventory builtin '{inventory_filename}': {e}", file=sys.stderr)
        raise
