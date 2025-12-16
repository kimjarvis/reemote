from fastapi import APIRouter, Depends, Query
from pydantic import Field

from common.router_utils import create_router_handler
from shell_params import RemoteModel, RemoteParams, remote_params

router = APIRouter()


# router = APIRouter()

class ShellModel(RemoteParams):
    cmd: str = Field(
        ...,  # Required field
    )

class Shell(RemoteModel):
    Model = ShellModel

@router.get("/server/shell/", tags=["Server"])
async def shell(
        cmd: str = Query(..., description="Shell command"),
        common: RemoteParams = Depends(remote_params)
) -> list[dict]:
    """# Execute a shell command on the remote host"""
    return await create_router_handler(ShellModel, Shell)(cmd=cmd, common=common)
