from fastapi import APIRouter, Depends, Query
from pydantic import Field
from typing import AsyncGenerator, List
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.models import RemoteModel, remotemodel
from reemote.remote import Remote
from reemote.response import Response, SSHCompletedProcessModel
from pydantic import BaseModel, Field
from reemote.response import ResponseModel, ShellResponse
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
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs
        )

@router.put("/shell", tags=["Server Commands"], response_model=List[ShellResponse])
async def shell(
        cmd: str = Query(..., description="Shell command"),
        common: RemoteModel = Depends(remotemodel)
) -> List[ShellResponse]:
    """# Execute a shell command on the remote host"""
    return await router_handler(ShellModel, Shell)(cmd=cmd, common=common)
