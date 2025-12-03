from typing import Any
from fastapi import FastAPI, Query, Depends, HTTPException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from common import CommonParams, common_params

router = APIRouter()

class Get_packages(BaseModel):
    pass  # No fields to validate for this specific model

def validate_facts_get_packages(common: CommonParams = Depends(common_params)) -> dict[str, Any]:
    try:
        # Validate only the common parameters using the model
        parms = Get_packages(**common.model_dump())
        return {"valid": True, "data": parms.model_dump(), "cmd": f"apt list --installed | head -10"}
    except ValidationError as e:
        # Return validation errors if inputs are invalid
        return {"valid": False, "errors": e.errors()}

@router.get("/get_packages/", tags=["APT Facts"],)
def facts_apt_get_packages(
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> dict[str, Any]:
    # Call the validation function with only the shared parameters
    result = validate_facts_get_packages(common=common)

    # Return the result
    return result