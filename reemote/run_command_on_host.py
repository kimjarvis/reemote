import asyncssh
import asyncio
from asyncssh import SSHCompletedProcess
from reemote.result import Result


async def run_command_on_host(operation):
    # print("running operation", operation)
    # Define the asynchronous function to connect to a host and run a command
    host_info = operation.host_info
    sudo_info = operation.sudo_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False
    try:
        # Connect to the host
        async with asyncssh.connect(**host_info, term_type='vt100') as conn:
            if command.startswith("composite"):
                # print(f"Executing composite command: {command}")
                pass
            else:
                if not operation.guard:
                    pass
                else:
                    # print(f"Executing command: {command}")
                    executed = True
                    if operation.sudo:

                        sudo_user = sudo_info.get("sudo_user", "root")
                        password = sudo_info.get("sudo_password", "")

                        full_command = f"sudo -S -u {sudo_user} {command}"

                        try:
                            if password:
                                cp = await conn.run(
                                    full_command,
                                    input=password + "\n",
                                    check=False
                                )
                            else:
                                cp = await conn.run(
                                    full_command,
                                    check=False
                                )

                            # Check for sudo-specific errors
                            if cp.stderr:
                                if "sudo: no tty present" in cp.stderr:
                                    # Retry with pseudo-TTY
                                    cp = await conn.run(
                                        full_command,
                                        input=password + "\n" if password else None,
                                        check=False,
                                        term_type='vt100',  # Force pseudo-TTY
                                        term_size=(80, 24)
                                    )
                                elif "Sorry, try again" in cp.stderr:
                                    raise RuntimeError("Incorrect sudo password.")
                                elif "password required" in cp.stderr.lower():
                                    raise RuntimeError("Sudo password required but not provided.")

                            if cp.exit_status != 0:
                                raise RuntimeError(f"Command failed with exit status {cp.exit_status}: {cp.stderr}")

                            return cp

                        except Exception as e:
                            print(f"Command failed with error: {e}")
                            return None

                        # sudo_user = sudo_info.get("sudo_user", "root")
                        # password = sudo_info.get("sudo_password", "")
                        #
                        # # Use asyncssh's built-in sudo functionality
                        # async with conn.create_process() as process:
                        #     await process.stdin.write(f"sudo -u {sudo_user} {command}\n")
                        #
                        #     # Handle password prompt if needed
                        #     output = await process.stdout.readuntil('password')
                        #     if output and password:
                        #         await process.stdin.write(password + "\n")
                        #
                        #     # Read the rest of the output
                        #     result = await process.stdout.read()
                        #     print(result)
                        #     return result

                        # # Construct the full sudo command
                        # # full_command = f"sudo -S -u {sudo_info['sudo_user']} {command}"
                        # full_command = f"sudo -S -u {sudo_info['sudo_user']} {command}"
                        # print(full_command)
                        #
                        # # Prepare the password input (if required)
                        # password_input = sudo_info.get("sudo_password", "") + "\n"
                        # print(password_input)
                        # try:
                        #     # Run the command with stdin for password input
                        #     cp = await conn.run(
                        #         full_command,
                        #         input=password_input,  # Provide the password via stdin
                        #         check=False  # Do not raise an exception on failure
                        #     )
                        #
                        #     if cp.stderr:
                        #         # Check if the command failed due to incorrect password
                        #         if "sudo: no tty present and no askpass program specified" in cp.stderr:
                        #             raise RuntimeError("Password was required but not provided.")
                        #         elif "Sorry, try again." in cp.stderr:
                        #             raise RuntimeError("Incorrect password provided.")
                        #         elif "are you root?" in cp.stderr:
                        #             raise RuntimeError("Insufficient privileges. Ensure the user has sudo access.")
                        #         else:
                        #             raise RuntimeError(f"Command failed with error: {cp.stderr}")
                        #
                        #     return cp
                        #
                        # except asyncssh.ProcessError as e:
                        #     # Handle any process-related errors
                        #     print(f"Command failed with error: {e}")
                        #     return None

                    elif operation.su:
                        # print(f"its su {sudo_info["su_user"]} {command}")
                        full_command = f"su {sudo_info['su_user']} -c '{command}'"
                        # print(full_command)
                        if sudo_info["su_user"] == "root":
                            # For root, don't expect password prompt
                            async with conn.create_process(full_command,
                                                           term_type='xterm',
                                                           stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                           stderr=asyncssh.PIPE) as process:
                                try:
                                    output = await process.stdout.readuntil('Password:')
                                    process.stdin.write(f'{sudo_info["su_password"]}\n')
                                except asyncio.TimeoutError:
                                    # No password prompt, continue without writing to stdin
                                    pass
                                stdout, stderr = await process.communicate()
                        else:
                            # For non-root users, handle password prompt
                            async with conn.create_process(full_command,
                                                           term_type='xterm',
                                                           stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                           stderr=asyncssh.PIPE) as process:
                                output = await process.stdout.readuntil('Password:')
                                process.stdin.write(f'{sudo_info["su_password"]}\n')
                                stdout, stderr = await process.communicate()

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
