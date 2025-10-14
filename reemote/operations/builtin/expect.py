# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
import json


class Expect:
    """
    A class to encapsulate the functionality of the ansible.builtin.expect module.
    Executes a command and responds to prompts using the pexpect library.

    This module executes a command and responds to prompts. The given command will be
    executed on all selected nodes. It will not be processed through the shell, so
    variables like $HOME and operations like "<", ">", "|", and "&" will not work.

    Attributes:
        command (str): The command to run.
        responses (dict): Mapping of prompt regular expressions and corresponding answer(s).
        chdir (str, optional): Change into this directory before running the command.
        creates (str, optional): A filename, when it already exists, this step will not be run.
        echo (bool): Whether or not to echo out your response strings.
        removes (str, optional): A filename, when it does not exist, this step will not be run.
        timeout (int, optional): Amount of time in seconds to wait for the expected strings.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Case insensitive password string match
        r = yield Expect(
            command="passwd username",
            responses={"(?i)password": "MySekretPa$$word"},
            guard=True
        )

        # Match multiple regular expressions with individual responses
        r = yield Expect(
            command="/path/to/custom/command",
            responses={
                "Question": ["response1", "response2", "response3"],
                "^Match another prompt$": "response"
            }
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - If you want to run a command through the shell, you must specify a shell in the command.
        - Case insensitive searches are indicated with a prefix of (?i).
        - The pexpect library operates with a search window of 2000 bytes.
        - For more complex scenarios, consider using expect code with Shell or Script modules.
    """

    def __init__(self,
                 command: str,
                 responses: dict,
                 chdir: str = None,
                 creates: str = None,
                 echo: bool = False,
                 removes: str = None,
                 timeout: int = 30,
                 guard: bool = True):

        self.command = command
        self.responses = responses
        self.chdir = chdir
        self.creates = creates
        self.echo = echo
        self.removes = removes
        self.timeout = timeout
        self.guard = guard

    def __repr__(self):
        return (f"Expect(command={self.command!r}, "
                f"responses={self.responses!r}, "
                f"chdir={self.chdir!r}, "
                f"creates={self.creates!r}, "
                f"echo={self.echo!r}, "
                f"removes={self.removes!r}, "
                f"timeout={self.timeout!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Construct the expect command with all parameters
        cmd_parts = ["expect"]

        # Add timeout if specified
        if self.timeout is not None:
            cmd_parts.extend(["-t", str(self.timeout)])

        # Add echo flag if true
        if self.echo:
            cmd_parts.append("-e")

        # Add chdir if specified
        if self.chdir:
            cmd_parts.extend(["--chdir", self.chdir])

        # Add creates check if specified
        if self.creates:
            cmd_parts.extend(["--creates", self.creates])

        # Add removes check if specified
        if self.removes:
            cmd_parts.extend(["--removes", self.removes])

        # Add the main command
        cmd_parts.extend(["--command", self.command])

        # Add responses as JSON
        responses_json = json.dumps(self.responses)
        cmd_parts.extend(["--responses", responses_json])

        # Join all parts into a single command string
        full_cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(full_cmd, guard=self.guard)
        r.changed = True