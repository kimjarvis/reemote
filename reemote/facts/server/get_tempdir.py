# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#

class Get_TmpDir:
    """
    Returns the temporary directory of the current server, if configured.

    **Examples:**

    .. code:: python

        yield Get_tmpdir()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("echo ")

