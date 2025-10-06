Creating files and directory structures on the server
=====================================================

Create a file hello.txt containing "Hello World!" on the server.

.. code-block::

    reemote -i ~/reemote/inventory-proxmox-alpine.py \
        -s ~/reemote/reemote/operations/sftp/write_file.py \
        -c Write_file \
        -k "path='hello.txt',text='Hello World!'"

Upload the file /home/kim/hfs/example.txt to a file /home/kim/example.txt on the server.

.. code-block::

    reemote -i ~/reemote/inventory-proxmox-alpine.py \
        -s ~/reemote/reemote/operations/scp/upload.py \
        -c Upload \
        -k "srcpaths='/home/kim/hfs/example.txt',dstpath='/home/kim/',recurse=True"

Upload a directory /home/kim/hfs recursive, to the server.

.. code-block::

    reemote -i ~/reemote/inventory-proxmox-alpine.py \
        -s ~/reemote/reemote/operations/scp/upload.py \
        -c Upload \
        -k "srcpaths='/home/kim/hfs',dstpath='/home/kim/',recurse=True"

