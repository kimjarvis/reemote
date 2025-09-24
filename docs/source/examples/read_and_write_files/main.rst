.. _read_and_write_files-example:

Read and Write Files Example
----------------------------

This example illustrates how to read from and write to files on all the servers in the inventory.

.. code-block:: bash

    python3 examples/read_and_write_files/main.py

This code reads from and writes to a file on a server.

.. code-block:: python

    yield Operation("Read and Write", composite=True)
    yield Read_file(path='example.txt', hosts=inventory()[0][0]['host'])
    yield Write_file(path='example.txt', text='hello world')
