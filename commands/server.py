from typing import Any, AsyncGenerator
from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel
from common import CommonParams, common_params
from utilities.validate_responses import Response, validate_responses
from command import Command
from result import Result
from utilities.validate_parameters import validate_parameters
from execute import execute
from inventory import get_inventory
from utilities.normalise_common import normalise_common


router = APIRouter()


class ShellModel(BaseModel):
    cmd: str


class Shell():
    def __init__(self, **kwargs: Any):
        response = validate_parameters(ShellModel, **kwargs)
        if response["valid"]:
            # Get extra kwargs (those not in ShellModel's fields)
            self.extra_kwargs = {k: v for k, v in kwargs.items() if k not in ShellModel.__fields__}
            self.cmd = response["data"]["cmd"]
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Shell validation failed: {response['errors']}")

    async def execute(self) -> AsyncGenerator[Command, Result]: 
        """Async generator that yields a Command."""
        # Yield the Command for execution and receive the result when resumed
        result = yield Command(
                command=self.cmd,
                **self.extra_kwargs
            )


        # When we resume, mark the result as changed
        if result and hasattr(result, 'changed'):
            result.changed = True

        # End the async generator without returning a value
        return


@router.get("/shell/", tags=["Server Commands"])
async def commands_server_shell(
        cmd: str = Query(..., description="Shell command"),
        common: CommonParams = Depends(common_params)
) -> list[Response]:
    """Direct implementation of router logic for shell commands."""

    # Validate parameters
    result = validate_parameters(ShellModel, common=common, cmd=cmd)

    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result["errors"])

    # Get inventory
    inventory = get_inventory()
    common_dict = await normalise_common(common)

    # Prepare all data
    all_data = {"cmd": cmd, **common_dict}

    # Execute the command
    responses = await execute(inventory, lambda: Shell(**all_data))

    # Validate and return responses
    validated_responses = await validate_responses(responses)
    return [response.dict() for response in validated_responses]
