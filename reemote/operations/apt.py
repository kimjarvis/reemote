from typing import AsyncGenerator
from fastapi import APIRouter, Query, Depends
from reemote.models import CommonModel, commonmodel, RemoteModel
from reemote.remote import Remote
from reemote.response import Response
from reemote.commands.apt import Install, Remove, Update, Upgrade
from reemote.facts.apt import GetPackages
from reemote.checks import mark_unchanged
from reemote.commands.system import Return
from pydantic import BaseModel, Field, field_validator
from reemote.response import ResponseElement, ResponseModel
from reemote.router_handler import router_handler
from reemote.commands.server import Shell
from reemote.router_handler import router_handler_put

router = APIRouter()


class PackageRequestModel(RemoteModel):
    packages: list[str]
    update: bool = Field(default=False, description="Whether or not to update the package list")
    present: bool = Field(default=True, description="Whether or not the packages should be present")

class Package(Remote):
    Model = PackageRequestModel

    async def execute(
        self,
    ) -> AsyncGenerator[GetPackages | Update | Install | Remove, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        pre = yield GetPackages(sudo=True)

        if model_instance.update:
            yield Update(**self.common_kwargs)

        if model_instance.packages:
            if model_instance.present:
                yield Install(
                    packages=model_instance.packages, **self.common_kwargs
                )
            else:
                yield Remove(
                    packages=model_instance.packages, **self.common_kwargs
                )

        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(changed=changed, value=None)

        return



@router.put("/package", tags=["APT Package Manager Operations"], response_model=ResponseModel)
# @router.put("/package", tags=["APT Package Manager Operations"])
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(
        True, description="Whether the packages should be present or not"
    ),
    update: bool = Query(False, description="Whether or not to update the package list"),
    common: CommonModel = Depends(commonmodel),
) -> ResponseModel:
    """# Manage installed APT packages"""
    return await router_handler_put(PackageRequestModel, Package)(
        common=common,
        packages=packages,
        present=present,
        update=update,
    )
