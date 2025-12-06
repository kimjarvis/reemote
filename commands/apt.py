from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from command import Command
from response import Response
from typing import Any, AsyncGenerator, Type, Optional
from common.base_classes import ShellBasedCommand
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params


router = APIRouter()



class InstallModel(BaseModel):
    packages: list[str]


class RemoveModel(BaseModel):
    packages: list[str]


class APTInstall(ShellBasedCommand):
    """APT install command"""
    Model = InstallModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = f"apt-get install -y {' '.join(self._data['packages'])}"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return


class APTRemove(ShellBasedCommand):
    """APT remove command"""
    Model = RemoveModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = f"apt-get remove -y {' '.join(self._data['packages'])}"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return

# Create endpoint handlers
install_handler = create_router_handler(InstallModel, APTInstall)
remove_handler = create_router_handler(RemoveModel, APTRemove)


@router.get("/install/", tags=["APT Commands"])
async def operations_apt_packages_install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Install APT packages"""
    return await install_handler(packages=packages, common=common)


@router.get("/remove/", tags=["APT Commands"])
async def operations_apt_packages_remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Remove APT packages"""
    return await remove_handler(packages=packages, common=common)