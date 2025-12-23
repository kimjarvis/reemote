from typing import AsyncGenerator
from fastapi import APIRouter, Depends
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.models import CommonModel, commonmodel, RemoteModel
from reemote.remote import Remote
from reemote.response import Response
from reemote.facts.parse_apt_list_installed import parse_apt_list_installed

router = APIRouter()


class GetPackagesModel(RemoteModel):
    pass



class GetPackages(Remote):
    """Get installed packages fact"""

    Model = GetPackagesModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        result = yield Command(
            command=f"apt list --installed",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )

        if result and hasattr(result, "value"):
            result.value = parse_apt_list_installed(result.cp.stdout)
        return


@router.get("/fact/get_packages/", tags=["APT Package Manager Facts"])
async def get_packages(common: CommonModel = Depends(commonmodel)) -> list[dict]:
    """# Get installed APT packages"""
    return await router_handler(GetPackagesModel, GetPackages)(
        cmd=cmd, common=common
    )
