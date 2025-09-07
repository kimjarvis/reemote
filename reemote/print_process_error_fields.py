from asyncssh import ProcessError


def print_process_error_fields(process_error):
    """
    Prints the fields of an asyncssh.ProcessError exception.

    Args:
        process_error (asyncssh.ProcessError): The exception instance to inspect.
    """
    if not isinstance(process_error, ProcessError):
        raise ValueError("The provided argument is not an instance of asyncssh.ProcessError.")

    # Define the fields to extract and their descriptions
    fields = [
        ("env", "The environment the client requested to be set for the process"),
        ("command", "The command the client requested the process to execute (if any)"),
        ("subsystem", "The subsystem the client requested the process to open (if any)"),
        ("exit_status", "The exit status returned, or -1 if an exit signal is sent"),
        ("exit_signal", "The exit signal sent (if any) in the form of a tuple containing the signal name, "
                        "a bool for whether a core dump occurred, a message associated with the signal, "
                        "and the language the message was in"),
        ("returncode", "The exit status returned, or negative of the signal number when an exit signal is sent"),
        ("stdout", "The output sent by the process to stdout (if not redirected)"),
        ("stderr", "The output sent by the process to stderr (if not redirected)"),
        ("reason", "The reason for the error"),
        ("lang", "The language of the error message")
    ]

    # Print each field and its value
    print("Fields of asyncssh.ProcessError:")
    print("--------------------------------")
    for field_name, description in fields:
        value = getattr(process_error, field_name, None)
        print(f"{field_name}: {value}")
        print(f"  Description: {description}")
        print()
