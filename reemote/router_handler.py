# common/router_handler.py
from typing import Type, Any, Callable
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ValidationError
from reemote.models import CommonModel, commonmodel
from reemote.execute import execute
from reemote.response import validate_responses


def router_handler(
        model: Type[BaseModel],
        command_class: Type,
        response_type: Any = None,
        **query_param_definitions
) -> Callable:
    """Create a standardized router handler function"""

    async def handler(
            common: CommonModel = Depends(commonmodel),
            **kwargs
    ) -> list[Any]:
        # Validate parameters (inlined from validate_parameters)
        # Normalize common to dict
        if common is None:
            common_dict = {}
        elif isinstance(common, BaseModel):
            common_dict = common.model_dump()
        elif isinstance(common, dict):
            common_dict = common
        else:
            raise TypeError("`common` must be a CommonParams instance, dict, or None")

        # Merge data (kwargs override common_dict)
        all_data = {**common_dict, **kwargs}

        # Validate using the model
        try:
            parms = model(**all_data)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors())

        # Execute the command with the validated data
        responses = await execute(lambda: command_class(**all_data))

        # Validate and return responses
        validated_responses = await validate_responses(responses)

        if response_type:
            return [response_type.from_response(response) for response in validated_responses]

        return [response.dict() for response in validated_responses]

    return handler