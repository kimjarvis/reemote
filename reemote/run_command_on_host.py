import asyncssh
from asyncssh import SSHCompletedProcess

from reemote.result import Result


async def run_command_on_host(operation):
    # Define the asynchronous function to connect to a host and run a command
    host_info = operation.host_info
    sudo_info = operation.sudo_info
    command = operation.command
    cp = SSHCompletedProcess()

    try:
        # Connect to the host
        async with asyncssh.connect(**host_info) as conn:
            # Run the command
            # Check if the command starts with 'sudo'
            # print(f"Executing command: {command}")
            if command.startswith("sudo"):
                if not sudo_info.get("sudo_password"):
                    raise ValueError("Command requires sudo, but no sudo password was provided.")

                # Run the command with sudo, providing the password via stdin
                cp = await conn.run(
                    command,
                    input=sudo_info.get("sudo_password") + "\n",
                    # Provide the sudo password followed by a newline
                    check=False  # Do not raise an exception if the command fails
                )
            elif command.startswith("su"):
                if not sudo_info.get("su_password"):
                    raise ValueError("Command requires su, but no su password was provided.")

                # Run the command with sudo, providing the password via stdin
                cp = await conn.run(
                    command,
                    input=sudo_info.get("su_password") + "\n",
                    # Provide the sudo password followed by a newline
                    check=False  # Do not raise an exception if the command fails
                )


            else:
                cp = await conn.run(command, check=False)

    except (OSError, asyncssh.Error) as e:
        return f"Connection failed on host {host_info.get("host")}: {str(e)}"

    # print(f"Output: {cp.stdout}")
    return Result(cp=cp, host=host_info.get("host"))
