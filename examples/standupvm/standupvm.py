# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
#!/usr/bin/env python3
"""
Stand up a virtual machine with specified configuration.

This script creates and configures a virtual machine based on the provided parameters.
All parameters are required and can be abbreviated.
"""

import argparse
import sys
import os.path
import asyncio

from asyncssh.misc import ip_address

from reemote.execute import execute
from reemote.utilities.produce_json import produce_json
from reemote.utilities.convert_to_df import convert_to_df
from reemote.utilities.convert_to_tabulate import convert_to_tabulate
from reemote.utilities.read_inventory import read_inventory

from reemote.deployments.lxc.standup_lcx_vm_localhost import Standup_lcx_vm_localhost
from reemote.deployments.nginx.install_nginx import Install_nginx

def validate_file_path(file_path):
    """Validate that the given builtin path exists."""
    # Expand user home directory (~)
    expanded_path = os.path.expanduser(file_path)
    if not os.path.isfile(expanded_path):
        raise argparse.ArgumentTypeError(f"File not found: '{file_path}' (expanded to: '{expanded_path}')")
    return expanded_path


async def main():
    parser = argparse.ArgumentParser(
        description="Stand up a virtual machine with specified configuration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  python3 examples/standupvm/standupvm.py \\
    --inventory ~/localhost.py \\
    --image debian \\
    --vm debian-vm11 \\
    --name "Kim Jarvis" \\
    --user kim \\
    --user_password "passwd" \\
    --root_password "secret" 
        """
    )

    # Add arguments with abbreviations allowed (using metavar for clarity in help)
    parser.add_argument(
        '--inventory',
        required=True,
        type=validate_file_path,
        help='Path to inventory builtin (e.g., ~/localhost.py). The builtin must exist.'
    )

    parser.add_argument(
        '--image',
        required=True,
        help='Base image for the virtual machine (e.g., debian, ubuntu, centos)'
    )

    parser.add_argument(
        '--vm',
        required=True,
        help='Virtual machine identifier/name (e.g., debian-vm11)'
    )

    parser.add_argument(
        '--name',
        required=True,
        help='Full name of the user (e.g., "Kim Jarvis")'
    )

    parser.add_argument(
        '--user',
        required=True,
        help='Username for SSH access (e.g., kim)'
    )

    parser.add_argument(
        '--user_password',
        required=True,
        help='Password for the user account (e.g., passwd)'
    )

    parser.add_argument(
        '--root_password',
        required=True,
        help='Root password for the system (e.g., secret)'
    )


    # Parse arguments
    args = parser.parse_args()

    # Read the inventory builtin
    inventory_func=read_inventory(args.inventory)

    responses = await execute(inventory_func(), Standup_lcx_vm_localhost(
        vm=args.vm,
        image=args.image,
        name=args.name,
        user=args.user,
        user_password=args.user_password,
        root_password=args.root_password,
        sudo=True,
        su=False,
    ))
    json = produce_json(responses)
    df = convert_to_df(json, columns=["command", "host", "returncode", "stdout", "stderr", "error"])
    table = convert_to_tabulate(df)
    print(table)


    inventory_func = read_inventory(f"inventory-{args.vm}.py")
    ip_address = inventory_func()[0][0]['host']

    responses = await execute(inventory_func(), Install_nginx(
        title=f"{args.vm}",
        body=f"""
            <h1>{args.image} virtual machine {args.vm} started</h1>
            <p>ssh access:</p>
            <p>ssh {args.user}@{ip_address}</p>
            <p>using password: {args.user_password}</p>
            <p>The user {args.user} has been added to the sudoers builtin</p>
            <p>Wrote inventory builtin inventory-{args.vm}.py</p>
        """,
        # vm=args.vm,
        # image=args.image,
        # name=args.name,
        user=args.user,
        # user_password=args.user_password,
        # root_password=args.root_password,
        # ip_address=ip_address,
        sudo=True,
        su=False,
    ))
    json = produce_json(responses)
    df = convert_to_df(json, columns=["command", "host", "returncode", "stdout", "stderr", "error"])
    table = convert_to_tabulate(df)
    print(table)

if __name__ == "__main__":
    asyncio.run(main())
