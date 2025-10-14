# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#

class Get_KernelVersion:
    """
    Returns the kernel name according to uname -r.

    **Examples:**

    .. code:: python

        yield Get_kernelversion()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -r")

