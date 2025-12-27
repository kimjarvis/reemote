from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from reemote.models import CommonModel, commonmodel, RemoteModel
from reemote.remote import Remote
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
            if before_update[0].value == after_update[0].value:
                mark_unchanged(update)

        if self.extra_kwargs.get("upgrade"):
            before_upgrade = yield GetPackages()

            upgrade = yield Upgrade(**self.common_kwargs)
            after_update = yield GetPackages()
            if before_upgrade[0].value == after_upgrade[0].value:
                mark_unchanged(upgrade)

        if "packages" in self.extra_kwargs:
            before_action = yield GetPackages()
            if self.extra_kwargs.get("present"):
                install = yield Install(
                    packages=self.extra_kwargs.get("packages"),
                    **self.common_kwargs
                )
                after_action = yield GetPackages()
                if before_action.value == after_action.value:
                    mark_unchanged(install)
            else:
                remove = yield Remove(
                    packages=self.extra_kwargs.get("packages"),
                    **self.common_kwargs
                )
                after_action = yield GetPackages()
                if before_action.value == after_action.value:
                    mark_unchanged(remove)
        return


@router.get("/package", tags=["APT Package Manager Operations"])
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(True, description="Whether the packages should be present or not"),
    common: CommonModel = Depends(commonmodel)
) -> list[dict]:
    """# Manage installed APT packages"""
    return await package_handler(packages=packages, present=present, common=common)

