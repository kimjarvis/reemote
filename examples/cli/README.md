# Command Line Interface

Run a script from the command line.  The file source.py contains the class Make_directory.  This makes a directory
on all the servers listed in inventory.py.

## Example 

```bash
 python3 examples/cli/main.py ~/inventory.py examples/cli/create_tmp_mydir.py Make_directory
+---------------------+-------------------+-----------------+
| Command             | 192.168.122.197   | 192.168.122.7   |
+=====================+===================+=================+
| [ -d /tmp/mydir ]   | False             | False           |
+---------------------+-------------------+-----------------+
| mkdir -p /tmp/mydir | True              | True            |
+---------------------+-------------------+-----------------+
| rmdir -p /tmp/mydir | True              | True            |
+---------------------+-------------------+-----------------+
```