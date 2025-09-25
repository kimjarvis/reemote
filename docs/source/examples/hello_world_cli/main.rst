Hello World CLI Example
-----------------------

We are going to connect to localhost using ssh and then execute a command using Reemote.

Check out the Reemote git repo.

.. code-block:: bash

   git clone git@github.com:kimjarvis/reemote.git
   cd reemote

Modify the file localhost.py to add your user name and your password.  This is an inventory file.
It describes the hosts on which the Reemote command will run.  In this case its localhost.

.. code-block:: bash

    from typing import List, Tuple, Dict, Any

    def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
        return [({'host': 'localhost',        # Host
                  'username': 'youruser',     # User name
                  'password': 'yourpassword'  # Password
        },{})]

Now we can now execute a class using the Reemote CLI.

.. code-block:: bash

     reemote -i ~/localhost.py -s examples/hello_world_cli/main.py -c Hello_world

This executes the following class in main.py

.. code-block:: bash

    class Hello_world:
        def execute(self):
            r = yield Shell("echo Hello World!")
            print(r.cp.stdout)

You should see the output of the command and an execution report.

.. code-block:: bash

    +-------------------+----------------------+---------------------+
    | Command           | localhost Executed   | localhost Changed   |
    +===================+======================+=====================+
    | echo Hello World! | True                 | True                |
    +-------------------+----------------------+---------------------+
