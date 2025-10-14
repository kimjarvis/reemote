# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
import subprocess
import argparse
import sys
import logging
from pathlib import Path
from reemote.utilities.verify_python_file import verify_python_file
from reemote.utilities.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.utilities.validate_inventory_structure import validate_inventory_structure

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def detect_terminal():
    """Detect available terminal emulator."""
    terminals = [
        ('konsole', ['konsole', '--new-tab', '-e']),
        ('gnome-terminal', ['gnome-terminal', '--']),
        ('xfce4-terminal', ['xfce4-terminal', '-e']),
        ('xterm', ['xterm', '-e']),
    ]
    
    for name, cmd in terminals:
        try:
            subprocess.run([cmd[0], '--version'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         check=False)
            return name, cmd
        except FileNotFoundError:
            continue
    
    return None, None


async def test_ssh_connection(host_info):
    """Test SSH connection before opening terminal."""
    try:
        logger.info(f"Testing SSH connection to {host_info.get('host', 'unknown host')}...")
        async with asyncssh.connect(**host_info) as conn:
            result = await conn.run('echo "Connection successful"', check=True)
            logger.info(f"Connection test successful: {result.stdout.strip()}")
            return True
    except asyncssh.Error as e:
        logger.error(f"SSH connection failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during connection test: {e}")
        return False


def extract_ssh_info(host_info):
    """Extract SSH connection information from host_info dictionary."""
    host = host_info.get('host')
    username = host_info.get('username')
    port = host_info.get('port', 22)
    
    if not host:
        raise ValueError("Host information missing from inventory")
    if not username:
        raise ValueError("Username missing from inventory")
    
    return host, username, port


def open_terminal(terminal_name, terminal_cmd, ssh_command):
    """Open a new terminal with the SSH command."""
    try:
        if terminal_name == 'konsole':
            cmd = terminal_cmd + ['bash', '-c', f"{ssh_command}; exec bash"]
        elif terminal_name == 'gnome-terminal':
            cmd = terminal_cmd + ['bash', '-c', f"{ssh_command}; exec bash"]
        else:
            cmd = terminal_cmd + ['bash', '-c', f"{ssh_command}; exec bash"]
        
        logger.info(f"Opening {terminal_name} with SSH connection...")
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error launching {terminal_name}: {e}")
        return False


async def open_ssh_console():
    """
    Main function to open SSH console with inventory verification.

    This function handles the complete workflow for establishing an SSH terminal connection:
    1. Parses command-line arguments for inventory file, host index, and test options
    2. Validates the inventory file and its structure
    3. Selects the specified host from the inventory
    4. Tests the SSH connection (unless skipped with --no-test)
    5. Detects available terminal emulator
    6. Opens a terminal with SSH connection to the selected host

    Command-line Arguments:
        -i/--inventory (required): Path to the inventory Python file (.py extension required)
        -n/--host-number (optional): Index of host to connect to (default: 0)
        --no-test (optional): Skip SSH connection test before opening terminal

    The inventory file should contain a callable that returns a list of host information
    dictionaries with at least 'host' and 'username' keys.

    Supported terminal emulators (auto-detected):
        - konsole (KDE)
        - gnome-terminal (GNOME)
        - xfce4-terminal (XFCE)
        - xterm (generic)

    Returns:
        None: Opens a terminal window with SSH connection or exits with error code
    """
    parser = argparse.ArgumentParser(
        description="Open an SSH terminal connection using an inventory builtin.",
        usage="usage: terminal.py [-h] -i INVENTORY_FILE [-n HOST_NUMBER] [--no-test]",
        epilog="""
Examples: 
  python3 terminal.py -i ~/reemote/inventory-proxmox-arch.py 
  python3 terminal.py -i inventory.py -n 2
  python3 terminal.py -i inventory.py --no-test
        """,
        formatter_class=argparse.RawTextHelpFormatter,
        allow_abbrev=False
    )

    parser.add_argument(
        "-i", "--inventory",
        required=True,
        dest="inventory",
        help="Path to the inventory Python builtin (.py extension required)"
    )
    
    parser.add_argument(
        "-n", "--host-number",
        type=int,
        default=0,
        dest="host_number",
        help="Index of the host to connect to (default: 0)"
    )
    
    parser.add_argument(
        "--no-test",
        action="store_true",
        dest="no_test",
        help="Skip SSH connection test before opening terminal"
    )

    args = parser.parse_args()

    # Verify inventory builtin exists and is a Python builtin
    logger.info(f"Verifying inventory builtin: {args.inventory}")
    if not verify_python_file(args.inventory):
        logger.error("Invalid inventory builtin")
        sys.exit(1)

    # Load and validate inventory
    logger.info("Loading inventory...")
    inventory = validate_inventory_file_and_get_inventory(args.inventory)
    if not inventory:
        logger.error("Failed to load inventory")
        sys.exit(1)

    # Validate inventory structure
    logger.info("Validating inventory structure...")
    inventory_data = inventory()
    if not validate_inventory_structure(inventory_data):
        logger.error("Inventory structure is invalid")
        sys.exit(1)

    # Check if inventory has hosts
    if not inventory_data or not inventory_data[0]:
        logger.error("Inventory is empty or has no hosts")
        sys.exit(1)

    # Select host from inventory
    try:
        host_info = inventory_data[0][args.host_number]
        logger.info(f"Selected host #{args.host_number} from inventory")
    except IndexError:
        logger.error(f"Host index {args.host_number} not found in inventory")
        logger.info(f"Available hosts: 0 to {len(inventory_data[0]) - 1}")
        sys.exit(1)

    # Extract SSH connection information
    try:
        host, username, port = extract_ssh_info(host_info)
    except ValueError as e:
        logger.error(f"Invalid host information: {e}")
        sys.exit(1)

    # Test SSH connection (unless --no-test flag is set)
    if not args.no_test:
        if not await test_ssh_connection(host_info):
            logger.error("SSH connection test failed. Use --no-test to skip this check.")
            sys.exit(1)

    # Detect available terminal
    terminal_name, terminal_cmd = detect_terminal()
    if not terminal_name:
        logger.error("No supported terminal emulator found")
        logger.info("Supported terminals: konsole, gnome-terminal, xfce4-terminal, xterm")
        sys.exit(1)

    logger.info(f"Detected terminal: {terminal_name}")

    # Construct SSH command
    if port != 22:
        ssh_command = f"ssh -p {port} {username}@{host}"
    else:
        ssh_command = f"ssh {username}@{host}"

    # Open terminal with SSH connection
    if not open_terminal(terminal_name, terminal_cmd, ssh_command):
        sys.exit(1)

    logger.info("Terminal opened successfully")

# Run the async function
if __name__ == "__main__":
    import asyncio
    asyncio.run(open_ssh_console())