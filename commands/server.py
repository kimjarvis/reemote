# commands/server.py
from typing import AsyncGenerator

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from command import Command
from common.base_classes import ShellBasedCommand
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from construction_tracker import ConstructionTracker,track_construction, track_yields
from response import Response

router = APIRouter()

class ShellModel(BaseModel):
    cmd: str


@track_construction
class Shell(ShellBasedCommand):
    """Shell command implementation"""
    Model = ShellModel

    @property
    def cmd(self) -> str:
        return self._data["cmd"]

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = self.cmd
        result = yield Command(
            command=cmd,
            id=ConstructionTracker.get_current_id(),
            **self.extra_kwargs
        )

        # Mark as changed if needed
        self.mark_changed(result)

        # If you need to yield the result
        # yield result

# Create endpoint handler
shell_handler = create_router_handler(ShellModel, Shell)

@router.get("/command/shell/", tags=["Server"])
async def shell_command(
        cmd: str = Query(..., description="Shell command"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Execute shell command"""
    return await shell_handler(cmd=cmd, common=common)