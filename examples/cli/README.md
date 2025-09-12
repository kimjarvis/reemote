# Command Line Interface

Makes a directory on all the servers listed in inventory.py.

Copy `example_inventory.py` to your home directory and modify it with the credentials of the target host.

Example output:

```
reemote --cli -i ~/inventory.py -s examples/cli/make_directory.py -c Make_directory

+---------------------------------------------------------------------+------------------+
| Command                                                             | 192.168.122.47   |
+=====================================================================+==================+
| >>>> Directory(path='/tmp/mydir', present=True,sudo=False, su=True) | True             |
+---------------------------------------------------------------------+------------------+
| [ -d /tmp/mydir ]                                                   | False            |
+---------------------------------------------------------------------+------------------+
| mkdir -p /tmp/mydir                                                 | True             |
+---------------------------------------------------------------------+------------------+
```