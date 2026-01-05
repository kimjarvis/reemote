from typing import Type, Any, Callable, List, Dict
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ValidationError
from reemote.core.models import RemoteModel, remotemodel
from reemote.execute import endpoint_execute


def router_handler(
    model: Type[BaseModel],
    command_class: Type,
) -> Callable:
    async def handler(
        common: RemoteModel = Depends(remotemodel), **kwargs
    ) -> list[Any]:
        # Inline _process_common_arguments logic
        if common is None:
            common_dict = {}
        elif isinstance(common, BaseModel):
            common_dict = common.model_dump()
        elif isinstance(common, dict):
            common_dict = common
        else:
            raise TypeError("`common` must be a CommonParams instance, dict, or None")

        all_arguments = {**common_dict, **kwargs}

        # Inline _validate_and_execute logic
        try:
            model(**all_arguments)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors())

        # Execute the command with the validated data
        responses = await endpoint_execute(lambda: command_class(**all_arguments))

        # Process responses
        out = []
        for r in responses:
            if r["call"].startswith("Return"):
                # Replace "Return" with the name of the command_class type
                r["call"] = r["call"].replace("Return", command_class.__name__, 1)
                out.append(r)

        return out

    return handler