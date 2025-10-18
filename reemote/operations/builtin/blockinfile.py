# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class BlockInFile:
    """
    A class to encapsulate the functionality of the ansible.builtin.blockinfile module.
    This module inserts, updates, or removes a block of multi-line text surrounded by marker lines in a file.

    Attributes:
        path (str): The file to modify.
        block (str): The text to insert inside the marker lines.
        state (str): Whether the block should be there or not ("present" or "absent").
        marker (str): The marker line template.
        marker_begin (str): Text inserted at {mark} in the opening marker.
        marker_end (str): Text inserted at {mark} in the closing marker.
        insertafter (str): Insert block after the last match of specified regex.
        insertbefore (str): Insert block before the last match of specified regex.
        create (bool): Create a new file if it does not exist.
        backup (bool): Create a backup file with timestamp information.
        append_newline (bool): Append a blank line to the inserted block.
        prepend_newline (bool): Prepend a blank line to the inserted block.
        owner (str): Name of the user that should own the file.
        group (str): Name of the group that should own the file.
        mode (str): The permissions the resulting file should have.
        attributes (str): The attributes the resulting file should have.
        seuser (str): The user part of the SELinux file context.
        serole (str): The role part of the SELinux file context.
        setype (str): The type part of the SELinux file context.
        selevel (str): The level part of the SELinux file context.
        unsafe_writes (bool): Allow unsafe writes if atomic operations fail.
        validate (str): Validation command to run before copying the file.
        guard (bool): If False, the commands will not be executed.

    **Examples:**

    .. code:: python

        # Insert/Update "Match User" configuration block in /etc/ssh/sshd_config
        r = yield BlockInFile(
            path="/etc/ssh/sshd_config",
            block="Match User ansible-agent\\nPasswordAuthentication no",
            append_newline=True,
            prepend_newline=True
        )
        print(r.cp.stdout)

        # Insert/Update eth0 configuration in /etc/network/interfaces
        r = yield BlockInFile(
            path="/etc/network/interfaces",
            block="iface eth0 inet static\\n    address 192.0.2.23\\n    netmask 255.255.255.0"
        )
        print(r.cp.stdout)

        # Remove HTML block and surrounding markers
        r = yield BlockInFile(
            path="/var/www/html/index.html",
            marker="<!-- {mark} ANSIBLE MANAGED BLOCK -->",
            block="",
            state="absent"
        )
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - When using loops, ensure each block has a unique marker to prevent overwriting.
        - The dest option from older versions is now path, but dest still works.
        - Multiple blocks in one file require different markers per task.
    """

    def __init__(self,
                 path: str,
                 block: str = "",
                 state: str = "present",
                 marker: str = "# {mark} ANSIBLE MANAGED BLOCK",
                 marker_begin: str = "BEGIN",
                 marker_end: str = "END",
                 insertafter: str = None,
                 insertbefore: str = None,
                 create: bool = False,
                 backup: bool = False,
                 append_newline: bool = False,
                 prepend_newline: bool = False,
                 owner: str = None,
                 group: str = None,
                 mode: str = None,
                 attributes: str = None,
                 seuser: str = None,
                 serole: str = None,
                 setype: str = None,
                 selevel: str = None,
                 unsafe_writes: bool = False,
                 validate: str = None,
                 guard: bool = True):

        self.path = path
        self.block = block
        self.state = state
        self.marker = marker
        self.marker_begin = marker_begin
        self.marker_end = marker_end
        self.insertafter = insertafter
        self.insertbefore = insertbefore
        self.create = create
        self.backup = backup
        self.append_newline = append_newline
        self.prepend_newline = prepend_newline
        self.owner = owner
        self.group = group
        self.mode = mode
        self.attributes = attributes
        self.seuser = seuser
        self.serole = serole
        self.setype = setype
        self.selevel = selevel
        self.unsafe_writes = unsafe_writes
        self.validate = validate
        self.guard = guard

    def __repr__(self):
        return (f"BlockInFile(path={self.path!r}, "
                f"block={self.block!r}, "
                f"state={self.state!r}, "
                f"marker={self.marker!r}, "
                f"marker_begin={self.marker_begin!r}, "
                f"marker_end={self.marker_end!r}, "
                f"insertafter={self.insertafter!r}, "
                f"insertbefore={self.insertbefore!r}, "
                f"create={self.create!r}, "
                f"backup={self.backup!r}, "
                f"append_newline={self.append_newline!r}, "
                f"prepend_newline={self.prepend_newline!r}, "
                f"owner={self.owner!r}, "
                f"group={self.group!r}, "
                f"mode={self.mode!r}, "
                f"attributes={self.attributes!r}, "
                f"seuser={self.seuser!r}, "
                f"serole={self.serole!r}, "
                f"setype={self.setype!r}, "
                f"selevel={self.selevel!r}, "
                f"unsafe_writes={self.unsafe_writes!r}, "
                f"validate={self.validate!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Build the ansible command
        cmd_parts = ["ansible.builtin.blockinfile"]

        # Add required path parameter
        cmd_parts.append(f"path={self.path}")

        # Add optional parameters
        if self.block:
            # Escape newlines for shell command
            escaped_block = self.block.replace('\n', '\\n')
            cmd_parts.append(f"block='{escaped_block}'")
        if self.state != "present":
            cmd_parts.append(f"state={self.state}")
        if self.marker != "# {mark} ANSIBLE MANAGED BLOCK":
            cmd_parts.append(f"marker='{self.marker}'")
        if self.marker_begin != "BEGIN":
            cmd_parts.append(f"marker_begin={self.marker_begin}")
        if self.marker_end != "END":
            cmd_parts.append(f"marker_end={self.marker_end}")
        if self.insertafter:
            cmd_parts.append(f"insertafter='{self.insertafter}'")
        if self.insertbefore:
            cmd_parts.append(f"insertbefore='{self.insertbefore}'")
        if self.create:
            cmd_parts.append("create=yes")
        if self.backup:
            cmd_parts.append("backup=yes")
        if self.append_newline:
            cmd_parts.append("append_newline=yes")
        if self.prepend_newline:
            cmd_parts.append("prepend_newline=yes")
        if self.owner:
            cmd_parts.append(f"owner={self.owner}")
        if self.group:
            cmd_parts.append(f"group={self.group}")
        if self.mode:
            cmd_parts.append(f"mode={self.mode}")
        if self.attributes:
            cmd_parts.append(f"attributes={self.attributes}")
        if self.seuser:
            cmd_parts.append(f"seuser={self.seuser}")
        if self.serole:
            cmd_parts.append(f"serole={self.serole}")
        if self.setype:
            cmd_parts.append(f"setype={self.setype}")
        if self.selevel:
            cmd_parts.append(f"selevel={self.selevel}")
        if self.unsafe_writes:
            cmd_parts.append("unsafe_writes=yes")
        if self.validate:
            cmd_parts.append(f"validate='{self.validate}'")

        # Construct the full command
        cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(cmd, guard=self.guard)
        r.changed = True