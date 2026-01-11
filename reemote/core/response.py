# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is distributed under the MIT License. See the LICENSE file for details.
#
from typing import Any, Dict, Optional, Tuple, Union, List
from pydantic import Field
from pydantic import BaseModel, RootModel





class ResponseElement(BaseModel):
    host: str = Field(default="", description="The host the command was executed on")
    changed: bool = Field(default=False, description="Whether the host changed")
    error: bool = Field(default=False, description="Whether or not there was an error")
    value: Union[str, None] = Field(description="Error message")


class ResponseModel(RootModel[List[ResponseElement]]):
    pass


