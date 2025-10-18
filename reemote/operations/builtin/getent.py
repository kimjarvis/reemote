# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class GetEnt:
    """
    A class to encapsulate the functionality of the Unix getent utility.
    It allows users to query system databases such as passwd, group, hosts, etc.

    Attributes:
        database (str): The name of a getent database supported by the target system (passwd, group, hosts, etc).
        key (str, optional): Key from which to return values from the specified database, otherwise the full contents are returned.
        fail_key (bool): If a supplied key is missing this will make the task fail if true. Default is True.
        service (str, optional): Override all databases with the specified service. Requires system support.
        split (str, optional): Character used to split the database values into lists/arrays such as ':' or '\\t'.

    **Examples:**

    .. code:: python

        # Get root user info
        r = yield GetEnt(database="passwd", key="root")
        # Access the result
        print(r.facts.getent_passwd)

        # Get all groups
        r = yield GetEnt(database="group", split=":")
        # Access the result
        print(r.facts.getent_group)

        # Get http service info, no error if missing
        r = yield GetEnt(database="services", key="http", fail_key=False)
        # Access the result
        print(r.facts.getent_services)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
        The results are available in the facts dictionary under getent_<database> keys.

    Notes:
        - Not all databases support enumeration, check system documentation for details
        - Commands are constructed based on the provided parameters
        - Results are returned in ansible_facts format for consistency with Ansible behavior
    """

    def __init__(self,
                 database: str,
                 key: str = None,
                 fail_key: bool = True,
                 service: str = None,
                 split: str = None):

        self.database = database
        self.key = key
        self.fail_key = fail_key
        self.service = service
        self.split = split

    def __repr__(self):
        return (f"GetEnt(database={self.database!r}, "
                f"key={self.key!r}, "
                f"fail_key={self.fail_key!r}, "
                f"service={self.service!r}, "
                f"split={self.split!r})")

    def execute(self):
        # Build the getent command
        cmd_parts = ["getent"]

        if self.service:
            cmd_parts.extend(["-s", self.service])

        cmd_parts.append(self.database)

        if self.key:
            cmd_parts.append(self.key)

        # Add split handling if specified
        if self.split:
            # For demonstration purposes, we'll handle splitting in post-processing
            pass

        cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(cmd, guard=True)

        # Process the output based on split parameter
        if r.cp.returncode == 0:
            output_lines = r.cp.stdout.strip().split('\n')

            # Parse the getent output
            parsed_results = []
            for line in output_lines:
                if line.strip():
                    if self.split:
                        parsed_results.append(line.split(self.split))
                    else:
                        # Try to auto-detect delimiter based on database type
                        if self.database in ['passwd', 'shadow', 'group']:
                            parsed_results.append(line.split(':'))
                        elif self.database in ['hosts']:
                            parsed_results.append(line.split())
                        else:
                            parsed_results.append([line])

            # Handle fail_key logic
            if self.key and not parsed_results and self.fail_key:
                raise Exception(f"Key '{self.key}' not found in database '{self.database}'")

            # Store results in facts
            fact_key = f"getent_{self.database}"
            r.facts = {
                fact_key: parsed_results if len(parsed_results) > 1 else parsed_results[0] if parsed_results else []}

        elif self.key and self.fail_key:
            raise Exception(f"Failed to retrieve key '{self.key}' from database '{self.database}'")

        r.changed = False