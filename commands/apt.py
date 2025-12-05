from typing import Any, AsyncGenerator
from pydantic import BaseModel
from inventory import get_inventory
from execute import execute
from utilities.validate_parameters import validate_parameters
from fastapi import APIRouter, Query, Depends, HTTPException
from common import CommonParams, common_params
from utilities.validate_responses import Response, validate_responses
from utilities.normalise_common import normalise_common
from result import Result
from command import Command


router = APIRouter()


class InstallModel(BaseModel):
    packages: list[str]

class Install():
    def __init__(self, **kwargs: Any):
        response = validate_parameters(InstallModel, **kwargs)
        if response["valid"]:
            # Get extra kwargs (those not in ShellModel's fields)
            self.extra_kwargs = {k: v for k, v in kwargs.items() if k not in InstallModel.__fields__}
            self.packages = response["data"]["packages"]
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Shell validation failed: {response['errors']}")

    async def execute(self) -> AsyncGenerator[Command, Result]:
        from commands.server import Shell
        result = yield Shell(cmd=f"apt-get install -y {' '.join(self.packages)}",**self.extra_kwargs)

        if result and hasattr(result, 'changed'):
            result.changed = True

        # End the async generator without returning a value
        return


@router.get("/install/", tags=["APT Commands"],)
async def operations_apt_packages(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> list[Response]:
    # Validate parameters
    result = validate_parameters(InstallModel, common=common, packages=packages)

    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result["errors"])

    # Get inventory
    inventory = get_inventory()

    common_dict = await normalise_common(common)

    # Prepare all data
    all_data = {"packages": packages, **common_dict}
    responses = await execute(inventory, lambda: Install(**all_data))
    # Validate and return responses
    validated_responses = await validate_responses(responses)
    return validated_responses


