# Command Line Interface

Makes a directory on all the servers listed in inventory.py.

## Example 

```bash
python3 examples/cli/main.py ~/inventory.py examples/cli/create_tmp_mydir.py Make_directory
+------------------------------------------------------------+-------------------+-----------------+
| Command                                                    | 192.168.122.197   | 192.168.122.7   |
+============================================================+===================+=================+
| echo Directory(path='/tmp/mydir', present=True, sudo=True) | False             | False           |
+------------------------------------------------------------+-------------------+-----------------+
| sudo -S [ -d /tmp/mydir ]                                  | False             | False           |
+------------------------------------------------------------+-------------------+-----------------+
| sudo -S mkdir -p /tmp/mydir                                | True              | True            |
+------------------------------------------------------------+-------------------+-----------------+
```