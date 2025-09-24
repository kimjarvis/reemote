.. _get_facts-example:

Get Facts Example
-----------------

This example illustrates how to gather facts about all the servers in the inventory.

.. code-block:: bash

    python3 examples/get_facts/main.py

This code gets the currently running OS name from server.

.. code-block:: bash

    from reemote.facts.server.get_os import Get_OS
    print((yield Get_OS()).cp.stdout)