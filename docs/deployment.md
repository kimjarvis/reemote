## An example deployment

Reemote is an API for controlling remote systems.  Lets look at an example deployment in which the command `echo Hello World!` is executed on two servers and the output is printed on the console.

```python
# block insert examples/hello_world.generated
```

This prints the following to the console

```bash
Hello World!
Hello World!
```

To convince yourself that the commands are actually being executed on both hosts change the command to `ip addr`.  You should see the IP address of both hosts printed.

The `main` function is run by the `asyncio` execution loop.  All Reemote deployments use this construction.  Don't worry if you are not familiar with asyncio, this is just a pattern to follow.

The body of the `main` function defines an inventory that contains two hosts `server104` and `server105`.  It provides ssh connection details for each host.  The [[inventory]] document describes the inventory in detail.

The body of the `main` function contains a call to the `execute` function.  The arguments are the `Shell` constructor and the inventory.  Again, don't worry if you are not familiar with `await` or lambda functions.  What matters here is that `Shell` is passed as a parameter to execute and execute returns a response dictionary with the result of the execution of `Shell` on each host.  

Reemote deployments consist of an inventory, an asynchronous main function one or more calls to the execute function and additional code to prepare the commands to execute and to interpret the responses.

## Interpreting the documentation

### API Requests

You can access the documentation either online at or 
by running the API server, as described in the [[installation]] section.

The documentation applies to both the python and the REST API.  The interfaces are semantically identical.  

Lets examine the description of the `Shell` request.

![[Screenshot_20260107_212807.png]]

The query parameters descriptions apply to both the python `Shell` class constructor and the REST API request.  

Example, of passing a Shell constructor to execute:

```python 
Shell(name="print hello", cmd="echo Hello World!")
```

The equivalent REST API request:

```bash
curl -X 'POST' \
  'http://127.0.0.1:8001/reemote/host/shell?cmd=echo%20%22Hello%20World%22&name=print%20hello' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "connection": {},
  "session": {}
}'
```

Shell is a POST operation.  POST operations sends data to the server to create or update a resource. It is not idempotent, repeated requests may create multiple resources.
### API Responses

Lets examine the description of the Shell command response.

![[Screenshot_20260107_214227.png]]

We can modify the `hello_world` deployment to format and print the responses.

```python
    # examples/hello_world_response.py
    responses = await execute(
        lambda: Shell(cmd="echo Hello World!"), inventory=inventory
    )
    # Convert to formatted JSON
    import json
    formatted_json = json.dumps(responses, indent=4)
    print(formatted_json)
```

This is the formatted response.  The REST API curl request and the python program both produce the same output.

```json
[
    {
        "host": "server104",
        "value": {
            "command": "echo Hello World!",
            "subsystem": null,
            "exit_status": 0,
            "exit_signal": null,
            "returncode": 0,
            "stdout": "Hello World!\n",
            "stderr": ""
        },
        "changed": true,
        "error": false
    },
    {
        "host": "server105",
        "value": {
            "command": "echo Hello World!",
            "subsystem": null,
            "exit_status": 0,
            "exit_signal": null,
            "returncode": 0,
            "stdout": "Hello World!\n",
            "stderr": ""
        },
        "changed": true,
        "error": false
    }
]
```

Responses always include a field with the host's name or address where the request was executed. Additionally, they contain a `value`, a `changed` indicator, and an `error` indicator. The execution of `Shell` consistently returns a changed field set to True, even when the executed command (e.g., `echo`) does not modify the host system. The error indicator signals whether an SSH connection encountered an error connecting to the host.  If the error field is True then the `value` field will provide a string describing the specific error type.
### Composition

Lets look at an example deployment in which commands are composed.

```python
# examples/composition.py
import asyncio
from reemote.execute import execute
from reemote.core import GetFact
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.core import return_put


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
            ),
        ]
    )

    class Child:
        async def execute(self):
            yield GetFact(cmd="echo Hello")

    class Root:
        async def execute(self):
            hello_response = yield Child()
            world_response = yield GetFact(cmd="echo World!")
            yield return_put(value=hello_response["value"]["stdout"] + world_response["value"]["stdout"])

    responses = await execute(lambda: Root(), inventory=inventory)
    for response in responses:
        print(response["value"])


if __name__ == "__main__":
    asyncio.run(main())
```

