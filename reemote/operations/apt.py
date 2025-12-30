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
from reemote.response import ResponseModel
from reemote.router_handler import router_handler
from reemote.commands.server import Shell

router = APIRouter()


class PackageModel(RemoteModel):
    packages: list[str]
    update: bool = Field(default=False, description="Whether or not to update the package list")
    present: bool = Field(default=True, description="Whether or not the packages should be present")

class Package(Remote):
    Model = PackageModel

    async def execute(
        self,
    ) -> AsyncGenerator[GetPackages | Update | Install | Remove, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        r = yield GetPackages(sudo=True)
        print(r)
        r = yield Update()
        print(r)
        return


@router.get("/package", tags=["APT Package Manager Operations"])
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(
        True, description="Whether the packages should be present or not"
    ),
    update: bool = Query(False, description="Whether or not to update the package list"),
    common: CommonModel = Depends(commonmodel),
) -> list[dict]:
    """# Manage installed APT packages"""
    return await router_handler(PackageModel, Package)(
        common=common,
        packages=packages,
        present=present,
        update=update,
    )
