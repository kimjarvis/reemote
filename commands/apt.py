from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from command import Command
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from remote_params import RemoteParams, RemoteModel
from response import Response
from construction_tracker import track_construction, track_yields

router = APIRouter()


class InstallModel(RemoteParams):
    packages: list[str]


@track_construction
class Install(RemoteModel):
    """APT install command"""

    Model = InstallModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get install -y {' '.join(self.extra_kwargs['packages'])}",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/install/", tags=["APT Package Manager"])
async def install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params),
) -> list[dict]:
    """# Install APT packages"""
    return await create_router_handler(InstallModel, Install)(
        packages=packages, common=common
    )


class RemoveModel(RemoteParams):
    packages: list[str]


@track_construction
class Remove(RemoteModel):
    """APT install command"""

    Model = RemoveModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get remove -y {' '.join(self.extra_kwargs['packages'])}",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/remove/", tags=["APT Package Manager"])
async def remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params),
) -> list[dict]:
    """# Remove APT packages"""
    return await create_router_handler(RemoveModel, Remove)(
        packages=packages, common=common
    )


class UpdateModel(RemoteParams):
    pass


@track_construction
class Update(RemoteModel):
    """APT install command"""

    Model = UpdateModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get update",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/update/", tags=["APT Package Manager"])
async def update(common: CommonParams = Depends(common_params)) -> list[dict]:
    """# Refresh the package index files from their sources"""
    return await create_router_handler(UpdateModel, Update)(common=common)


class UpgradeModel(RemoteParams):
    pass


@track_construction
class Upgrade(RemoteModel):
    """APT install command"""

    Model = UpgradeModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get upgrade",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/upgrade/", tags=["APT Package Manager"])
async def upgrade(common: CommonParams = Depends(common_params)) -> list[dict]:
    """# Install available updates for all installed packages"""
    return await create_router_handler(UpgradeModel, Upgrade)(common=common)
