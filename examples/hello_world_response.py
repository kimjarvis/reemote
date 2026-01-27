import asyncio

from reemote import core
from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                )
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                )
            ),
        ]
    )

    # block extract examples/hello_world_response.py.generated
    # examples/hello_world_response.py
    import json
    responses = await execute(
        lambda: core.get.Fact(cmd="echo Hello World!"), inventory=inventory
    )
    # Print the list of responses from each host in json format
    print(core.get.Fact.responses.model_validate(responses).model_dump_json(indent=4))
    # Print the json schema for the response
    responses_schema = core.get.Fact.responses.model_validate(responses).model_json_schema()
    print(json.dumps(responses_schema, indent=4))
    for response in responses:
        # Print the response data as a dictionary
        print(core.get.Fact.response.model_validate(response).model_dump())
    # block end

if __name__ == "__main__":
    asyncio.run(main())
