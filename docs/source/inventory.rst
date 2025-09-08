Inventory
=========

The ssh connection to reemote hosts is usually defined in an inventory file.
This is an example inventory that contains ssh connection information for one host.

.. code-block:: python

    from typing import List, Tuple, Dict, Any

    def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
        return [
            (
                {
                    'host': '192.168.122.47',     # The host IP
                    'username': 'joe',            # User name
                    'password': 'password'        # Password
                },
                {
                    'su_password': 'password'     # Password
                }
            )
        ]


The inventory() function returns the inventory structure which is a list of tuples.  Each tuple
represents a host.  A host tuple contains two dictionaries.  The first contians ssh connection information that
is passed to the `AsyncSSH Connect API <https://asyncssh.readthedocs.io/en/latest/api.html#asyncssh.connect>`_
The second contains global information that is available in your reemote class. For example, sudo and su passwords.

The inventory.py file may contain sensitive information and is usually added to .gitignore.

The reemote cli takes the inventory file as its first parameter.