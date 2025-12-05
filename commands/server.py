import json
from fastapi import Query, Depends
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from common import CommonParams, common_params
from typing import Any
from utilities.validate_parameters import validate_parameters
from utilities.base import Base
from inventory import get_inventory
from execute import execute
import logging
from response import Response, validate_responses

router = APIRouter()

class ShellModel(BaseModel):
    cmd: str

class Shell(Base):
    def __init__(self, **kwargs: Any):
        logging.info(f"server.py Shell(Base) __init__ {kwargs}")
        super().__init__(ShellModel, **kwargs)

@router.get("/shell/", tags=["Server Commands"])
async def commands_server_shell(
        cmd: str = Query(..., description="Shell command"),
        common: CommonParams = Depends(common_params)  # Inject shared parameters
) -> list[Response]:
    logging.basicConfig(
        level=logging.DEBUG,
        filename="asyncssh_debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logging.info(f"server.py commands_server_shell {cmd} {common}")
    result = validate_parameters(ShellModel,common=common, cmd=cmd)

    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    else:
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

        all_data={"cmd": cmd, **common_dict}
        logging.info(f"server.py commands_server_shell all_data: {all_data}")
        responses = await execute(inventory, lambda: Shell(**all_data))
        logging.info(f"server.py commands_server_shell responses: {responses}")
        validated_responses = await validate_responses(responses)
        return validated_responses

