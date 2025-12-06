from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from command import Command
from common.base_classes import ShellBasedCommand
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from response import Response
from facts.parse_apt_list_installed import parse_apt_list_installed

router = APIRouter()



class InstallModel(BaseModel):
    packages: list[str]


class Install(ShellBasedCommand):
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

class RemoveModel(BaseModel):
    packages: list[str]

class Remove(ShellBasedCommand):
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

class UpdateModel(BaseModel):
    pass

class Update(ShellBasedCommand):
    """APT remove command"""
    Model = UpdateModel

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

class Upgrade(ShellBasedCommand):
    """APT remove command"""
    Model = UpgradeModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        cmd = f"apt-get upgrade"
        result = yield Command(
            command=cmd,
            **self.extra_kwargs
        )
        self.mark_changed(result)
        return


class PackageModel(BaseModel):
    packages: list[str]
    update: bool = False
    upgrade: bool = False
    present: bool = True

class APTPackage(ShellBasedCommand):
    """APT package command"""
    Model = PackageModel

    async def execute(self) -> AsyncGenerator[Command, Response]:
        from commands.apt import GetPackages
        before_update = yield GetPackages()
        update = yield Update(guard=self._data["update"])
        self.mark_unchanged(update)
        after_update = yield GetPackages()
        if before_update.output != after_update.output:
            self.mark_changed(update)
        upgrade = yield Upgrade(guard=self._data["upgrade"])
        self.mark_unchanged(upgrade)
        after_upgrade = yield GetPackages()
        if after_update.output != after_upgrade.output:
            self.mark_changed(upgrade)
        install = yield Install(
            guard=self._data["present"],
            packages=self._data["packages"],
            **self.extra_kwargs  # Pass sudo flag here
        )
        self.mark_unchanged(install)
        remove = yield Remove(
            guard=not self._data["present"],
            packages=self._data["packages"],
            **self.extra_kwargs  # Pass sudo flag here
        )
        self.mark_unchanged(remove)
        after_opration = yield GetPackages()
        if after_upgrade.output != after_opration.output:
            if self._data["present"]:
                self.mark_changed(install)
            else:
               self.mark_changed(remove)
        return

class GetPackagesModel(BaseModel):
    pass

class GetPackages(ShellBasedCommand):
    """Get installed packages fact"""
    Model = GetPackagesModel

    async def execute(self) -> AsyncGenerator:
        from commands.server import Shell
        result = yield Shell(cmd="apt list --installed", **self.extra_kwargs)

        if result and hasattr(result, 'output'):
            result.output = parse_apt_list_installed(result.cp.stdout)
            print(result.output)

        return


# Create endpoint handlers
install_handler = create_router_handler(InstallModel, Install)
remove_handler = create_router_handler(RemoveModel, Remove)
update_handler = create_router_handler(UpdateModel, Update)
upgrade_handler = create_router_handler(UpgradeModel, Upgrade)
package_handler = create_router_handler(RemoveModel, APTPackage)
get_packages_handler = create_router_handler(GetPackagesModel, GetPackages)


@router.get("/command/install/", tags=["APT Package Manager"])
async def install_packages(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Install APT packages"""
    return await install_handler(packages=packages, common=common)


@router.get("/command/remove/", tags=["APT Package Manager"])
async def remove_packages(
    packages: list[str] = Query(..., description="List of package names"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Remove APT packages"""
    return await remove_handler(packages=packages, common=common)


@router.get("/command/update/", tags=["APT Package Manager"])
async def update_packages(
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Remove APT packages"""
    return await update_handler(common=common)

@router.get("/command/upgrade/", tags=["APT Package Manager"])
async def upgrade_packages(
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Remove APT packages"""
    return await upgrade_handler(common=common)


@router.get("/operation/package/", tags=["APT Package Manager"])
async def package_operations(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(True, description="Whether the packages should be present or not"),
    common: CommonParams = Depends(common_params)
) -> list[dict]:
    """APT packages"""
    return await package_handler(packages=packages, present=present, common=common)


@router.get("/fact/get_packages/", tags=["APT Package Manager"])
async def get_packages(
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    """Get installed APT packages"""
    return await get_packages_handler(common=common)






