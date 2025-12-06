from typing import Any
from pydantic import BaseModel
from inventory import get_inventory
from execute import execute
from utilities.validate_parameters import validate_parameters
from fastapi import APIRouter, Query, Depends, HTTPException
from common import CommonParams, common_params
from utilities.normalise_common import normalise_common
from facts.parse_apt_list_installed import parse_apt_list_installed

router = APIRouter()


class FactModel(BaseModel):
    pass

class Get_packages():
    def __init__(self, **kwargs: Any):
        response = validate_parameters(FactModel, **kwargs)
        if response["valid"]:
            # Get extra kwargs (those not in ShellModel's fields)
            self.extra_kwargs = {k: v for k, v in kwargs.items() if k not in FactModel.__fields__}
        else:
            print(f"Validation errors: {response['errors']}")
            raise ValueError(f"Shell validation failed: {response['errors']}")

    async def execute(self):
        from commands.server import Shell
        result = yield Shell(cmd=f"apt list --installed | head -10",**self.extra_kwargs)
        result.output = parse_apt_list_installed(result.cp.stdout)
        print(result.output)
        if result and hasattr(result, 'changed'):
            result.changed = True

        # End the async generator without returning a value
        return


@router.get("/get_packages/", tags=["APT Facts"],)
async def facts_apt_packages(
    common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> list[dict]:
    # Validate parameters
    result = validate_parameters(FactModel, common=common)

    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result["errors"])

    # Get inventory
    inventory = get_inventory()

    common_dict = await normalise_common(common)

    # Prepare all data
    all_data = {**common_dict}
    responses = await execute(inventory, lambda: Get_packages(**all_data))
    print(responses)
    # Validate and return responses
    validated_responses = await validate_responses(responses)
    return [response.dict() for response in validated_responses]

