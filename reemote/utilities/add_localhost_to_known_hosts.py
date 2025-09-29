import subprocess
import os


def add_localhost_to_known_hosts():
    # Define the path to the known_hosts file
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

        # Append the output to the known_hosts file
        with open(known_hosts_path, "a") as known_hosts_file:
            known_hosts_file.write(result.stdout)

        print("Successfully added localhost to known_hosts.")

    except subprocess.CalledProcessError as e:
        print(f"Error running ssh-keyscan: {e.stderr}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