This prints the following to the console:

```bash
Hello
World!
```

This deployment follows the same pattern as the previous example.  It has an asynchronous `main` function and it passes an inventory to the `execute` function.  

The `Root` constructor is passed to the `execute` function.  The `Root` class implements an asynchronous `execute` method.  When the deployment runs this method is executed on all of the hosts in the inventory.  The method yields the `Child` constructor and then yields the `Shell` constructor.  This executes the shell command `echo World` on all of the hosts in the inventory.

The `Child` class has an `execute` method that yields a `Shell` constructor.  This executes the shell command `echo Hello` on all of the hosts in the inventory.

The `Child` class could yield additional class constructors and their execute methods, in turn, could yield constructors and so on.  This is how classes in Reemote are composed.  For example, the `Shell` class contains an execute method that runs on all of the hosts in the inventory.

The `execute` method yields its last response.  So, class `Child` yields the response from `Shell`.  Class `Root` yields the response from `Return`.   The `Return` class simply yields the value of its `value` parameter.

In this example `Root()` has no parameters.  In the next section we will describe how to pass parameters.
### Parameters

Lets look at an example deployment with a custom class `Greet` that has a parameter `name`.

```python
# examples/compostion_parameters.py
import asyncio
from typing import AsyncGenerator
from pydantic import BaseModel, Field

from reemote.execute import execute
from reemote.core import GetFact
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.context import Context
from reemote.response import ResponseModel
from reemote.operation import Operation


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
            ),
        ]
    )

    class GreetRequest(BaseModel):
        name: str = Field(
            default="no one", description="The name of the person to greet"
        )

    class Greet(Operation):
        request_model = GreetRequest

        async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
            model_instance = self.Model.model_validate(self.kwargs)
            yield GetFact(cmd=f"echo Hello {model_instance.name}")

    responses = await execute(lambda: Greet(name="Joe"), inventory=inventory)
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
```

This prints the following to the console:

```bash
Hello Joe
```

This deployment follows the same pattern as the previous example.  It implements a class `GreetRequest` that defines the model for parameter validation.  Reemote use the [Pydantic](https://github.com/pydantic/pydantic) module for parameter validation.  

The `Greet` class inherits from the class `Reemote`.  The `Remote` class handles execution on remote hosts.  The `Greet` class specifies the pydantic validation model in the `Model` field.  It calls the pydantic method `model_validate` to validate its parameters.  The specified parameter `name` is a field of the Model.  This is passed to `Shell` which executes the `echo` command on the remote host.  The responses are collected and printed the console.
### Concurrency

When operations are executed on multiple hosts serially we can be sure that the operation has completed on all hosts before the next one starts.  When operations are executed on multiple hosts concurrently there can be a benefit to application performance.

Lets look at an example that demonstrates both serial and concurrent execution.

```python
# examples/concurrency.py
import asyncio
from reemote.execute import execute
from reemote.core import GetFact
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.core import return_put


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["all"],
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                groups=["all"],
            ),
        ]
    )

    class Root:
        async def execute(self):
            hello_response = yield GetFact(cmd="echo Hello")
            world_response = yield GetFact(cmd="echo World!")
            yield return_put(value=[hello_response, world_response])

    main_responses = await execute(
        lambda: GetFact(cmd="echo Ready?"),
        inventory=inventory,
    )
    for item in main_responses:
        print(item["value"]["stdout"])

    root_responses = await execute(
        lambda: Root(),
        inventory=inventory,
    )
    for response in root_responses:
        for item in response["value"]:
            print(item["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
```

This deployment follows the same pattern as the previous examples. 

The first execute function has the `Shell(cmd="echo Ready?")` parameter.  The execute function waits until a response is returned by both hosts before continuing.

The second execute function has the `Root()` parameter.  The execute function waits until a response is returned by both hosts.

The `execute` method of the `Root` class contains two `Shell` constructors.  On a single host these are executed in the order they appear.  However, the execution on `server104 ` occurs concurrently with the execution on `server105`.

This prints the starting and ending time of the Root execute method ,on both hosts, to the console:

```
(1767892848.3315704, 1767892848.5971832)
(1767892848.3314345, 1767892848.635084)
```

We can see that these time periods overlap, indicating that the `Root` `execute` method is being executed concurrently on the hosts.




