from typing import Any
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from common import CommonParams, common_params

router = APIRouter()

class Shell(BaseModel):
    cmd: str

def validate_operations_server_shell(common: CommonParams = Depends(common_params), **kwargs: Any) -> dict[str, Any]:
    try:
        # Combine common parameters with additional kwargs
        common = {**common.model_dump(), **kwargs}

        # Validate the inputs using the model
        parms = Shell(**common)
        return {"valid": True, "data": parms.model_dump(), "cmd": f"{parms.cmd}"}
    except ValidationError as e:
        # Return validation errors if inputs are invalid
        return {"valid": False, "errors": e.errors()}

@router.get("/shell/", tags=["Server Commands"],)
def commands_server_shell(
        cmd: str = Query(..., description="Shell command"),
        common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> dict[str, Any]:
    # Call the validation function with only the shared parameters
    result = validate_operations_server_shell(common=common , cmd=cmd)

    # Return the result
    return result

def shell(common: dict, **kwargs: Any) -> str | None:
    response = validate_operations_server_shell(common, **kwargs)
    return response.get('cmd')