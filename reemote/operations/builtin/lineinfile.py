import re
import asyncssh
from asyncssh.sftp import SFTPAttrs

from reemote.command_install import Command_install
from reemote.command import Command
from reemote.operations.sftp.read_file import Read_file
from reemote.operations.sftp.write_file import Write_file
from reemote.operations.sftp.setstat import Setstat

class Lineinfile():
    """
    A class to manage lines in a remote file using SFTP operations.

    This class allows you to ensure that a specific line exists in a file on a remote server.
    It supports adding, replacing, or modifying lines based on search criteria such as regular
    expressions, string matches, or exact line comparisons. Additionally, it can set file
    attributes (e.g., permissions) after making changes.

    :param str line: The line to ensure exists in the file. This line will be added, replaced,
                     or left unchanged depending on the search criteria.
    :param str path: The path to the file on the remote server.
    :param str regexp: A regular expression pattern to match lines in the file. Mutually
                       exclusive with ``search_string``. Defaults to ``None``.
    :param str search_string: A string to search for in the file. Mutually exclusive with
                              ``regexp``. Defaults to ``None``.
    :param str insertafter: A pattern or keyword ('EOF') specifying where to insert the line
                            after a match. Mutually exclusive with ``insertbefore``.
                            Defaults to ``None``.
    :param str insertbefore: A pattern or keyword ('BOF') specifying where to insert the line
                             before a match. Mutually exclusive with ``insertafter``.
                             Defaults to ``None``.
    :param dict attrs: File attributes (e.g., permissions) to set after modifying the file.
                       Defaults to ``None``.

    :raises ValueError: If mutually exclusive parameters (``regexp`` and ``search_string``, or
                        ``insertafter`` and ``insertbefore``) are both provided.

    **Examples:**

    Ensure SELinux is set to enforcing mode:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/selinux/config",
            regexp="^SELINUX=",
            line="SELINUX=enforcing",
        )

    Make sure group wheel is not in the sudoers configuration:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/sudoers",
            state="absent",
            regexp="^%wheel",
        )

    Replace a localhost entry with our own:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/hosts",
            regexp="^127\\.0\\.0\\.1",
            line="127.0.0.1 localhost",
            owner="root",
            group="root",
            mode="0644",
        )

    Replace a localhost entry searching for a literal string to avoid escaping:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/hosts",
            search_string="127.0.0.1",
            line="127.0.0.1 localhost",
            owner="root",
            group="root",
            mode="0644",
        )

    Ensure the default Apache port is 8080:

    Ensure php extension matches new pattern:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/httpd/conf/httpd.conf",
            search_string='<FilesMatch ".php[45]?$">',
            insertafter="^\t<Location />\n",
            line='        <FilesMatch ".php[34]?$">',
        )

    Ensure we have our own comment added to /etc/services:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/services",
            regexp="^# port for http",
            insertbefore="^www.*80/tcp",
            line="# port for http by default",
        )

    Add a line to a file if the file does not exist, without passing regexp:

    .. code-block:: python

        yield Lineinfile(
            path="/tmp/testfile",
            line="192.168.1.99 foo.lab.net foo",
            create=True,
        )

    Ensure the JBoss memory settings are exactly as needed:

    .. code-block:: python

        yield Lineinfile(
            path="/opt/jboss-as/bin/standalone.conf",
            regexp="^(.*)Xms(\\d+)m(.*)$",
            line="\\1Xms${xms}m\\3",
            backrefs=True,
        )

    Validate the sudoers file before saving:

    .. code-block:: python

        yield Lineinfile(
            path="/etc/sudoers",
            state="present",
            regexp="^%ADMIN ALL=",
            line="%ADMIN ALL=(ALL) NOPASSWD: ALL",
            validate="/usr/sbin/visudo -cf %s",
        )

    Use backrefs with alternative group syntax to avoid conflicts with variable values:

    .. code-block:: python

        yield Lineinfile(
            path="/tmp/config",
            regexp="^(host=).*",
            line="\\g<1>{{ hostname }}",
            backrefs=True,
        )

    .. note::
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.
    """


    def __init__(self,
                 line="",
                 path="",
                 regexp=None,
                 search_string=None,
                 insertafter=None,
                 insertbefore=None,
                 attrs=None):
        if regexp and search_string:
            raise ValueError("Parameters 'regexp' and 'search_string' are mutually exclusive.")
        if insertafter and insertbefore:
            raise ValueError("Parameters 'insertafter' and 'insertbefore' are mutually exclusive.")

        self.line = line
        self.path = path
        self.regexp = regexp
        self.search_string = search_string
        self.insertafter = insertafter
        self.insertbefore = insertbefore
        self.attrs = attrs  # Store the raw attrs parameter

        # Debug: Inspect the attrs parameter
        print(f"Initialized Lineinfile with attrs: {self.attrs}")

    def _match_line(self, file_line):
        """Check if a builtin line matches the search criteria."""
        if self.regexp is not None:
            # Match using a regular expression
            return re.search(self.regexp, file_line) is not None
        elif self.search_string is not None:
            # Match using a string search
            return self.search_string in file_line
        else:
            # Match using an exact line comparison
            return self.line == file_line.rstrip('\n')

    def execute(self):
        """
        :no-index:
        """
        r = yield Read_file(path=self.path)
        content = r.cp.stdout

        # Handle empty builtin or builtin not existing
        if not content:
            lines = []
        elif isinstance(content, bytes):
            lines = content.decode('utf-8').splitlines(keepends=True)
        else:
            lines = content.splitlines(keepends=True)

        # Normalize line to not end with newline
        target_line = self.line.rstrip('\n')

        # Step 1: Check if the exact target line already exists
        exact_match_found = False
        regex_match_indices = []

        for i, line in enumerate(lines):
            # Check for exact match of the target line (idempotency)
            if line.rstrip('\r\n') == target_line:
                exact_match_found = True
                print(f"Exact match found. Setting attributes for {self.path}: {self.attrs}")
                if self.attrs:
                    yield Setstat(path=self.path, attrs=self.attrs)  # Pass attrs directly
                return

            # Check if line matches our search pattern (for replacement)
            if self._match_line(line):
                regex_match_indices.append(i)

        # Step 2: Replace the last matching line if no exact match
        if regex_match_indices and not exact_match_found:
            last_match = regex_match_indices[-1]

            # Preserve the original line ending
            original_line_ending = '\n'
            if lines[last_match].endswith('\r\n'):
                original_line_ending = '\r\n'
            elif lines[last_match].endswith('\n'):
                original_line_ending = '\n'

            lines[last_match] = target_line + original_line_ending
            new_content = ''.join(lines)

            print("Replacing line and setting attributes...")
            yield Write_file(path=self.path, text=new_content)
            if self.attrs:
                yield Setstat(path=self.path, attrs=self.attrs)  # Pass attrs directly
            return

        # Step 3: Insert the line if no matches found
        if not exact_match_found and not regex_match_indices:
            insert_index = None

            if self.insertbefore is not None:
                if self.insertbefore == 'BOF':
                    insert_index = 0
                else:
                    pattern = self.insertbefore
                    insert_index = len(lines)  # Default to end if pattern not found
                    for i, line in enumerate(lines):
                        if re.search(pattern, line):
                            insert_index = i
                            break
            elif self.insertafter is not None:
                if self.insertafter == 'EOF':
                    insert_index = len(lines)
                else:
                    pattern = self.insertafter
                    insert_index = len(lines)  # Default to end if pattern not found
                    for i, line in enumerate(lines):
                        if re.search(pattern, line):
                            insert_index = i + 1
            else:
                # Default: append to end of builtin
                insert_index = len(lines)

            # Determine line ending
            line_ending = '\n'
            if lines:
                if lines[0].endswith('\r\n'):
                    line_ending = '\r\n'
                elif lines[0].endswith('\n'):
                    line_ending = '\n'

            # Create new content with the inserted line
            if not lines:
                new_content = target_line + line_ending
            else:
                lines.insert(insert_index, target_line + line_ending)
                new_content = ''.join(lines)

            print("Inserting line and setting attributes...")
            yield Write_file(path=self.path, text=new_content)
            if self.attrs:
                yield Setstat(path=self.path, attrs=self.attrs)  # Pass attrs directly