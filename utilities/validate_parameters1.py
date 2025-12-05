from typing import Any, Union, Dict
from typing import Optional
from pydantic import BaseModel
from pydantic import ValidationError
from common import CommonParams


def validate_parameters1(
        Model,
        common: Optional[Union[CommonParams, Dict[str, Any]]] = None,
        **kwargs: Any
) -> Dict[str, Any]:
    """
    Alternative explicit version with clear parameter separation
    """
    # Normalize common to dict
    if common is None:
        common_dict = {}
    elif isinstance(common, BaseModel):
        common_dict = common.model_dump()
    elif isinstance(common, dict):
        common_dict = common
    else:
        raise TypeError("`common` must be a CommonParams instance, dict, or None")

    # Merge data (kwargs override common_dict)
    all_data = {**common_dict, **kwargs}

    try:
        parms = Model(**all_data)
        return {
            "valid": True,
            "data": parms.model_dump(),
        }
    except ValidationError as e:
        return {
            "valid": False,
            "errors": e.errors(),
        }
