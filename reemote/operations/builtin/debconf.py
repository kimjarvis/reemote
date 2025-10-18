# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Debconf:
    """
    A class to configure a .deb package using debconf-set-selections or query existing selections.

    This class allows users to pre-configure Debian packages by setting debconf database values,
    which is commonly used before package installation. It supports various value types including
    passwords, multiselect, boolean, etc.

    Attributes:
        name (str): Name of package to configure.
        question (str, optional): A debconf configuration setting/question.
        value (any, optional): Value to set the configuration to.
        vtype (str, optional): The type of the value supplied.
        unseen (bool): Do not set seen flag when pre-seeding.

    Examples:
        .. code:: python

            # Set default locale to fr_FR.UTF-8
            r = yield Debconf(
                name="locales",
                question="locales/default_environment_locale",
                value="fr_FR.UTF-8",
                vtype="select"
            )

            # Set to generate locales
            r = yield Debconf(
                name="locales",
                question="locales/locales_to_be_generated",
                value="en_US.UTF-8 UTF-8, fr_FR.UTF-8 UTF-8",
                vtype="multiselect"
            )

            # Accept oracle license
            r = yield Debconf(
                name="oracle-java7-installer",
                question="shared/accepted-oracle-license-v1-1",
                value=True,
                vtype="select"
            )

            # Query existing selections for a package
            r = yield Debconf(name="tzdata")

            # Pre-configure tripwire site passphrase
            r = yield Debconf(
                name="tripwire",
                question="tripwire/site-passphrase",
                value="my_secret_passphrase",
                vtype="password"
            )

    Notes:
        - This class requires the debconf and debconf-utils packages to be installed on the target system.
        - For sensitive data like passwords, ensure the task uses appropriate security measures.
        - This module only updates the debconf database; an additional step is needed to reconfigure the package.
        - Setting passwords may always show as changed due to debconf-get-selections masking passwords.

    """

    VALID_VTYPES = [
        "boolean", "error", "multiselect", "note",
        "password", "seen", "select", "string",
        "text", "title"
    ]

    def __init__(self,
                 name: str,
                 question: str = None,
                 value=None,
                 vtype: str = None,
                 unseen: bool = False):
        """
        Initialize a Debconf instance.

        Args:
            name (str): Name of package to configure (required).
            question (str, optional): A debconf configuration setting/question.
            value (any, optional): Value to set the configuration to.
            vtype (str, optional): The type of the value supplied.
            unseen (bool, optional): Do not set seen flag when pre-seeding. Defaults to False.
        """
        self.name = name
        self.question = question
        self.value = value
        self.vtype = vtype
        self.unseen = unseen

        # Validate vtype if provided
        if self.vtype and self.vtype not in self.VALID_VTYPES:
            raise ValueError(f"Invalid vtype '{self.vtype}'. Must be one of {self.VALID_VTYPES}")

    def __repr__(self):
        return (f"Debconf(name={self.name!r}, "
                f"question={self.question!r}, "
                f"value={self.value!r}, "
                f"vtype={self.vtype!r}, "
                f"unseen={self.unseen!r})")

    def _build_command(self) -> str:
        """
        Build the debconf-set-selections command string.

        Returns:
            str: The complete command string.
        """
        if not self.question or self.value is None or not self.vtype:
            # If missing required parameters, just check/show existing config
            return f"debconf-show {self.name}"

        # Format the value appropriately
        if isinstance(self.value, bool):
            formatted_value = "true" if self.value else "false"
        elif isinstance(self.value, list) and self.vtype == "multiselect":
            formatted_value = ", ".join(str(v) for v in self.value)
        else:
            formatted_value = str(self.value)

        # Escape special characters in value
        escaped_value = formatted_value.replace("'", "'\"'\"'")

        line = f"{self.name} {self.question} {self.vtype} '{escaped_value}'"
        cmd = f"echo '{line}' | debconf-set-selections"

        if self.unseen:
            cmd += " -u"

        return cmd

    def execute(self):
        """
        Execute the debconf configuration command.

        Yields:
            Command: A Command object for execution.
        """
        cmd = self._build_command()
        r = yield Command(cmd)
        r.changed = True