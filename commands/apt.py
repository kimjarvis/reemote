from typing import Any, Dict
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from common import CommonParams, common_params

router = APIRouter()

class Install_remove(BaseModel):
    packages: list[str]

def validate_command_apt_install(common: CommonParams = Depends(common_params), **kwargs: Any) -> dict[str, Any]:
    try:
        # Combine common parameters with additional kwargs
        common = {**common.model_dump(), **kwargs}

        # Validate the inputs using the model
        parms = Install_remove(**common)
        return {"valid": True, "data": parms.model_dump(), "cmd": f"apt install -y {parms.packages}"}
    except ValidationError as e:
        # Return validation errors if inputs are invalid
        return {"valid": False, "errors": e.errors()}


@router.get("/install/", tags=["APT Commands"],)
def commands_apt_install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> dict[str, Any]:
    # Call the validation function with shared parameters and additional kwargs
    result = validate_command_apt_install(common=common, packages=packages)

    # Return the result
    return result

def install(**kwargs: Any) -> str | None:
    response = validate_command_apt_install(**kwargs)
    return response.get('cmd')

def validate_command_apt_remove(common: CommonParams = Depends(common_params), **kwargs: Any) -> dict[str, Any]:
    try:
        # Combine common parameters with additional kwargs
        common = {**common.model_dump(), **kwargs}

        # Validate the inputs using the model
        parms = Install_remove(**common)
        return {"valid": True, "data": parms.model_dump(), "cmd": f"apt remove -y {parms.packages}"}
    except ValidationError as e:
        # Return validation errors if inputs are invalid
        return {"valid": False, "errors": e.errors()}

@router.get("/remove/",tags=["APT Commands"],)
def commands_apt_remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> dict[str, Any]:
    # Call the validation function with shared parameters and additional kwargs
    result = validate_command_apt_remove(common=common, packages=packages)

    # Return the result
    return result


def remove(common: dict, **kwargs: Any) -> str | None:
    response = validate_command_apt_remove(common, **kwargs)
    return response.get('cmd')