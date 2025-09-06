import pytest
from asyncssh import SSHCompletedProcess
from reemote.operations.filesystem.chown import Chown
from reemote.result import Result
from reemote.operation import Operation


def process_chown_generator(generator, ls_output_before, ls_output_after, returncode=0):
    """Helper function to process a Chown generator and collect commands"""
    commands = []
    results = []

    try:
        # First yield: ls -ld command
        value = next(generator)
        if isinstance(value, str):
            commands.append(value)
        elif isinstance(value, Operation):
            commands.append(value.command)

        # Send first ls result
        s1 = SSHCompletedProcess()
        s1.returncode = returncode
        s1.stdout = ls_output_before
        result1 = Result(cp=s1, host="localhost")
        results.append(result1)

        # Second yield: chown command
        value = generator.send(result1)
        if isinstance(value, str):
            commands.append(value)
        elif isinstance(value, Operation):
            commands.append(value.command)

        # Send chown result (empty stdout for chown)
        s2 = SSHCompletedProcess()
        s2.returncode = returncode
        result2 = Result(cp=s2, host="localhost")
        results.append(result2)

        # Third yield: ls -ld command again
        value = generator.send(result2)
        if isinstance(value, str):
            commands.append(value)
        elif isinstance(value, Operation):
            commands.append(value.command)

        # Send final ls result
        s3 = SSHCompletedProcess()
        s3.returncode = returncode
        s3.stdout = ls_output_after
        result3 = Result(cp=s3, host="localhost")
        results.append(result3)

        # Final send to complete the generator
        generator.send(result3)

    except StopIteration:
        pass

    return commands, results


@pytest.mark.parametrize(
    "target, user, group, options, sudo, ls_before, ls_after, expected_commands",
    [
        # Test case 1: Change user only
        (
                "/opt/myapp", "myuser", None, [], False,
                "drwxr-xr-x 2 root root 4096 Dec 1 10:00 /opt/myapp",
                "drwxr-xr-x 2 myuser root 4096 Dec 1 10:00 /opt/myapp",
                ["ls -ld /opt/myapp", "chown myuser /opt/myapp", "ls -ld /opt/myapp"]
        ),
        # Test case 2: Change user and group with sudo
        (
                "/opt/myapp", "myuser", "mygroup", [], True,
                "drwxr-xr-x 2 root root 4096 Dec 1 10:00 /opt/myapp",
                "drwxr-xr-x 2 myuser mygroup 4096 Dec 1 10:00 /opt/myapp",
                ["sudo -S ls -ld /opt/myapp", "sudo -S chown myuser:mygroup /opt/myapp", "sudo -S ls -ld /opt/myapp"]
        ),
        # Test case 3: Change group only
        (
                "/opt/myapp", None, "mygroup", [], False,
                "drwxr-xr-x 2 root root 4096 Dec 1 10:00 /opt/myapp",
                "drwxr-xr-x 2 root mygroup 4096 Dec 1 10:00 /opt/myapp",
                ["ls -ld /opt/myapp", "chgrp mygroup /opt/myapp", "ls -ld /opt/myapp"]
        ),
        # Test case 4: With options
        (
                "/opt/myapp", "myuser", "mygroup", ["-R"], False,
                "drwxr-xr-x 2 root root 4096 Dec 1 10:00 /opt/myapp",
                "drwxr-xr-x 2 myuser mygroup 4096 Dec 1 10:00 /opt/myapp",
                ["ls -ld /opt/myapp", "chown -R myuser:mygroup /opt/myapp", "ls -ld /opt/myapp"]
        ),
        # Test case 5: No change (same before and after)
        (
                "/opt/myapp", "myuser", None, [], False,
                "drwxr-xr-x 2 myuser root 4096 Dec 1 10:00 /opt/myapp",
                "drwxr-xr-x 2 myuser root 4096 Dec 1 10:00 /opt/myapp",
                ["ls -ld /opt/myapp", "chown myuser /opt/myapp", "ls -ld /opt/myapp"]
        ),
    ],
)
def test_chown(target, user, group, options, sudo, ls_before, ls_after, expected_commands):
    # Create an instance of the Chown class
    chown_instance = Chown(
        target=target,
        user=user,
        group=group,
        options=options,
        sudo=sudo
    )

    # Process the generator
    commands, results = process_chown_generator(
        chown_instance.execute(),
        ls_before,
        ls_after
    )

    # Debugging: Print the commands for inspection
    print("Generated Commands:", commands)
    print("Expected Commands:", expected_commands)

    # Assert that the commands match the expected sequence
    assert commands == expected_commands

    # Additional assertion: check if changed flag is set correctly
    if ls_before != ls_after:
        # The second result (chown command result) should have changed=True
        assert hasattr(results[1], 'changed') and results[1].changed is True
    else:
        # No change should have occurred
        assert not hasattr(results[1], 'changed') or results[1].changed is not True


def test_chown_no_user_or_group():
    """Test that ValueError is raised when neither user nor group is specified"""
    with pytest.raises(ValueError, match="Either user or group must be specified"):
        Chown(target="/opt/myapp")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])