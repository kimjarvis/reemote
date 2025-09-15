Inventory
=========

Specifying the ssh connection
-----------------------------

Reemote connects to hosts using asyncssh.  The inventory contains the information used to create a ssh connection to reemote hosts.
This is an example inventory that contains ssh connection information for two hosts.

.. code-block:: python

    from typing import List, Tuple, Dict, Any

    def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
         return [
            (
                {
                    'host': '192.168.122.47',  # alpine
                    'username': 'youruser',  # User name
                    'password': 'yourpassword'  # Password
                },
                {
                    'su_user': 'root',
                    'su_password': 'rootuser'  # Password
                }
            ),
            (
                {
                    'host': '192.168.122.24',  # alpine
                    'username': 'youruser',  # User name
                    'password': 'yourpassword'  # Password
                },
                {
                    'su_user': 'root',
                    'su_password': 'rootuser'  # Password
                }
            )
        ]

The inventory() function returns the inventory structure which is a list of tuples.  Each tuple
represents a host.  A host tuple contains two dictionaries.  The first dictonary contains ssh connection information that
is passed to the `AsyncSSH Connect API <https://asyncssh.readthedocs.io/en/latest/api.html#asyncssh.connect>`_.
The second dictonary contains global information that is available in your reemote class. For example, the su password.

Using an inventory file
-----------------------

Inventory is just a python List.  It can be defined inline in code or in a python file.
The inventory may contain sensitive information and best practice is to use a file and add it to .gitignore.
