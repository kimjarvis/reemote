from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from reemote.context import Context
from reemote.core.request import RequestModel, requestmodel
from reemote.core.request import Request
from reemote.core.response import (
    Response,
    ShellResponseModel,
)
from reemote.core.router_handler import router_handler
from reemote.system import Callback
from reemote.core.local import LocalModel, localmodel


router = APIRouter()


class ShellRequestModel(RequestModel):
    cmd: str = Field(...)


class Shell(Request):
    Model = ShellRequestModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)
        yield Context(
            command=model_instance.cmd,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.post("/shell", tags=["Host Operations"], response_model=ShellResponseModel)
async def shell(
    cmd: str = Query(..., description="Shell command",examples=["echo Hello World!","ls -ltr"]),
    common: RequestModel = Depends(requestmodel),
) -> ShellResponseModel:
    """# Execute a shell command on the remote host"""
    return await router_handler(ShellRequestModel, Shell)(cmd=cmd, common=common)




class ContextGetResponse(BaseModel):
    error: bool
    value: Context

async def context_get_callback(context: Context):
    return context

class Getcontext(Request):
    Model = LocalModel

    async def execute(self):
        yield Callback(callback=context_get_callback)

@router.get(
    "/getcontext",
    tags=["Host Operations"],
    response_model=List[ContextGetResponse],
)
async def get_context(
    common: LocalModel = Depends(localmodel)
) -> List[ContextGetResponse]:
    """# Retrieve the context"""
    return await router_handler(LocalModel, Getcontext)(
        common=common
    )