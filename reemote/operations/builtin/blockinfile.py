import re
from asyncssh.sftp import SFTPAttrs

from reemote.operations.sftp.read_file import Read_file
from reemote.operations.sftp.write_file import Write_file
from reemote.operations.sftp.setstat import Setstat


class Blockinfile:
    """
    A class to manage blocks of text in builtin using marker lines.

    This class is inspired by Ansible's `blockinfile` module. It allows inserting,
    updating, or removing a block of text between customizable marker lines in a builtin.
    The class also supports setting builtin attributes such as permissions, ownership,
    and group information.

    Attributes:
        path (str): The path to the builtin to modify.
        block (str, optional): The text to insert inside the marker lines. If empty or None,
                               the block will be removed if `state="absent"`. Defaults to ``""``.
        marker (str, optional): The marker line template. `{mark}` will be replaced with the values
                                in `marker_begin` (default="BEGIN") and `marker_end` (default="END").
                                Defaults to ``"# {mark} ANSIBLE MANAGED BLOCK"``.
        marker_begin (str, optional): The text to replace `{mark}` in the opening marker line.
                                      Defaults to ``"BEGIN"``.
        marker_end (str, optional): The text to replace `{mark}` in the closing marker line.
                                    Defaults to ``"END"``.
        state (str, optional): Whether the block should be present or absent. Choices are:
                               - "present" (default): Ensure the block exists.
                               - "absent": Remove the block if it exists.
                               Defaults to ``"present"``.
        create (bool, optional): Create a new builtin if it does not exist. Defaults to ``False``.
        insertafter (str, optional): Insert the block after the last match of the specified regular
                                     expression. Special value "EOF" inserts the block at the end of
                                     the builtin. Defaults to ``None``.
        insertbefore (str, optional): Insert the block before the last match of the specified regular
                                      expression. Special value "BOF" inserts the block at the beginning
                                      of the builtin. Defaults to ``None``.
        append_newline (bool, optional): Append a blank line to the inserted block if this does not
                                         appear at the end of the builtin. Defaults to ``False``.
        prepend_newline (bool, optional): Prepend a blank line to the inserted block if this does not
                                          appear at the beginning of the builtin. Defaults to ``False``.
        backup (bool, optional): Create a backup builtin including the timestamp information so the original
                                 builtin can be restored if needed. Defaults to ``False``.
        attrs (dict, optional): A dictionary containing builtin attributes to set, such as permissions,
                                owner, and group. Example: ``{"permissions": 0o644, "owner": "root",
                                "group": "root"}``. Defaults to an empty dictionary.
        unsafe_writes (bool, optional): Allow unsafe writes if atomic operations fail. Defaults to ``False``.

    Methods:
        execute():
            Executes the blockinfile operation, modifying the builtin as specified.

    **Examples:**

    .. code:: python

        # Insert/Update "Match User" configuration block in /etc/ssh/sshd_config prepending and appending a new line
        yield Blockinfile(
            path="/etc/ssh/sshd_config",
            append_newline=True,
            prepend_newline=True,
            block="Match User ansible-agent
            PasswordAuthentication no",
        )
        
        # Insert/Update eth0 configuration stanza in /etc/network/interfaces
        # (it might be better to copy files into /etc/network/interfaces.d/)
        yield Blockinfile(
            path="/etc/network/interfaces",
            block="iface eth0 inet static
            address 192.0.2.23
            netmask 255.255.255.0"
        )
    
        # Insert/Update configuration using a local file and validate it
        yield Blockinfile(
            block="{{ lookup('ansible.builtin.file', './local/sshd_config') }}",
            path="/etc/ssh/sshd_config",
            backup=True,
            validate="/usr/sbin/sshd -T -f %s",
        )
        
        # Insert/Update HTML surrounded by custom markers after <body> line
        yield Blockinfile(
            path="/var/www/html/index.html",
            marker="<!-- {mark} ANSIBLE MANAGED BLOCK -->",
            insertafter="<body>",
            block="""<h1>Welcome to {{ ansible_hostname }}</h1>
            <p>Last updated on {{ ansible_date_time.iso8601 }}</p>""",
        )
        
        # Remove HTML as well as surrounding markers
        yield Blockinfile(
            path="/var/www/html/index.html",
            marker="<!-- {mark} ANSIBLE MANAGED BLOCK -->",
            block="",
        )
        
        # Add mappings to /etc/hosts
        yield Blockinfile(
            path="/etc/hosts",
            block="{{ item.ip }} {{ item.name }}",
            marker="# {mark} ANSIBLE MANAGED BLOCK {{ item.name }}",
        )
        # loop:
        #   - { name: host1, ip: 10.10.1.10 }
        #   - { name: host2, ip: 10.10.1.11 }
        #   - { name: host3, ip: 10.10.1.12 }
        
        # Search with a multiline search flags regex and if found insert after
        yield Blockinfile(
            path="listener.ora",
            block="{{ listener_line | indent(width=8, first=True) }}",
            insertafter="(?m)SID_LIST_LISTENER_DG =\n.*\\(SID_LIST =",
            marker="    <!-- {mark} ANSIBLE MANAGED BLOCK -->",
        )
    
    Notes:
        - The `attrs` parameter is used to set builtin attributes via the `Setstat` class.
        - The `insertafter` and `insertbefore` parameters are mutually exclusive.
        - Multi-line markers are not supported and may result in repeated insertions.
    """

    def __init__(self,
                 path="",
                 block="",
                 marker="# {mark} ANSIBLE MANAGED BLOCK",
                 marker_begin="BEGIN",
                 marker_end="END",
                 state="present",
                 create=False,
                 insertafter=None,
                 insertbefore=None,
                 append_newline=False,
                 prepend_newline=False,
                 backup=False,
                 attrs=None,
                 unsafe_writes=False):
        self.path = path
        self.block = block.rstrip('\n')  # Normalize block to not end with newline
        self.marker = marker
        self.marker_begin = marker_begin
        self.marker_end = marker_end
        self.state = state
        self.create = create
        self.insertafter = insertafter
        self.insertbefore = insertbefore
        self.append_newline = append_newline
        self.prepend_newline = prepend_newline
        self.backup = backup
        self.attrs = attrs or {}  # Default to an empty dictionary if not provided
        self.unsafe_writes = unsafe_writes

        # Validate mutual exclusivity
        if self.insertafter and self.insertbefore:
            raise ValueError("Parameters 'insertafter' and 'insertbefore' are mutually exclusive.")

    def _generate_markers(self):
        """Generate the begin and end markers."""
        begin_marker = self.marker.format(mark=self.marker_begin)
        end_marker = self.marker.format(mark=self.marker_end)
        return begin_marker, end_marker

    def _find_block_indices(self, lines, begin_marker, end_marker):
        """Find the indices of the begin and end markers in the builtin."""
        begin_index = None
        end_index = None

        for i, line in enumerate(lines):
            if begin_marker in line:
                begin_index = i
            if end_marker in line:
                end_index = i
                break  # End marker found, no need to continue

        return begin_index, end_index

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

        # Generate markers
        begin_marker, end_marker = self._generate_markers()

        # Find existing block indices
        begin_index, end_index = self._find_block_indices(lines, begin_marker, end_marker)

        # Step 2: Handle state = "absent"
        if self.state == "absent":
            if begin_index is not None and end_index is not None:
                # Remove the block including the markers
                del lines[begin_index:end_index + 1]
                new_content = ''.join(lines)
                yield Write_file(path=self.path, text=new_content)
                return
            else:
                # No block to remove, nothing to do
                return

        # Step 3: Handle state = "present"
        if self.state == "present":
            # Prepare the new block content with markers
            block_lines = [f"{begin_marker}\n"]
            if self.block:
                block_lines.append(f"{self.block}\n")
            block_lines.append(f"{end_marker}\n")

            # Append/prepend newlines if required
            if self.append_newline and not block_lines[-2].endswith('\n\n'):
                block_lines.insert(-1, '\n')
            if self.prepend_newline and not block_lines[1].startswith('\n'):
                block_lines.insert(1, '\n')

            # If block already exists, replace it
            if begin_index is not None and end_index is not None:
                lines[begin_index:end_index + 1] = block_lines
                new_content = ''.join(lines)
                yield Write_file(path=self.path, text=new_content)
                return

            # If block does not exist, insert it
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

            # Insert the block
            lines[insert_index:insert_index] = block_lines
            new_content = ''.join(lines)
            yield Write_file(path=self.path, text=new_content)

        # Step 4: Set attributes if specified
        if self.attrs:
            yield Setstat(path=self.path, attrs=self.attrs)