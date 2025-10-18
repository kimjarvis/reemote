# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class AptRepository:
    """
    A class to manage APT repositories on Debian-based systems.
    This class allows adding or removing APT repositories similar to the ansible.builtin.apt_repository module.

    Attributes:
        repo (str): A source string for the repository.
        state (str): Desired state of the repository ('present' or 'absent').
        codename (str): Override the distribution codename for PPA repositories.
        filename (str): Sets the name of the source list file in sources.list.d.
        install_python_apt (bool): Whether to automatically install python3-apt.
        mode (str): The octal mode for newly created files in sources.list.d.
        update_cache (bool): Run apt-get update when a change occurs.
        update_cache_retries (int): Amount of retries if the cache update fails.
        update_cache_retry_max_delay (int): Max delay for exponential backoff.
        validate_certs (bool): If false, SSL certificates will not be validated.

    **Examples:**

    .. code:: python

        # Add a repository
        r = yield AptRepository(
            repo="deb http://archive.canonical.com/ubuntu hardy partner",
            state="present"
        )
        print(r.cp.stdout)

        # Remove a repository
        r = yield AptRepository(
            repo="deb http://archive.canonical.com/ubuntu hardy partner",
            state="absent"
        )
        print(r.cp.stdout)

        # Add repository with custom filename
        r = yield AptRepository(
            repo="deb http://dl.google.com/linux/chrome/deb/ stable main",
            state="present",
            filename="google-chrome"
        )
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Requires python3-apt and apt-key or gpg on the target system
        - Supports Debian Squeeze (version 6) and its successors
        - Works with both regular repositories and PPAs
    """

    def __init__(self,
                 repo: str,
                 state: str = "present",
                 codename: str = None,
                 filename: str = None,
                 install_python_apt: bool = True,
                 mode: str = None,
                 update_cache: bool = True,
                 update_cache_retries: int = 5,
                 update_cache_retry_max_delay: int = 12,
                 validate_certs: bool = True):

        self.repo = repo
        self.state = state
        self.codename = codename
        self.filename = filename
        self.install_python_apt = install_python_apt
        self.mode = mode
        self.update_cache = update_cache
        self.update_cache_retries = update_cache_retries
        self.update_cache_retry_max_delay = update_cache_retry_max_delay
        self.validate_certs = validate_certs

    def __repr__(self):
        return (f"AptRepository(repo={self.repo!r}, "
                f"state={self.state!r}, "
                f"codename={self.codename!r}, "
                f"filename={self.filename!r}, "
                f"install_python_apt={self.install_python_apt!r}, "
                f"mode={self.mode!r}, "
                f"update_cache={self.update_cache!r}, "
                f"update_cache_retries={self.update_cache_retries!r}, "
                f"update_cache_retry_max_delay={self.update_cache_retry_max_delay!r}, "
                f"validate_certs={self.validate_certs!r})")

    def execute(self):
        # Build the apt_repository command
        cmd_parts = ["apt_repository"]

        # Add required repo parameter
        cmd_parts.append(f"repo='{self.repo}'")

        # Add state parameter
        cmd_parts.append(f"state={self.state}")

        # Add optional parameters if specified
        if self.codename is not None:
            cmd_parts.append(f"codename={self.codename}")

        if self.filename is not None:
            cmd_parts.append(f"filename={self.filename}")

        if not self.install_python_apt:
            cmd_parts.append("install_python_apt=false")

        if self.mode is not None:
            cmd_parts.append(f"mode={self.mode}")

        if not self.update_cache:
            cmd_parts.append("update_cache=false")

        cmd_parts.append(f"update_cache_retries={self.update_cache_retries}")
        cmd_parts.append(f"update_cache_retry_max_delay={self.update_cache_retry_max_delay}")

        if not self.validate_certs:
            cmd_parts.append("validate_certs=false")

        # Construct the full command
        full_cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(full_cmd, guard=True, sudo=True)
        r.changed = True