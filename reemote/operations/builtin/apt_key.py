# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Apt_key:
    """
    A class to encapsulate the functionality of managing apt keys in Debian-based systems.
    It allows users to add or remove apt keys from keyring, optionally downloading them from
    a keyserver or URL, or using local keyfiles.

    Attributes:
        data (str): The keyfile contents to add to the keyring.
        file (str): The path to a keyfile on the remote server to add to the keyring.
        id (str): The identifier of the key.
        keyring (str): The full path to specific keyring file in /etc/apt/trusted.gpg.d/.
        keyserver (str): The keyserver to retrieve key from.
        state (str): Ensures that the key is present (added) or absent (revoked).
        url (str): The URL to retrieve key from.
        validate_certs (bool): If False, SSL certificates for the target url will not be validated.

    **Examples:**

    .. code:: python

        # Add an apt key by id from a keyserver
        r = yield AptKey(keyserver="keyserver.ubuntu.com",
                         id="36A1D7869245C8950F966E92D8576A8BA88D21E9")

        # Add an Apt signing key from URL
        r = yield AptKey(url="https://ftp-master.debian.org/keys/archive-key-6.0.asc",
                         state="present")

        # Remove an Apt specific signing key
        r = yield AptKey(id="0x9FED2BCBDCD29CDF762678CBAED4B06F473041FA",
                         state="absent")

        # Add a key from data content
        key_data = "-----BEGIN PGP PUBLIC KEY BLOCK-----\\n..."
        r = yield AptKey(data=key_data, state="present")

        # Add an Apt signing key to a specific keyring file
        r = yield AptKey(id="9FED2BCBDCD29CDF762678CBAED4B06F473041FA",
                         url="https://ftp-master.debian.org/keys/archive-key-6.0.asc",
                         keyring="/etc/apt/trusted.gpg.d/debian.gpg")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - The apt-key command used by this implementation has been deprecated.
        - Adding a new key requires an apt cache update.
        - Use full fingerprint (40 characters) key ids to avoid key collisions.
    """

    def __init__(self,
                 data: str = None,
                 file: str = None,
                 id: str = None,
                 keyring: str = None,
                 keyserver: str = None,
                 state: str = "present",
                 url: str = None,
                 validate_certs: bool = True,
                 guard: bool = True,
                 sudo: bool = True):  # apt-key typically requires sudo

        self.data = data
        self.file = file
        self.id = id
        self.keyring = keyring
        self.keyserver = keyserver
        self.state = state
        self.url = url
        self.validate_certs = validate_certs
        self.guard = guard
        self.sudo = sudo

    def __repr__(self):
        return (f"AptKey(data={self.data!r}, "
                f"file={self.file!r}, "
                f"id={self.id!r}, "
                f"keyring={self.keyring!r}, "
                f"keyserver={self.keyserver!r}, "
                f"state={self.state!r}, "
                f"url={self.url!r}, "
                f"validate_certs={self.validate_certs!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r})")

    def execute(self):
        # Build the apt-key command
        cmd_parts = ["apt-key"]

        if self.keyring:
            cmd_parts.extend(["--keyring", self.keyring])

        if self.state == "absent":
            if not self.id:
                raise ValueError("id parameter is required when state is absent")
            cmd_parts.extend(["del", self.id])
        else:  # state == "present"
            if self.data:
                # For data, we need to echo it and pipe to apt-key
                cmd_parts = ["echo", repr(self.data), "|"] + cmd_parts + ["add", "-"]
            elif self.file:
                cmd_parts.extend(["add", self.file])
            elif self.keyserver and self.id:
                cmd_parts.extend(["adv", "--keyserver", self.keyserver, "--recv-keys", self.id])
            elif self.url:
                # Download and add from URL
                cmd_parts.extend(["adv", "--fetch-keys", self.url])
            elif self.id and not self.keyserver:
                # If only ID is provided, try to receive from default keyserver
                cmd_parts.extend(["adv", "--recv-keys", self.id])
            else:
                raise ValueError("Insufficient parameters to add key. Provide data, file, keyserver+id, or url.")

        cmd = " ".join(cmd_parts)
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo)
        r.changed = True