from typing import AsyncGenerator, Union, List
from fastapi import APIRouter, Depends
from reemote.command import Command
from reemote.router_handler import router_handler
from reemote.models import CommonModel, commonmodel, RemoteModel
from reemote.remote import Remote
from reemote.response import Response, ResponseModel
from reemote.facts.parse_apt_list_installed import parse_apt_list_installed
from pydantic import BaseModel, Field, field_validator
router = APIRouter()




class GetPackagesModel(RemoteModel):
    pass


from pydantic import BaseModel, Field
from typing import List

class Package(BaseModel):
    name: str = Field(..., description="The name of the package")
    version: str = Field(..., description="The version of the package")

class PackageList(BaseModel):
    packages: List[Package] = Field(..., description="A list of packages with their names and versions")


class GetPackagesResponse(ResponseModel):
    value: Union[str, PackageList] = Field(default="",
                                                    description="The response containing package versions, or an error message")

class GetPackages(Remote):
    async def execute(self) -> AsyncGenerator[Command, Response]:
        result = yield Command(
            command=f"apt list --installed | head -3",
            call=str(self.Model(**self.kwargs)),
            **self.common_kwargs,
        )
        parsed_packages = parse_apt_list_installed(result["value"]["stdout"])

        # Convert the parsed list of dictionaries into a PackageList
        package_list = PackageList(packages=[Package(**pkg) for pkg in parsed_packages])

        # Wrap the PackageList in a GetPackagesResponse
        result["value"] = package_list

        return


@router.get("/get_packages", tags=["APT Package Manager Facts"], response_model=List[GetPackagesResponse])
async def get_packages(common: CommonModel = Depends(commonmodel)) -> list[dict]:
    """# Get installed APT packages"""
    return await router_handler(RemoteModel, GetPackages)(
        common=common
    )
