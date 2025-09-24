Hello World API Example
-----------------------

This example echos "Hello world" on the localhost.

.. code-block:: python

    import asyncio
    from reemote.run import run
    from reemote.produce_json import produce_json
    from reemote.produce_table import produce_table
    from reemote.operations.server.shell import Shell

    from typing import List, Tuple, Dict, Any

    def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
        return [({'host': 'localhost',
                  'username': 'youruser',  # User name
                  'password': 'yourpassword'  # Password
                  },{})]

    class Hello_world:
        def execute(self):
            r = yield Shell("echo Hello World!")
            print(r.cp.stdout)

    async def main():
        _, responses = await run(inventory(), Hello_world())
        print(produce_table(produce_json(responses)))


    if __name__ == "__main__":
        asyncio.run(main())

To run it, modify youruser and yourpassword.  You should see:

.. code-block:: bash

    python3 examples/hello_world/main.py

.. code-block:: bash

    +-------------------+----------------------+---------------------+
    | Command           | localhost Executed   | localhost Changed   |
    +===================+======================+=====================+
    | echo Hello World! | True                 | True                |
    +-------------------+----------------------+---------------------+

The True in the "localhost Executed" column indicates that the command was executed.
The True in the "locahost Changed" changed indicates that the host was changed.  The host wasn't changed,
but all Shell commands are assumed to change values on the host.

Inventory is a function that describes the hosts on which the execute function in class Hello_world
runs.  In this case its our localhost.  The yield in the execute method of the class in Hello_world describes the
action.  In this case its to echo "hello world".  When more commands
are added they appear as rows in the output table.  When another host is added to the inventory it will
appear as another column.
