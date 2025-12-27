from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.models import CommonModel, commonmodel, RemoteModel
from reemote.remote import Remote
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


@router.get("/install", tags=["APT Package Manager Commands"])
async def install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonModel = Depends(commonmodel),
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


@router.get("/remove", tags=["APT Package Manager Commands"])
async def remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonModel = Depends(commonmodel),
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


@router.get("/update", tags=["APT Package Manager Commands"])
async def update(common: CommonModel = Depends(commonmodel)) -> list[dict]:
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


@router.get("/upgrade", tags=["APT Package Manager Commands"])
async def upgrade(common: CommonModel = Depends(commonmodel)) -> list[dict]:
    """# Install available updates for all installed packages"""
    return await router_handler(UpgradeModel, Upgrade)(common=common)
