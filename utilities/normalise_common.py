from typing import Any
from pydantic import BaseModel
from inventory import get_inventory
from execute import execute
from utilities.validate_parameters import validate_parameters
from fastapi import APIRouter, Query, Depends, HTTPException
from common import CommonParams, common_params
from utilities.validate_responses import Response, validate_responses

async def normalise_common(common: CommonParams) -> dict[Any, Any]:
    # Normalize common to dict
    if common is None:
        common_dict = {}
    elif isinstance(common, BaseModel):
        common_dict = common.model_dump()
    elif isinstance(common, dict):
        common_dict = common
    else:
        raise TypeError("`common` must be a CommonParams instance, dict, or None")
    return common_dict
