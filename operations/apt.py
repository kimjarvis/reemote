from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from common_model import CommonModel, common_params
from remote_model import RemoteModel, Remote
from response import Response
from construction_tracker import track_construction, track_yields
from commands.apt import Install, Remove, Update, Upgrade
from facts.apt import GetPackages
from utilities.checks import mark_changed, mark_unchanged

router = APIRouter()

class PackageModel(RemoteModel):
    packages: list[str]
    update: bool = False
    upgrade: bool = False
    present: bool = True

@track_construction
class Package(Remote):
    """APT package command"""
    Model = PackageModel

    @track_yields
    async def execute(self) -> AsyncGenerator[GetPackages | Update | Upgrade | Install | Remove, Response]:
        if self.extra_kwargs.get("update"):
            before_update = yield GetPackages()
            update = yield Update(**self.common_kwargs)
            after_update = yield GetPackages()
            if before_update[0].output == after_update[0].output:
                mark_unchanged(update)

        if self.extra_kwargs.get("upgrade"):
            before_upgrade = yield GetPackages()

            upgrade = yield Upgrade(**self.common_kwargs)
            after_update = yield GetPackages()
            if before_upgrade[0].output == after_upgrade[0].output:
                mark_unchanged(upgrade)

        if "packages" in self.extra_kwargs:
            before_action = yield GetPackages()
            if self.extra_kwargs.get("present"):
                print(f"debug 00 {self.extra_kwargs} {self.extra_kwargs.get("packages")}")
                install = yield Install(
                    packages=self.extra_kwargs.get("packages"),
                    **self.common_kwargs
                )
                after_action = yield GetPackages()
                if before_action.output == after_action.output:
                    mark_unchanged(install)
            else:
                remove = yield Remove(
                    packages=self.extra_kwargs.get("packages"),
                    **self.common_kwargs
                )
                after_action = yield GetPackages()
                if before_action.output == after_action.output:
                    mark_unchanged(remove)
        return


@router.get("/operation/package/", tags=["APT Package Manager Operations"])
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(True, description="Whether the packages should be present or not"),
    common: CommonModel = Depends(common_params)
) -> list[dict]:
    """# Manage installed APT packages"""
    return await package_handler(packages=packages, present=present, common=common)

