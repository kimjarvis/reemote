import subprocess
import re


def extract_dnf_command(playbook_path):
    """
    Runs an Ansible playbook with -vvv verbosity and extracts the dnf command from the output.

    Args:
        playbook_path (str): Path to the Ansible playbook file.

    Returns:
        str: The extracted dnf shell command.
    """
    try:
        # Run the Ansible playbook with -vvv verbosity
        result = subprocess.run(
            ['ansible-playbook', '-i', 'inventory.yaml', '-vvvv', '--check', playbook_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        print(result.stdout)
        # Parse the output to find the dnf command
        for line in result.stdout.splitlines():
            if "SSH: EXEC ssh" in line:
                if 'dnf' in line:
                    return line

        # If no dnf command is found, raise an error
        raise ValueError("No dnf command found in the verbose output.")

    except Exception as e:
        print(f"Error: {e}")
        return None


# Example usage
if __name__ == "__main__":
    playbook_path = "playbook.yaml"  # Replace with the path to your playbook
    dnf_command = extract_dnf_command(playbook_path)
    if dnf_command:
        print(f"Extracted dnf command: {dnf_command}")