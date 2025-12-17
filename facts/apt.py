from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from command import Command
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from remote_params import RemoteParams, RemoteModel
from response import Response
from facts.parse_apt_list_installed import parse_apt_list_installed
from construction_tracker import track_construction, track_yields
import logging

router = APIRouter()


class GetPackagesModel(RemoteParams):
    pass


@track_construction
class GetPackages(RemoteModel):
    """Get installed packages fact"""

    Model = GetPackagesModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        result = yield Command(
            command=f"apt list --installed",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )

        if result and hasattr(result, "output"):
            result.output = parse_apt_list_installed(result.cp.stdout)
        return


@router.get("/fact/get_packages/", tags=["APT Package Manager"])
async def get_packages(common: CommonParams = Depends(common_params)) -> list[dict]:
    """# Get installed APT packages"""
    return await create_router_handler(GetPackagesModel, GetPackages)(
        cmd=cmd, common=common
    )
