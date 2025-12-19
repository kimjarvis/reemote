from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.common_model import CommonModel, common_params
from reemote.remote_model import RemoteModel, Remote
from reemote.response import Response

router = APIRouter()


class InstallModel(RemoteModel):
    packages: list[str]


class Install(Remote):
    """APT install command"""

    Model = InstallModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get install -y {' '.join(self.extra_kwargs['packages'])}",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/install/", tags=["APT Package Manager Commands"])
async def install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonModel = Depends(common_params),
) -> list[dict]:
    """# Install APT packages"""
    return await router_handler(InstallModel, Install)(
        packages=packages, common=common
    )


class RemoveModel(RemoteModel):
    packages: list[str]



class Remove(Remote):
    """APT install command"""

    Model = RemoveModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get remove -y {' '.join(self.extra_kwargs['packages'])}",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/remove/", tags=["APT Package Manager Commands"])
async def remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonModel = Depends(common_params),
) -> list[dict]:
    """# Remove APT packages"""
    return await router_handler(RemoveModel, Remove)(
        packages=packages, common=common
    )


class UpdateModel(RemoteModel):
    pass


class Update(Remote):
    """APT install command"""

    Model = UpdateModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get update",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/update/", tags=["APT Package Manager Commands"])
async def update(common: CommonModel = Depends(common_params)) -> list[dict]:
    """# Refresh the package index files from their sources"""
    return await router_handler(UpdateModel, Update)(common=common)


class UpgradeModel(RemoteModel):
    pass



class Upgrade(Remote):
    """APT install command"""

    Model = UpgradeModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        yield Command(
            command=f"apt-get upgrade",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )


@router.get("/command/upgrade/", tags=["APT Package Manager Commands"])
async def upgrade(common: CommonModel = Depends(common_params)) -> list[dict]:
    """# Install available updates for all installed packages"""
    return await router_handler(UpgradeModel, Upgrade)(common=common)
