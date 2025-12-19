from fastapi import APIRouter, Depends, Query
from pydantic import Field
from typing import AsyncGenerator
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.remote_model import Remote, RemoteModel, remote_params
from reemote.response import Response
router = APIRouter()

class ShellModel(RemoteModel):
    cmd: str = Field(
        ...,  # Required field
    )

class Shell(Remote):
    Model = ShellModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)

        yield Command(
            command = model_instance.cmd,
            call = str(model_instance),
            **self.common_kwargs
        )

@router.get("/server/shell/", tags=["Server Commands"])
async def shell(
        cmd: str = Query(..., description="Shell command"),
        common: RemoteModel = Depends(remote_params)
) -> list[dict]:
    """# Execute a shell command on the remote host"""
    return await router_handler(ShellModel, Shell)(cmd=cmd, common=common)
