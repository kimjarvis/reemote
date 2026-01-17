# examples/compostion_parameters.py
import asyncio
from typing import AsyncGenerator
from pydantic import Field

from reemote.execute import execute
from reemote.core import GetFact
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.context import Context
from reemote.response import ResponseModel
from reemote.operation import Operation, CommonOperationRequest
from setup_logging import setup_logging

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


    class Greet(Operation):
        class GreetRequest(CommonOperationRequest):
            name: str = Field(
                default="Nobody", description="The name of the person to greet"
            )

        async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
            model_instance = self.GreetRequest.model_validate(self.kwargs)
            yield GetFact(cmd=f"echo Hello {model_instance.name}")


    setup_logging()

    responses = await execute(lambda: Greet(), inventory=inventory)
    for response in responses:
        print(response["value"]["stdout"])


    responses = await execute(lambda: Greet(name="Joe"), inventory=inventory)
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
