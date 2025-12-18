# common/router_utils.py
from typing import Type, Any, Callable
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from reemote.common_model import CommonModel, common_params
from reemote.execute import execute
from reemote.response import validate_responses
from reemote.validate_parameters import validate_parameters


def create_router_handler(
        model: Type[BaseModel],
        command_class: Type,
        response_type: Any = None,
        **query_param_definitions
) -> Callable:
    """Create a standardized router handler function"""

    async def handler(
            common: CommonModel = Depends(common_params),
            **kwargs
    ) -> list[Any]:
        # Validate parameters
        result = validate_parameters(model, common=common, **kwargs)

        if not result["valid"]:
            raise HTTPException(status_code=422, detail=result["errors"])

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
        responses = await execute(lambda: command_class(**all_data))

        # Validate and return responses
        validated_responses = await validate_responses(responses)

        if response_type:
            return [response_type.from_response(response) for response in validated_responses]

        return [response.dict() for response in validated_responses]

    return handler

