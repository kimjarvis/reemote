from typing import Any

from pydantic import BaseModel

from common import CommonParams


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
