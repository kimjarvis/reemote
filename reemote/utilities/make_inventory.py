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
    """
    Create an inventory file for VM configuration.

    Args:
        inventory_filename: Path to the inventory file
        image: VM image description
        name: User's full name
        user: Username
        user_password: User password
        root_password: Root password
        ip_address: IP address of the VM
    """
    # Create the inventory file content
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

    # Write the inventory file
    try:
        with open(inventory_filename, 'w') as f:
            f.write(inventory_content)
        print(f"Inventory file '{inventory_filename}' created successfully.")
    except IOError as e:
        print(f"Error writing inventory file '{inventory_filename}': {e}", file=sys.stderr)
        raise
