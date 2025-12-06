# common/router_utils.py
from typing import Type, Dict, Any, Callable
from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel
from common_params import CommonParams, common_params
from inventory import get_inventory
from execute import execute
from response import validate_responses
from utilities.validate_parameters import validate_parameters


def create_router_handler(
        model: Type[BaseModel],
        command_class: Type,
        response_type: Any = None,
        **query_param_definitions
) -> Callable:
    """Create a standardized router handler function"""

    async def handler(
            common: CommonParams = Depends(common_params),
            **kwargs
    ) -> list[Any]:
        # Validate parameters
        result = validate_parameters(model, common=common, **kwargs)

        if not result["valid"]:
            raise HTTPException(status_code=422, detail=result["errors"])

        # Get inventory
        inventory = get_inventory()

        # Normalize common to dict (inlined from normalise_common)
        if common is None:
            common_dict = {}
        elif isinstance(common, BaseModel):
            common_dict = common.model_dump()
        elif isinstance(common, dict):
            common_dict = common
        else:
            raise TypeError("`common` must be a CommonParams instance, dict, or None")

        # Prepare all data
        all_data = {**kwargs, **common_dict}

        # Execute the command
        responses = await execute(inventory, lambda: command_class(**all_data))

        # Validate and return responses
        validated_responses = await validate_responses(responses)

        if response_type:
            return [response_type.from_response(response) for response in validated_responses]

        return [response.dict() for response in validated_responses]

    return handler


def register_command(
        router: APIRouter,
        path: str,
        model: Type[BaseModel],
        command_class: Type,
        tags: list[str],
        **query_params
) -> None:
    """Register a command endpoint with a router"""

    # Dynamically create the endpoint handler
    endpoint_func = create_router_handler(model, command_class)

    # Add the endpoint to the router with the query parameters
    router.get(path, tags=tags)(endpoint_func)