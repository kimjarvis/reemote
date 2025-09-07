from asyncssh import SSHCompletedProcess


def print_ssh_completed_process(process_result):
    """
    Prints the fields of an SSHCompletedProcess object.

    :param process_result: The completed process result to inspect.
    :type process_result: SSHCompletedProcess
    """
    # Ensure the input is of the expected type
    if not isinstance(process_result, SSHCompletedProcess):
        raise ValueError("The provided argument is not an instance of SSHCompletedProcess.")

    # Define the fields to extract and their descriptions
    fields = [
        ("env", "The environment the client requested to be set for the process", "dict or None"),
        ("command", "The command the client requested the process to execute (if any)", "str or None"),
        ("subsystem", "The subsystem the client requested the process to open (if any)", "str or None"),
        ("exit_status", "The exit status returned, or -1 if an exit signal is sent", "int"),
        ("exit_signal", "The exit signal sent (if any) in the form of a tuple containing the signal name, "
                        "a bool for whether a core dump occurred, a message associated with the signal, "
                        "and the language the message was in", "tuple or None"),
        ("returncode", "The exit status returned, or negative of the signal number when an exit signal is sent", "int"),
        ("stdout", "The output sent by the process to stdout (if not redirected)", "str or bytes"),
        ("stderr", "The output sent by the process to stderr (if not redirected)", "str or bytes"),
    ]

    # Print each field and its value
    print("Fields of SSHCompletedProcess:")
    print("--------------------------------")
    for field_name, description, field_type in fields:
        value = getattr(process_result, field_name, None)
        print(f"{field_name}: {value}")
        print(f"  Description: {description}")
        print(f"  Type: {field_type}")
        print()
