from typing import Any
from pydantic import BaseModel
from inventory import get_inventory
from execute import execute
from utilities.validate_parameters1 import validate_parameters1
from fastapi import APIRouter, Query, Depends, HTTPException
from common import CommonParams, common_params
from utilities.validate_responses import Response, validate_responses

class InstallModel(BaseModel):
    packages: list[str]

class Install():
    def __init__(self, **kwargs: Any):
        response = validate_parameters1(InstallModel, **kwargs)
        if response["valid"]:
            # Get extra kwargs (those not in ShellModel's fields)
            self.extra_kwargs = {k: v for k, v in kwargs.items() if k not in InstallModel.__fields__}
            self.packages = response["data"]["packages"]
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Shell validation failed: {response['errors']}")

    async def execute(self):
        from commands.server import Shell
        r = yield Shell(cmd=f"apt-get install -y {' '.join(self.packages)}",**self.extra_kwargs)

router = APIRouter()

@router.get("/install/", tags=["APT Commands"],)
async def operations_apt_packages(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> list[Response]:
    # Get inventory
    inventory = get_inventory()

    # Normalize common to dict
    if common is None:
        common_dict = {}
    elif isinstance(common, BaseModel):
        common_dict = common.model_dump()
    elif isinstance(common, dict):
        common_dict = common
    else:
        raise TypeError("`common` must be a CommonParams instance, dict, or None")

    # Prepare all data
    all_data = {"packages": packages, **common_dict}
    responses = await execute(inventory, lambda: Install(**all_data))
    # Validate and return responses
    validated_responses = await validate_responses(responses)
    return validated_responses
