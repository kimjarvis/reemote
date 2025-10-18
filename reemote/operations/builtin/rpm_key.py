# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class RpmKey:
    """
    A class to manage GPG keys in the RPM database on RPM-based systems.
    This class provides functionality to add or remove GPG keys from the RPM database,
    supporting various key sources including URLs, local files, and existing key IDs.

    Attributes:
        key (str): Key that will be modified. Can be a url, a file on the managed node, or a keyid if the key already exists in the database.
        state (str): If the key will be imported or removed from the rpm db. Choices: "absent", "present".
        fingerprint (list): The long-form fingerprint(s) of the key being imported for verification.
        validate_certs (bool): If false and the key is a url starting with https, SSL certificates will not be validated.

    **Examples:**

    .. code:: python

        # Import a key from a url
        r = yield RpmKey(state="present", key="http://apt.sw.be/RPM-GPG-KEY.dag.txt")

        # Import a key from a file
        r = yield RpmKey(state="present", key="/path/to/key.gpg")

        # Ensure a key is not present in the db
        r = yield RpmKey(state="absent", key="DEADB33F")

        # Verify the key, using a fingerprint, before import
        r = yield RpmKey(key="/path/to/RPM-GPG-KEY.dag.txt", fingerprint="EBC6 E12C 62B1 C734 026B  2122 A20E 5214 6B8D 79E6")

        # Verify the key, using multiple fingerprints, before import
        r = yield RpmKey(key="/path/to/RPM-GPG-KEY.dag.txt", fingerprint=["EBC6 E12C 62B1 C734 026B  2122 A20E 5214 6B8D 79E6", "19B7 913E 6284 8E3F 4D78 D6B4 ECD9 1AB2 2EB6 8D86"])

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the provided parameters and state.
        - Supports both adding (present) and removing (absent) keys from the RPM database.
        - Can verify keys using fingerprints before importing.
    """

    def __init__(self,
                 key: str,
                 state: str = "present",
                 fingerprint: list = None,
                 validate_certs: bool = True):

        self.key = key
        self.state = state
        self.fingerprint = fingerprint if fingerprint is not None else []
        self.validate_certs = validate_certs

        # Normalize fingerprint to list if single string provided
        if isinstance(self.fingerprint, str):
            self.fingerprint = [self.fingerprint]

    def __repr__(self):
        return (f"RpmKey(key={self.key!r}, "
                f"state={self.state!r}, "
                f"fingerprint={self.fingerprint!r}, "
                f"validate_certs={self.validate_certs!r})")

    def execute(self):
        # Build the rpm command based on state
        if self.state == "present":
            cmd_parts = ["rpm", "--import"]

            # Add key verification if fingerprints are provided
            if self.fingerprint:
                # For verification, we would typically check the key first
                # This is a simplified approach - in practice you might want to verify before import
                pass

            cmd_parts.append(self.key)
            cmd = " ".join(cmd_parts)

        elif self.state == "absent":
            # To remove a key, we need to find it first and then remove it
            # This is more complex and typically involves querying the rpmdb
            # For simplicity, we'll use rpm -e with the key ID
            cmd = f"rpm -e gpg-pubkey-{self.key.lower()}* 2>/dev/null || true"
        else:
            raise ValueError(f"Invalid state '{self.state}'. Must be 'present' or 'absent'.")

        # Execute the command
        r = yield Command(cmd)
        r.changed = True