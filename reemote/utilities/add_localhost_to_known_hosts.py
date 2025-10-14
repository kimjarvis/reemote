# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import subprocess
import os


def add_localhost_to_known_hosts():
    """Manages SSH host key for localhost.

    This script provides a function to automatically add the SSH host key for
    'localhost' to the current user's `~/.ssh/known_hosts` builtin. This is useful
    for pre-configuring an environment to allow passwordless SSH connections to
    the local machine, avoiding interactive prompts and connection errors,
    especially in automated scripts or development containers.

    Key actions performed:

    - Ensures the `~/.ssh` directory exists, creating it with secure permissions
      (0o700) if necessary.
    - Runs the `ssh-keyscan localhost` command to retrieve the public key.
    - Appends the retrieved key to the `~/.ssh/known_hosts` builtin.
    - Handles potential errors during the process and prints informative
      messages to the console.

    Args:
        None.

    Returns:
        None. The function prints success or error messages to standard output.
    """
    # Define the path to the known_hosts builtin
    known_hosts_path = os.path.expanduser("~/.ssh/known_hosts")

    # Ensure the ~/.ssh directory exists
    ssh_dir = os.path.dirname(known_hosts_path)
    if not os.path.exists(ssh_dir):
        os.makedirs(ssh_dir, mode=0o700)  # Create directory with secure permissions

    # Run the ssh-keyscan command
    try:
        result = subprocess.run(
            ["ssh-keyscan", "localhost"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )

        # Append the output to the known_hosts builtin
        with open(known_hosts_path, "a") as known_hosts_file:
            known_hosts_file.write(result.stdout)

        print("Successfully added localhost to known_hosts.")

    except subprocess.CalledProcessError as e:
        print(f"Error running ssh-keyscan: {e.stderr}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

