from typing import Any, Union, Dict, Optional
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from common import CommonParams, common_params
from command import Command
from utilities.kwargs_to_string import kwargs_to_string

router = APIRouter()

class ShellModel(BaseModel):
    cmd: str

from typing import Any, Union, Dict
from pydantic import BaseModel


def validate_operations_server_shell(
        common: Optional[Union[CommonParams, Dict[str, Any]]] = None,
        **kwargs: Any
) -> Dict[str, Any]:
    """
    Alternative explicit version with clear parameter separation
    """
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

    try:
        parms = ShellModel(**all_data)
        return {
            "valid": True,
            "data": parms.model_dump(),
            "cmd": parms.cmd,
        }
    except ValidationError as e:
        return {
            "valid": False,
            "errors": e.errors(),
        }


@router.get("/shell/", tags=["Server Commands"])
def commands_server_shell(
        cmd: str = Query(..., description="Shell command"),
        common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> dict[str, Any]:
    # Call the validation function (using explicit version)
    result = validate_operations_server_shell(common=common, cmd=cmd)

    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result["errors"])

    return result

class Shell():
    def __init__(self, **kwargs: Any):
        response = validate_operations_server_shell(**kwargs)
        if response["valid"]:
            extra_kwargs = {k: v for k, v in kwargs.items() if k not in ShellModel.__fields__}
            self.command=Command(
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