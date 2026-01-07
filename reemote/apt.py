from typing import AsyncGenerator, Union
from reemote.core.response import ResponseElement, Response
from reemote.core.parse_apt_list_installed import parse_apt_list_installed
from fastapi import APIRouter, Query, Depends
from reemote.context import Context
from reemote.core.router_handler import router_handler
from reemote.core.remote import remotemodel, RemoteModel
from reemote.core.remote import Remote
from reemote.core.response import ShellResponseModel
from reemote.system import Return
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler_put
from pydantic import BaseModel, Field
from typing import List


router = APIRouter()


class GetPackagesRequestModel(RemoteModel):
    pass


class PackageModel(BaseModel):
    name: str = Field(..., description="The name of the package")
    version: str = Field(..., description="The version of the package")


class PackageList(BaseModel):
    packages: List[PackageModel] = Field(
        ..., description="A list of packages with their names and versions"
    )


class GetPackagesResponse(ResponseElement):
    value: Union[str, PackageList] = Field(
        default="",
        description="The response containing package versions, or an error message",
    )


class GetPackages(Remote):
    Model = GetPackagesRequestModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt list --installed",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            changed=False,
            **self.common_kwargs,
        )
        parsed_packages = parse_apt_list_installed(result["value"]["stdout"])

        # Convert the parsed list of dictionaries into a PackageList
        package_list = PackageList(
            packages=[PackageModel(**pkg) for pkg in parsed_packages]
        )

        # Wrap the PackageList in a GetPackagesResponse
        result["value"] = package_list

        return


@router.get(
    "/get_packages",
    tags=["APT Package Manager"],
    response_model=List[GetPackagesResponse],
)
async def get_packages(common: RemoteModel = Depends(remotemodel)) -> list[dict]:
    """# Get installed APT packages"""
    return await router_handler(RemoteModel, GetPackages)(common=common)


class InstallRequestModel(RemoteModel):
    packages: list[str]


class Install(Remote):
    Model = InstallRequestModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            command=f"apt-get install -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.post(
    "/install",
    tags=["APT Package Manager"],
    response_model=ShellResponseModel,
)
async def install(
    packages: list[str] = Query(..., description="List of package names"),
    common: RemoteModel = Depends(remotemodel),
) -> ShellResponseModel:
    """# Install APT packages"""
    return await router_handler(InstallRequestModel, Install)(
        packages=packages, common=common
    )


class RemoveModel(RemoteModel):
    packages: list[str]


class Remove(Remote):
    Model = RemoveModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            command=f"apt-get remove -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.post(
    "/remove",
    tags=["APT Package Manager"],
    response_model=ShellResponseModel,
)
async def remove(
    packages: list[str] = Query(..., description="List of package names"),
    common: RemoteModel = Depends(remotemodel),
) -> ShellResponseModel:
    """# Remove APT packages"""
    return await router_handler(RemoveModel, Remove)(packages=packages, common=common)


class UpdateRequestModel(RemoteModel):
    pass


class Update(Remote):
    Model = UpdateRequestModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            command="apt-get update",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.post(
    "/update",
    tags=["APT Package Manager"],
    response_model=ShellResponseModel,
)
async def update(
    common: RemoteModel = Depends(remotemodel),
) -> ShellResponseModel:
    """# Update APT packages"""
    return await router_handler(RemoteModel, Update)(common=common)


class UpgradeRequestModel(RemoteModel):
    pass


class Upgrade(Remote):
    Model = UpgradeRequestModel

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Context(
            command="apt-get upgrade",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )


@router.post(
    "/upgrade",
    tags=["APT Package Manager"],
    response_model=ShellResponseModel,
)
async def upgrade(
    common: RemoteModel = Depends(remotemodel),
) -> ShellResponseModel:
    """# Upgrade APT packages"""
    return await router_handler(RemoteModel, Upgrade)(common=common)


class PackageRequestModel(RemoteModel):
    packages: list[str]
    update: bool = Field(
        default=False, description="Whether or not to update the package list"
    )
    present: bool = Field(
        default=True, description="Whether or not the packages should be present"
    )


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
                yield Install(packages=model_instance.packages, **self.common_kwargs)
            else:
                yield Remove(packages=model_instance.packages, **self.common_kwargs)

        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(changed=changed, value=None)

        return


@router.put("/package", tags=["APT Package Manager"], response_model=ResponseModel)
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(
        True, description="Whether the packages should be present or not"
    ),
    update: bool = Query(
        False, description="Whether or not to update the package list"
    ),
    common: RemoteModel = Depends(remotemodel),
) -> ResponseModel:
    """# Manage installed APT packages"""
    return await router_handler_put(PackageRequestModel, Package)(
        common=common,
        packages=packages,
        present=present,
        update=update,
    )
