from typing import AsyncGenerator
from fastapi import APIRouter, Depends
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.common_model import CommonModel, common_params
from reemote.remote_model import RemoteModel, Remote
from reemote.response import Response
from reemote.facts.parse_apt_list_installed import parse_apt_list_installed
from reemote.construction_tracker import track_construction

router = APIRouter()


class GetPackagesModel(RemoteModel):
    pass


@track_construction
class GetPackages(Remote):
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


@router.get("/fact/get_packages/", tags=["APT Package Manager Facts"])
async def get_packages(common: CommonModel = Depends(common_params)) -> list[dict]:
    """# Get installed APT packages"""
    return await router_handler(GetPackagesModel, GetPackages)(
        cmd=cmd, common=common
    )
