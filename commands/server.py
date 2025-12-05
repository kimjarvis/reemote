from typing import Any
from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel
from common import CommonParams, common_params
from utilities.validate_responses import Response, validate_responses
import logging
from command import Command
from utilities.validate_parameters import validate_parameters
from execute import execute
from inventory import get_inventory

router = APIRouter()


class ShellModel(BaseModel):
    cmd: str


class Shell():
    def __init__(self, **kwargs: Any):
        # Direct implementation of Base's functionality
        logging.info(f"Shell() __init__ kwargs: {kwargs}")

        # Replicate validation logic from Base
        response = validate_parameters(ShellModel, **kwargs)
        if response["valid"]:
            # Get extra kwargs (those not in ShellModel's fields)
            extra_kwargs = {k: v for k, v in kwargs.items() if k not in ShellModel.__fields__}

            # Create Command instance
            self.command = Command(
                command=response.get('cmd'),
                **extra_kwargs
            )
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Shell validation failed: {response['errors']}")

    async def execute(self):
        """Async generator that yields a Command."""
        # Yield the Command for execution and receive the result when resumed
        result = yield self.command

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

    # Normalize common to dict
    if common is None:
        common_dict = {}
    elif isinstance(common, BaseModel):
        common_dict = common.model_dump()
    elif isinstance(common, dict):
        common_dict = common
    else:
        raise TypeError("`common` must be a CommonParams instance, dict, or None")

    # Prepare all data
    all_data = {"cmd": cmd, **common_dict}
    logging.info(f"commands_server_shell all_data: {all_data}")

    # Execute the command
    responses = await execute(inventory, lambda: Shell(**all_data))
    logging.info(f"commands_server_shell responses: {responses}")

    # Validate and return responses
    validated_responses = await validate_responses(responses)
    return validated_responses