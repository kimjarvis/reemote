from fastapi import APIRouter, Depends, Query
from pydantic import Field
from typing import AsyncGenerator
from command import Command
from common.router_utils import create_router_handler
from remote_params import RemoteModel, RemoteParams, remote_params
from response import Response
router = APIRouter()

class ShellModel(RemoteParams):
    cmd: str = Field(
        ...,  # Required field
    )

class Shell(RemoteModel):
    Model = ShellModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)

        yield Command(
            command = model_instance.cmd,
            call = str(model_instance),
            **self.common_kwargs
        )

@router.get("/server/shell/", tags=["Server"])
async def shell(
        cmd: str = Query(..., description="Shell command"),
        common: RemoteParams = Depends(remote_params)
) -> list[dict]:
    """# Execute a shell command on the remote host"""
    return await create_router_handler(ShellModel, Shell)(cmd=cmd, common=common)
