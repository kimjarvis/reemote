# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is distributed under the MIT License. See the LICENSE file for details.
#
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, RootModel


class ResponseElement(BaseModel):
    host: str = Field(default="", description="The host the command was executed on")
    changed: bool = Field(
        default=False, description="Whether the host was changed by the operation"
    )
    error: bool = Field(default=False, description="Whether or not there was an error")
    value: Union[str, None] = Field(
        description="Descriptive message or, when error is True, an error message"
    )


class ResponseModel(RootModel[List[ResponseElement]]):
    pass


class ResponseElement1(BaseModel):
    host: str = Field(
        default="",
        description="The ip address or name of the host the operation was executed on.",
    )
    error: bool = Field(
        default=False,
        description="Whether or not there was an error establishing the ssh connectivity with the host.",
    )
    message: Any = Field(
        default="",
        description="Connectivity error message.",
    )

class GetResponseElement(ResponseElement1):
    value: Any = Field(
        default=None,
        description="Class specific information from the target host.",
    )

class GetResponseModel(RootModel[List[GetResponseElement]]):
    pass

class PostResponseElement(ResponseElement1):
    pass

class PostResponseModel(RootModel[List[PostResponseElement]]):
    pass

class PutResponseElement(ResponseElement1):
    changed: bool = Field(
        default=True,
        description="Whether or not the host was changed by the operation.",
    )

class PutResponseModel(RootModel[List[PutResponseElement]]):
    pass
