import asyncssh
from asyncssh import SSHCompletedProcess
from reemote.result import Result


async def run_command_on_host(operation):
    # Define the asynchronous function to connect to a host and run a command
    host_info = operation.host_info
    sudo_info = operation.sudo_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False
    try:
        # Connect to the host
        async with asyncssh.connect(**host_info) as conn:
            if command.startswith("composite"):
                # print(f"Executing composite command: {command}")
                pass
            else:
                if not operation.guard:
                    pass
                else:
                    # print(f"Executing command: {command}")
                    executed = True
                    if command.startswith("sudo"):
                        if not sudo_info.get("sudo_password"):
                            raise ValueError("Command requires sudo, but no sudo password was provided.")

                        # Construct the full command for sudo
                        full_command = f'echo "{sudo_info["sudo_password"]}" | sudo -S {command[len("sudo "):]}'

                        # Run the command
                        cp = await conn.run(full_command, check=False)
                    elif command.startswith("su"):
                        full_command = f'su {sudo_info["su_user"]} -c {command.replace("su", "", 1)}'
                        async with conn.create_process(full_command,
                        # async with conn.create_process(f'su {sudo_info["su_user"]} -c "ls -ld /tmp/mydir"',
                                                       term_type='xterm',
                                                       stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                       stderr=asyncssh.PIPE) as process:
                            # Wait for the password prompt and send the password
                            output = await process.stdout.readuntil('Password:')
                            process.stdin.write(f'{sudo_info["su_password"]}\n')  # Provide the su password
                            # Read the remaining output and check for errors
                            stdout, stderr = await process.communicate()

                            # if process.exit_status != 0:
                            #     print(stderr, end='', file=sys.stderr)
                            #     print(f'Process exited with status {process.exit_status}', file=sys.stderr)
                            # else:
                            #     print(stdout, end='')

                        cp = SSHCompletedProcess(
                            command=full_command ,  # Command executed
                            exit_status=process.exit_status,                     # Exit status
                            returncode=process.returncode,                       # Return code
                            stdout=stdout,                                       # Standard output
                            stderr=stderr                                        # Standard error
                        )
                    else:
                        cp = await conn.run(command, check=False)

    except asyncssh.ProcessError as exc:
        return f"Process on host {host_info.get("host")} exited with status {exc.exit_status}"
    except (OSError, asyncssh.Error) as e:
        return f"Connection failed on host {host_info.get("host")}: {str(e)}"

    # print(f"Output: {cp.stdout}")
    return Result(cp=cp, host=host_info.get("host"), op=operation, executed=executed)
