from typing import Type, Any, Callable, List, Dict
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ValidationError
from reemote.core.remote import RemoteModel, remotemodel
from reemote.execute import endpoint_execute


def _process_common_arguments(
    common: RemoteModel | None,
) -> Dict[str, Any]:
    """Helper function to process `common` arguments."""
    if common is None:
        return {}
    elif isinstance(common, BaseModel):
        return common.model_dump()
    elif isinstance(common, dict):
        return common
    else:
        raise TypeError("`common` must be a CommonParams instance, dict, or None")


async def _validate_and_execute(
    model: Type[BaseModel],
    command_class: Type,
    all_arguments: Dict[str, Any],
) -> List[Any]:
    """Helper function to validate input and execute the command."""
    try:
        model(**all_arguments)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())

    # Execute the command with the validated data
    responses = await endpoint_execute(lambda: command_class(**all_arguments))
    return responses


def router_handler(
    model: Type[BaseModel],
    command_class: Type,
) -> Callable:
    async def handler(
        common: RemoteModel = Depends(remotemodel), **kwargs
    ) -> list[Any]:
        common_dict = _process_common_arguments(common)
        all_arguments = {**common_dict, **kwargs}
        responses = await _validate_and_execute(model, command_class, all_arguments)
        return responses

    return handler


def router_handler_put(
    model: Type[BaseModel],
    command_class: Type,
) -> Callable:
    async def handler(
        common: RemoteModel = Depends(remotemodel), **kwargs
    ) -> list[Any]:
        common_dict = _process_common_arguments(common)
        all_arguments = {**common_dict, **kwargs}
        responses = await _validate_and_execute(model, command_class, all_arguments)

        # Process responses
        out = []
        for r in responses:
            if r["call"].startswith("Return"):
                # Replace "Return" with the name of the command_class type
                r["call"] = r["call"].replace("Return", command_class.__name__, 1)
                out.append(r)

        return out

    return handler