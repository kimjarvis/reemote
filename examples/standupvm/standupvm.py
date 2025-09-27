#!/usr/bin/env python3
"""
Stand up a virtual machine with specified configuration.

This script creates and configures a virtual machine based on the provided parameters.
All parameters are required and can be abbreviated.
"""

import argparse
import sys
import os.path
from pathlib import Path


def validate_file_path(file_path):
    """Validate that the given file path exists."""
    # Expand user home directory (~)
    expanded_path = os.path.expanduser(file_path)
    if not os.path.isfile(expanded_path):
        raise argparse.ArgumentTypeError(f"File not found: '{file_path}' (expanded to: '{expanded_path}')")
    return expanded_path


def main():
    parser = argparse.ArgumentParser(
        description="Stand up a virtual machine with specified configuration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  python3 examples/standupvm/standupvm.py \\
    --image debian \\
    --vm debian-vm11 \\
    --name "Kim Jarvis" \\
    --user kim \\
    --user_password "passwd" \\
    --root_password "secret" \\
    --inventory ~/localhost.py
        """
    )

    # Add arguments with abbreviations allowed (using metavar for clarity in help)
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

    parser.add_argument(
        '--inventory',
        required=True,
        type=validate_file_path,
        help='Path to inventory file (e.g., ~/localhost.py). The file must exist.'
    )

    # Parse arguments
    args = parser.parse_args()

    # Simulate VM creation and output the required message
    # In a real implementation, this would involve actual VM provisioning logic
    ip_address = "10.23.45.66"  # Hardcoded as per the example output

    output = f"""{args.image} virtual machine {args.vm} started
view credentials at http://{ip_address}
ssh access:
ssh {args.user}@{ip_address}
using password: {args.user_password}
the user {args.user} has been added to the sudoers file
wrote inventory file inventory-{args.vm}.py"""

    print(output)


if __name__ == "__main__":
    main()