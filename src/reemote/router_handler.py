from typing import Type, Any, Callable
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ValidationError
from reemote.operation import CommonOperationRequest, common_operation_request
from reemote.execute import endpoint_execute


def router_handler(
    model: Type[BaseModel],
    command_class: Type,
) -> Callable:
    async def handler(
        common: CommonOperationRequest = Depends(common_operation_request), **kwargs
    ) -> list[Any]:
        # Inline logic for _process_common_arguments
        if common is None:
            common_dict = {}
        elif isinstance(common, BaseModel):
            common_dict = common.model_dump()
        elif isinstance(common, dict):
            common_dict = common
        else:
            raise TypeError("`common` must be a CommonParams instance, dict, or None")

        all_arguments = {**common_dict, **kwargs}

        # Inline logic for _validate_and_execute
        try:
            model(**all_arguments)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors())

        # Execute the command with the validated data
        responses = await endpoint_execute(lambda: command_class(**all_arguments))
        return responses

    return handler


def router_handler1(
    command_class: Type,
) -> Callable:
    async def handler(
        common: CommonOperationRequest = Depends(common_operation_request), **kwargs
    ) -> list[Any]:
        # Inline logic for _process_common_arguments
        if common is None:
            common_dict = {}
        elif isinstance(common, BaseModel):
            common_dict = common.model_dump()
        elif isinstance(common, dict):
            common_dict = common
        else:
            raise TypeError("`common` must be a CommonParams instance, dict, or None")

        all_arguments = {**common_dict, **kwargs}

        # Inline logic for _validate_and_execute
        try:
            command_class.request_schema(**all_arguments)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors())

        # Execute the command with the validated data
        responses = await endpoint_execute(lambda: command_class(**all_arguments))
        return responses

    return handler
