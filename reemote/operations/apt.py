from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from reemote.common_model import CommonModel, common_params
from reemote.remote_model import RemoteModel, Remote
from reemote.response import Response
from reemote.commands.apt import Install, Remove, Update, Upgrade
from reemote.facts.apt import GetPackages
from reemote.checks import mark_unchanged

router = APIRouter()

class PackageModel(RemoteModel):
    packages: list[str]
    update: bool = False
    upgrade: bool = False
    present: bool = True

class Package(Remote):
    """APT package command"""
    Model = PackageModel

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

