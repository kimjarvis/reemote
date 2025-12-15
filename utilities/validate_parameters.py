from typing import Any, Union, Dict, Type, TypedDict
from typing import Optional
from pydantic import BaseModel
from pydantic import ValidationError
from common_params import CommonParams, LocalParams


class ValidationOK(TypedDict):
    valid: bool
    data: Dict[str, Any]


class ValidationFail(TypedDict):
    valid: bool
    errors: Any


def validate_parameters(
    model: Type[BaseModel],
    common: Optional[Union[CommonParams, LocalParams, Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Union[ValidationOK, ValidationFail]:
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
        raise TypeError(
            f"`common` must be a CommonParams instance, dict, or None {type(common)} {common}"
        )

    # Merge data (kwargs override common_dict)
    all_data = {**common_dict, **kwargs}

    try:
        parms = model(**all_data)
        return {
            "valid": True,
            "data": parms.model_dump(),
        }
    except ValidationError as e:
        return {
            "valid": False,
            "errors": e.errors(),
        }

