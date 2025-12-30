# common/router_handler.py
from typing import Type, Any, Callable
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ValidationError
from reemote.models import CommonModel, commonmodel
from reemote.execute import endpoint_execute


def router_handler(
    model: Type[BaseModel],
    command_class: Type,
    # response_type: Any = None,
    **query_param_definitions,
) -> Callable:
    """Create a standardized router handler function"""

    async def handler(
        common: CommonModel = Depends(commonmodel), **kwargs
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
        responses = await endpoint_execute(lambda: command_class(**all_data))

        # Validate and return responses
        # validated_responses = await validate_responses(responses)
        # validated_responses = [response for response in responses if response is not None]

        # if response_type:
        #     return [response_type.from_response(response) for response in validated_responses]

        # list_of_dicts = [response.dict() for response in validated_responses]
        #
        # keys_to_exclude = {"callback_str", "command", "caller_str", "name", "group", "type", "host_info", "global_info",
        #                    "env", "subsystem", "exit_signal", "exit_status", "stdout_bytes", "stderr_bytes"}
        #
        # return [
        #     {k: v for k, v in item.items() if k not in keys_to_exclude}
        #     for item in list_of_dicts
        # ]
        return responses

    return handler
