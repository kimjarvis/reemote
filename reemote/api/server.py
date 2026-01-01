from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.command import Command
from reemote.models import RemoteModel, remotemodel
from reemote.remote import Remote
from reemote.response import (
    Response,
    ShellResponseModel,
)
from reemote.router_handler import router_handler

router = APIRouter()


class ShellRequestModel(RemoteModel):
    cmd: str = Field(
        ...,  # Required field
    )


class Shell(Remote):
    Model = ShellRequestModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)
        yield Command(
            command=model_instance.cmd,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.post("/shell", tags=["Server Operations"], response_model=ShellResponseModel)
async def shell(
    cmd: str = Query(..., description="Shell command"),
    common: RemoteModel = Depends(remotemodel),
) -> ShellResponseModel:
    """# Execute a shell command on the remote host"""
    return await router_handler(ShellRequestModel, Shell)(cmd=cmd, common=common)
