import pytest
from asyncssh import SSHCompletedProcess
from reemote.operations.filesystem.directory import Directory
from reemote.result import Result
from reemote.operation import Operation


def process_generator(generator, returncode):
    """Helper function to process a generator and collect commands"""
    commands = []
    try:
        value = next(generator)
        while True:
            # Handle strings by converting to Operation objects
            if isinstance(value, str):
                operation = Operation(command=value)
                commands.append(operation.command)

                # Simulate result
                s = SSHCompletedProcess()
                s.returncode = returncode
                result = Result(cp=s, host="localhost")

                value = generator.send(result)

            elif isinstance(value, Operation):
                commands.append(value.command)

                # Simulate result
                s = SSHCompletedProcess()
                s.returncode = returncode
                result = Result(cp=s, host="localhost")

                value = generator.send(result)

    except StopIteration:
        pass

    return commands


@pytest.mark.parametrize(
    "path, present, sudo, returncode, expected_commands",
    [
        ("/opt/myapp", False, False, 0, ["[ -d /opt/myapp ]", "rmdir -p /opt/myapp"]),
        ("/opt/myapp", False, False, 1, ["[ -d /opt/myapp ]"]),
        ("/opt/myapp", True, False, 0, ["[ -d /opt/myapp ]"]),
        ("/opt/myapp", True, False, 1, ["[ -d /opt/myapp ]", "mkdir -p /opt/myapp"]),
        ("/opt/myapp", False, True, 0, ["sudo -S [ -d /opt/myapp ]", "sudo -S rmdir -p /opt/myapp"]),
        ("/opt/myapp", False, True, 1, ["sudo -S [ -d /opt/myapp ]"]),
        ("/opt/myapp", True, True, 0, ["sudo -S [ -d /opt/myapp ]"]),
        ("/opt/myapp", True, True, 1, ["sudo -S [ -d /opt/myapp ]", "sudo -S mkdir -p /opt/myapp"]),
    ],
)
def test_directory(path, present, sudo, returncode, expected_commands):
    # Create an instance of the Directory class
    directory_instance = Directory(path=path, present=present, sudo=sudo)

    # Process the generator
    commands = process_generator(directory_instance.execute(), returncode)

    # Debugging: Print the commands for inspection
    print("Generated Commands:", commands)
    print("Expected Commands:", expected_commands)

    # Assert that the commands match the expected sequence
    assert commands == expected_commands