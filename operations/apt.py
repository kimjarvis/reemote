from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from common import CommonParams, common_params

router = APIRouter()

class Packages(BaseModel):
    packages: list[str]
    present: bool

def validate_operations_apt_packages(common: CommonParams = Depends(common_params), **kwargs):
    try:
        # Combine common parameters with additional kwargs
        common = {**common.model_dump(), **kwargs}

        # Validate the inputs using the model
        parms = Packages(**common)
        return {"valid": True, "data": parms.model_dump()}
    except ValidationError as e:
        # Return validation errors if inputs are invalid
        return {"valid": False, "errors": e.errors()}


@router.get("/packages/", tags=["APT Operations"],)
def operations_apt_packages(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(..., description="Whether the packages are present"),
    common: CommonParams = Depends(common_params)  # Inject shared parameters
):
    # Call the validation function with shared parameters and additional kwargs
    result = validate_operations_apt_packages(common=common, packages=packages, present=present)

    # Return the result
    return result


