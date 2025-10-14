# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
import json


class Find:
    """
    A class to encapsulate the functionality of finding files based on specific criteria.
    This mimics the ansible.builtin.find module behavior, allowing users to search for files
    based on various criteria like age, size, patterns, etc.

    Attributes:
        paths (list): List of paths to search in.
        age (str): Select files whose age is equal to or greater than the specified time.
        age_stamp (str): Choose the file property against which we compare age (atime, ctime, mtime).
        checksum_algorithm (str): Algorithm to determine checksum of file.
        contains (str): A regular expression or pattern to match against file content.
        depth (int): Set the maximum number of levels to descend into.
        encoding (str): Encoding of files when doing contains search.
        exact_mode (bool): Restrict mode matching to exact matches only.
        excludes (list): Patterns to exclude from matches.
        file_type (str): Type of file to select (any, directory, file, link).
        follow (bool): Whether to follow symlinks.
        get_checksum (bool): Whether to return a checksum of the file.
        hidden (bool): Include hidden files.
        limit (int): Limit the maximum number of matching paths returned.
        mode (str): Choose objects matching specified permission.
        patterns (list): Patterns to match file basenames.
        read_whole_file (bool): Whether to read whole file for contains search.
        recurse (bool): Recursively descend into directories.
        size (str): Select files by size.
        use_regex (bool): Whether patterns are regex or shell globs.

    **Examples:**

    .. code:: python

        # Recursively find /tmp files older than 2 days
        r = yield Find(paths=["/tmp"], age="2d", recurse=True)
        print(r.cp.files)

        # Find /var/log files equal or greater than 10 megabytes ending with .old or .log.gz
        r = yield Find(paths=["/var/log"], patterns=["*.old", "*.log.gz"], size="10m")
        print(r.cp.files)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - This implementation uses the ansible find module under the hood
        - Multiple criteria are AND'd together
    """

    def __init__(self,
                 paths: list,
                 age: str = None,
                 age_stamp: str = "mtime",
                 checksum_algorithm: str = "sha1",
                 contains: str = None,
                 depth: int = None,
                 encoding: str = "utf-8",
                 exact_mode: bool = True,
                 excludes: list = None,
                 file_type: str = "file",
                 follow: bool = False,
                 get_checksum: bool = False,
                 hidden: bool = False,
                 limit: int = None,
                 mode: str = None,
                 patterns: list = None,
                 read_whole_file: bool = False,
                 recurse: bool = False,
                 size: str = None,
                 use_regex: bool = False):

        self.paths = paths
        self.age = age
        self.age_stamp = age_stamp
        self.checksum_algorithm = checksum_algorithm
        self.contains = contains
        self.depth = depth
        self.encoding = encoding
        self.exact_mode = exact_mode
        self.excludes = excludes or []
        self.file_type = file_type
        self.follow = follow
        self.get_checksum = get_checksum
        self.hidden = hidden
        self.limit = limit
        self.mode = mode
        self.patterns = patterns or []
        self.read_whole_file = read_whole_file
        self.recurse = recurse
        self.size = size
        self.use_regex = use_regex

    def __repr__(self):
        return (f"Find(paths={self.paths!r}, "
                f"age={self.age!r}, "
                f"age_stamp={self.age_stamp!r}, "
                f"checksum_algorithm={self.checksum_algorithm!r}, "
                f"contains={self.contains!r}, "
                f"depth={self.depth!r}, "
                f"encoding={self.encoding!r}, "
                f"exact_mode={self.exact_mode!r}, "
                f"excludes={self.excludes!r}, "
                f"file_type={self.file_type!r}, "
                f"follow={self.follow!r}, "
                f"get_checksum={self.get_checksum!r}, "
                f"hidden={self.hidden!r}, "
                f"limit={self.limit!r}, "
                f"mode={self.mode!r}, "
                f"patterns={self.patterns!r}, "
                f"read_whole_file={self.read_whole_file!r}, "
                f"recurse={self.recurse!r}, "
                f"size={self.size!r}, "
                f"use_regex={self.use_regex!r})")

    def execute(self):
        # Build the ansible find command
        cmd_dict = {
            "module": "ansible.builtin.find",
            "args": {
                "paths": self.paths
            }
        }

        # Add optional parameters
        if self.age is not None:
            cmd_dict["args"]["age"] = self.age
        if self.age_stamp != "mtime":
            cmd_dict["args"]["age_stamp"] = self.age_stamp
        if self.checksum_algorithm != "sha1":
            cmd_dict["args"]["checksum_algorithm"] = self.checksum_algorithm
        if self.contains is not None:
            cmd_dict["args"]["contains"] = self.contains
        if self.depth is not None:
            cmd_dict["args"]["depth"] = self.depth
        if self.encoding != "utf-8":
            cmd_dict["args"]["encoding"] = self.encoding
        if not self.exact_mode:
            cmd_dict["args"]["exact_mode"] = self.exact_mode
        if self.excludes:
            cmd_dict["args"]["excludes"] = self.excludes
        if self.file_type != "file":
            cmd_dict["args"]["file_type"] = self.file_type
        if self.follow:
            cmd_dict["args"]["follow"] = self.follow
        if self.get_checksum:
            cmd_dict["args"]["get_checksum"] = self.get_checksum
        if self.hidden:
            cmd_dict["args"]["hidden"] = self.hidden
        if self.limit is not None:
            cmd_dict["args"]["limit"] = self.limit
        if self.mode is not None:
            cmd_dict["args"]["mode"] = self.mode
        if self.patterns:
            cmd_dict["args"]["patterns"] = self.patterns
        if self.read_whole_file:
            cmd_dict["args"]["read_whole_file"] = self.read_whole_file
        if self.recurse:
            cmd_dict["args"]["recurse"] = self.recurse
        if self.size is not None:
            cmd_dict["args"]["size"] = self.size
        if self.use_regex:
            cmd_dict["args"]["use_regex"] = self.use_regex

        # Convert to JSON and execute
        cmd_json = json.dumps(cmd_dict)
        r = yield Command(f"ansible localhost -c local -m {cmd_dict['module']} -a '{json.dumps(cmd_dict['args'])}'",
                         guard=True, sudo=False, su=False)
        r.changed = False