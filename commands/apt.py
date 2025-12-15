from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from command import Command
from common.base_classes import BaseCommand
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from response import Response
from facts.parse_apt_list_installed import parse_apt_list_installed
from construction_tracker import  track_construction, track_yields
import logging
router = APIRouter()



class InstallModel(BaseModel):
    packages: list[str]

@track_construction
class Install(BaseCommand):
    """APT install command"""
    Model = InstallModel

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = f"apt-get install -y {' '.join(self.data['packages'])}"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return

class RemoveModel(BaseModel):
    packages: list[str]

@track_construction
class Remove(BaseCommand):
    """APT remove command"""
    Model = RemoveModel

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = f"apt-get remove -y {' '.join(self.data['packages'])}"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return

class UpdateModel(BaseModel):
    pass

@track_construction
class Update(BaseCommand):
    """APT remove command"""
    Model = UpdateModel

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = f"apt-get update"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return

class UpgradeModel(BaseModel):
    pass

@track_construction
class Upgrade(BaseCommand):
    """APT remove command"""
    Model = UpgradeModel

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = f"apt-get upgrade"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return

class GetPackagesModel(BaseModel):
    pass

@track_construction
class GetPackages(BaseCommand):
    """Get installed packages fact"""
    Model = GetPackagesModel

    @track_yields
    async def execute(self) -> AsyncGenerator:
        cmd = f"apt list --installed"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )

        if result and hasattr(result, 'output'):
            result.output = parse_apt_list_installed(result.cp.stdout)
        return


class PackageModel(BaseModel):
    packages: list[str]
    update: bool = False
    upgrade: bool = False
    present: bool = True

@track_construction
class Package(BaseCommand):
    """APT package command"""
    Model = PackageModel

    @track_yields
    async def execute(self) -> AsyncGenerator[GetPackages | Update | Upgrade | Install | Remove, Response]:
        if self.data["update"]:
            before_update = yield GetPackages()
            update = yield Update(**self.extra_kwargs)
            after_update = yield GetPackages()
            if before_update[0].output == after_update[0].output:
                self.mark_unchanged(update)

        if self.data["upgrade"]:
            before_upgrade = yield GetPackages()

            upgrade = yield Upgrade(**self.extra_kwargs)
            after_update = yield GetPackages()
            if before_upgrade[0].output == after_upgrade[0].output:
                self.mark_unchanged(upgrade)

        if self.data["packages"] is not None:
            before_action = yield GetPackages()
            if self.data["present"]:
                install = yield Install(
                    packages=self.data["packages"],
                    **self.extra_kwargs
                )
                after_action = yield GetPackages()
                if before_action[0].output == after_action[0].output:
                    self.mark_unchanged(install)
            else:
                remove = yield Remove(
                    packages=self.data["packages"],
                    **self.extra_kwargs
                )
                after_action = yield GetPackages()
                if before_action[0].output == after_action[0].output:
                    self.mark_unchanged(remove)
        return



# Create endpoint handlers
install_handler = create_router_handler(InstallModel, Install)
remove_handler = create_router_handler(RemoveModel, Remove)
update_handler = create_router_handler(UpdateModel, Update)
upgrade_handler = create_router_handler(UpgradeModel, Upgrade)
package_handler = create_router_handler(RemoveModel, Package)
get_packages_handler = create_router_handler(GetPackagesModel, GetPackages)


@router.get("/command/install/", tags=["APT Package Manager"])
async def install(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """# Install APT packages"""
    return await install_handler(packages=packages, common=common)


@router.get("/command/remove/", tags=["APT Package Manager"])
async def remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """# Remove APT packages"""
    return await remove_handler(packages=packages, common=common)


@router.get("/command/update/", tags=["APT Package Manager"])
async def update(
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """# Refresh the package index files from their sources"""
    return await update_handler(common=common)

@router.get("/command/upgrade/", tags=["APT Package Manager"])
async def upgrade(
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """# Install available updates for all installed packages"""
    return await upgrade_handler(common=common)


@router.get("/operation/package/", tags=["APT Package Manager"])
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(True, description="Whether the packages should be present or not"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """# Manage installed APT packages"""
    return await package_handler(packages=packages, present=present, common=common)


@router.get("/fact/get_packages/", tags=["APT Package Manager"])
async def get_packages(
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    """# Get installed APT packages"""
    return await get_packages_handler(common=common)






