Installing vim on Alpine API Example
------------------------------------

This example installs vim on a server, which is running Alpine, using the apk package manager.

.. code-block:: python

    import asyncio
    from reemote.report import report
    from reemote.run import run

    from reemote.operations.apk.packages import Packages
    from reemote.operations.apk.update import Update

    from typing import List, Tuple, Dict, Any


    def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
        return [({'host': '192.168.122.47',
                  'username': 'youruser',  # User name
                  'password': 'yourpassword'  # Password
                  },{
                  'su_password': 'youruser'})]

    class Install_vim:
        def execute(self):
            r = yield "echo Installing VIM on Alpine!"
            r.changed = False
            yield Update(su=True)
            yield Packages(packages=["vim"], present=True, su=True)


    async def main():
        operations, responses = await run(inventory(), Install_vim())
        print(report(operations, responses))


    if __name__ == "__main__":
        asyncio.run(main())

To run it, spin up an Alpine VM, then modify the IP address,youruser and yourpassword.  You should see:

.. code-block:: bash

    python3 examples/install_vim_on_alpine/main.py

.. code-block:: bash

    +-----------------------------------------------------------------------------------+------------------+
    | Command                                                                           | 192.168.122.47   |
    +===================================================================================+==================+
    | echo Installing VIM on Alpine!                                                    | False            |
    +-----------------------------------------------------------------------------------+------------------+
    | >>>> Update(sudo=False, su=True)                                                  | False            |
    +-----------------------------------------------------------------------------------+------------------+
    | apk info -v                                                                       | False            |
    +-----------------------------------------------------------------------------------+------------------+
    | su -c 'apk update'                                                                | False            |
    +-----------------------------------------------------------------------------------+------------------+
    | apk info -v                                                                       | False            |
    +-----------------------------------------------------------------------------------+------------------+
    | >>>> Packages(packages=['vim'], present=True,repository=None,sudo=False, su=True) | True             |
    +-----------------------------------------------------------------------------------+------------------+
    | apk info -v                                                                       | False            |
    +-----------------------------------------------------------------------------------+------------------+
    | su -c 'apk add vim'                                                               | True             |
    +-----------------------------------------------------------------------------------+------------------+
    | apk info -v                                                                       | False            |
    +-----------------------------------------------------------------------------------+------------------+
    None

The operation Update updates the list of packages on the server.  The command column shows
that the command "apk update" is wrapped by two "apk info" commands.  These allow Update to check for
changes to the installed packages.  Update doesn't change anything so there is
a False in the changed column.  The commadn installs vim.  This command changes the
list of packages on the host.  There is a True in the changed colum on both the Packages command and
the "apk add vim" operation to indicate that the host was changed.
