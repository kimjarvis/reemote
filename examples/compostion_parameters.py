# examples/compostion_parameters.py
import asyncio
from typing import AsyncGenerator
from pydantic import BaseModel, Field

from reemote.execute import execute
from reemote.host import Shell
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.context import Context
from reemote.core.response import Response
from reemote.core.request import Request


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

    class Greet(Request):
        Model = GreetRequest

        async def execute(self) -> AsyncGenerator[Context, Response]:
            model_instance = self.Model.model_validate(self.kwargs)
            yield Shell(cmd=f"echo Hello {model_instance.name}")

    responses = await execute(lambda: Greet(name="Joe"), inventory=inventory)
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
