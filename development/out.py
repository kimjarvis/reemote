import asyncio
import asyncssh

# Define a class to hold the result of the SSH command execution
class SSHCompletedProcess:
    def __init__(self):
        self.stdout = ""
        self.stderr = ""
        self.exit_status = None

# Define the asynchronous function to connect to a host and run a command
async def run_command_on_host(operation):
    host_info = operation.host_info
    sudo_info = operation.sudo_info
    command = operation.command
    cp = SSHCompletedProcess()

    try:
        # Connect to the host
        async with asyncssh.connect(**host_info) as conn:
            # Check if the command starts with 'sudo'
            if command.startswith("sudo"):
                if not sudo_info.get("sudo_password"):
                    raise ValueError("Command requires sudo, but no sudo password was provided.")

                # Construct the full command for sudo
                full_command = f'echo "{sudo_info["sudo_password"]}" | sudo -S {command[len("sudo "):]}'

                # Run the command
                result = await conn.run(full_command, check=False)
                cp.stdout = result.stdout
                cp.stderr = result.stderr
                cp.exit_status = result.exit_status

            elif command.startswith("su"):
                if not sudo_info.get("su_password"):
                    raise ValueError("Command requires su, but no su password was provided.")

                # Construct the full command for su
                full_command = f'echo "{sudo_info["su_password"]}" | su -c "{command[len("su "):]}"'

                # Run the command
                result = await conn.run(full_command, check=False)
                cp.stdout = result.stdout
                cp.stderr = result.stderr
                cp.exit_status = result.exit_status

            else:
                # Run the command as-is
                result = await conn.run(command, check=False)
                cp.stdout = result.stdout
                cp.stderr = result.stderr
                cp.exit_status = result.exit_status

    except Exception as e:
        cp.stderr = str(e)
        cp.exit_status = 1

    return cp

# Example usage
class Operation:
    def __init__(self, host_info, sudo_info, command):
        self.host_info = host_info
        self.sudo_info = sudo_info
        self.command = command

async def main():
    # Define connection parameters
    host_info = {
        "host": "your_host",
        "username": "your_username",
        "password": "your_password",
    }
    sudo_info = {
        "sudo_password": "your_sudo_password",  # Password for sudo
        "su_password": "your_su_password",      # Password for su
    }

    # Example commands
    commands = [
        "sudo apk add vim",  # Command requiring sudo
        "su -c apk add curl",  # Command requiring su
        "echo Hello World",  # Regular command
    ]

    for cmd in commands:
        operation = Operation(host_info, sudo_info, cmd)
        result = await run_command_on_host(operation)

        print(f"Command: {cmd}")
        print(f"Exit Status: {result.exit_status}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        print("-" * 40)

# Run the main function
asyncio.run(main())