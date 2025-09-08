Getting started
===============

Hello world example
===================

This is the Hello world example helloworld.py.

.. code-block:: python

    import asyncio
    from reemote.report import report
    from reemote.run import run

    from typing import List, Tuple, Dict, Any

    def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
        return [({'host': 'localhost',
                  'username': 'youruser',  # User name
                  'password': 'yourpassword'  # Password
                  },{})]

    class Hello_world:
        def execute(self):
            r = yield "echo hello world"
            r.changed = False

    async def main():
        operations, responses = await run(inventory(), Hello_world())
        print(report(operations, responses))


    if __name__ == "__main__":
        asyncio.run(main())

It runs echo on the local host.  To run it, modify youruser and yourpassword.  You should see:

.. code-block:: bash

    ❯ python3 helloworld.py
    +------------------+-------------+
    | Command          | localhost   |
    +==================+=============+
    | echo hello world | False       |
    +------------------+-------------+
    None

Inventory is a function that describes the hosts on which the execute function in class Hello_world
runs.  In this case its our localhost.  The yield in execute class in Hello_world describes the
action.  In this case its to echo "hello world".  The echo command does not change the host so the
changed value is set to false.  This is the value shown in hosts column in the output table.  If more commands
are added they appear as rows in the output table.  If another host is added to the inventory it will
appear as another column.

Installing vim on Alpine example
================================

This installs vim on a server 192.168.122.47 which is running Alpine using the apk package manager.

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

It on the Alpine host.  To run it, spin up a VM modify the IP address and youruser and yourpassword.  You should see:

.. code-block:: bash

    ❯ python3 vimonapline.py
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
that the command apk update is wrapped by two apk info commands.  These allow Update to check for
changes to the installed packages.  Update doesn't change anything so there is
a False in the changed column.  The operation Package installs vim.  This function changes the
list of packages on the host.  The changed column is flagged True on both the Packages command and
the apk add vim operation.

