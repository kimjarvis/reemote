from typing import Any, AsyncGenerator

from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel

from command import Command
from common import CommonParams, common_params
from execute import execute
from inventory import get_inventory
from unifiedresult import UnifiedResult, validate_responses
from utilities.normalise_common import normalise_common
from utilities.validate_parameters import validate_parameters

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

    async def execute(self) -> AsyncGenerator[Command, UnifiedResult]:
        from commands.server import Shell
        result = yield Shell(cmd=f"apt-get install -y {' '.join(self.packages)}",**self.extra_kwargs)

        if result and hasattr(result, 'changed'):
            result.changed = True

        # End the async generator without returning a value
        return


@router.get("/install/", tags=["APT Commands"],)
async def operations_apt_packages_install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> list[UnifiedResult]:
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
    return [response.dict() for response in validated_responses]




class RemoveModel(BaseModel):
    packages: list[str]

class Remove():
    def __init__(self, **kwargs: Any):
        response = validate_parameters(RemoveModel, **kwargs)
        if response["valid"]:
            # Get extra kwargs (those not in ShellModel's fields)
            self.extra_kwargs = {k: v for k, v in kwargs.items() if k not in RemoveModel.__fields__}
            self.packages = response["data"]["packages"]
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Shell validation failed: {response['errors']}")

    async def execute(self) -> AsyncGenerator[Command, UnifiedResult]:
        from commands.server import Shell
        result = yield Shell(cmd=f"apt-get remove -y {' '.join(self.packages)}",**self.extra_kwargs)

        if result and hasattr(result, 'changed'):
            result.changed = True

        # End the async generator without returning a value
        return


@router.get("/remove/", tags=["APT Commands"],)
async def operations_apt_packages_remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> list[UnifiedResult]:
    # Validate parameters
    result = validate_parameters(RemoveModel, common=common, packages=packages)

    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result["errors"])

    # Get inventory
    inventory = get_inventory()

    common_dict = await normalise_common(common)

    # Prepare all data
    all_data = {"packages": packages, **common_dict}
    responses = await execute(inventory, lambda: Remove(**all_data))
    # Validate and return responses
    validated_responses = await validate_responses(responses)
    return [response.dict() for response in validated_responses]


