from typing import Any, AsyncGenerator
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from common.base_classes import ShellBasedCommand
from command import Command
from response import Response
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params

router = APIRouter()


class ShellModel(BaseModel):
    cmd: str


class Shell(ShellBasedCommand):
    """Shell command implementation"""
    Model = ShellModel

    @property
    def cmd(self) -> str:
        return self._data["cmd"]

    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = self.cmd
        async for response in self.execute_shell_command(cmd):
            yield response


# Create endpoint handler
shell_handler = create_router_handler(ShellModel, Shell)


@router.get("/command/shell/", tags=["Server"])
async def shell_command(
        cmd: str = Query(..., description="Shell command"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Execute shell command"""
    return await shell_handler(cmd=cmd, common=common)