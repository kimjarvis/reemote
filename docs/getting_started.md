# Getting Started

Remote is used for controlling a remote server.  In this tutorial we have two servers "192.168.0.24" and "192.168.1.76" we can access the server, via ssh, using the username "user" and the password "password".

```bash
ssh user@192.168.0.26
```
We can use the `remote` command to connect to the server and execute a shell command.

```python
import asyncio
from reemote.execute import execute
from reemote.api.server import Shell
from reemote.api.inventory  import create_inventory


async def main():
    create_inventory([
        [
            {
                "host": "192.168.1.24",
                "username": "user",
                "password": "password"
            },
            {
                "groups": [
                    "all",
                ],
            }
        ],
        [
            {
                "host": "192.168.1.76",
                "username": "user",
                "password": "password"
            },
            {
                "groups": [
                    "all",
                ],
            }
        ],
    ])

    response = await execute(lambda: Shell(cmd="echo Hello World!"))
    for r in response:
        print(r.stdout)


if __name__ == "__main__":
    asyncio.run(main())
```

This script will execute the command `echo Hello World!` on both of the servers.  The output should be:

```bash
Hello World!
Hello World!
```
The inventory is a list.  There is one entry for each server.  Each entry is a list of two dictionaries.  The first dictionary describes how to connect to a host.  The information 
it contains encoded into a [SSHClientConnectionOptions](https://asyncssh.readthedocs.io/en/latest/_modules/asyncssh/connection.html#SSHClientConnectionOptions) object and passed to asyncssh to make the ssh connection.  
The second dictionary contains information about the server.  The groups parameter is a list of groups that the server belongs to.

The inventory is stored in a file which, by default, is located at `~/.reemote/inventory.yaml`.  The inventory file 
can be edited manually or modified using the RestAPI.  The program can be run again without the call to create_inventory producing the same result as before.

```python
import asyncio
from reemote.execute import execute
from reemote.api.server import Shell
from reemote.api.inventory  import create_inventory


async def main():
    response = await execute(lambda: Shell(cmd="echo Hello World!"))
    for r in response:
        print(r.stdout)


if __name__ == "__main__":
    asyncio.run(main())
```

The execute function executes the shell command on each server in the inventory asynchronously.  It returns a list of responses.  The response object contains the return code, stdout and stderr of the command.

Commands can be composed.   In this example, the order of execution is determined by the order of the yield statements in the two classes.  Execution proceeds asynchronously.

```python
import asyncio
from reemote.execute import execute
from reemote.api.server import Shell
from reemote.api.inventory  import create_inventory


class Message:
    async def execute(self):
        yield Shell(cmd="echo Brave")
        yield Shell(cmd="echo New")


class Root:
    async def execute(self):
        yield Shell(cmd="echo Hello")
        yield Message()
        yield Shell(cmd="echo World!")


async def main():
    response = await execute(lambda: Root())
    for r in response:
        print(r.stdout)


if __name__ == "__main__":
    asyncio.run(main())

```

This script will execute the commands on both of the servers.  The output might be:

```bash
Hello
Brave
New
World!
Hello
Brave
New
World!
```

But, on your system you might find the responses from the two servers interleaved.

This exemple demonstrates synchronous execution.  The order in which the two servers print "Brave New" is not guaranteed. However, both processors must have finished printing "Brave New" before either starts printing "Hello World!"

```python
import asyncio
from reemote.execute import execute
from reemote.api.server import Shell
from reemote.api.inventory  import create_inventory


class Message:
    async def execute(self):
        yield Shell(cmd="echo Brave")
        yield Shell(cmd="echo New")


class Root:
    async def execute(self):
        yield Shell(cmd="echo Hello")
        yield Shell(cmd="echo World!")


async def main():
    response = await execute(lambda: Message())
    for r in response:
        print(r.stdout)
    response = await execute(lambda: Root())
    for r in response:
        print(r.stdout)


if __name__ == "__main__":
    asyncio.run(main())

```

```bash
Brave
New
Brave
New
Hello
World!
Hello
World!
```