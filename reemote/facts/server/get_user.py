# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#

class Get_User:
    """
    Returns the name of the current user.

    **Examples:**

    .. code:: python

        yield Get_user()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("whoami")

