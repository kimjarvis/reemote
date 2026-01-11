from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query

from reemote.context import Context
from reemote.core.remote import Remote, RemoteModel, remotemodel
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler
from reemote.apt1.getpackages import GetPackages
from reemote.system import Return

router = APIRouter()


class InstallRequestModel(RemoteModel):
    packages: list[str]


class _Install(Remote):
    Model = InstallRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt-get install -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None
        return


class Install(Remote):
    Model = InstallRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        pre = yield GetPackages()
        result = yield _Install(**self.kwargs)
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(changed=changed, value=result["value"])

        return


@router.put(
    "/install",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def install(
    common: InstallRequestModel = Depends(remotemodel),
    packages: list[str] = Query(..., description="List of package names"),
) -> InstallRequestModel:
    """# Install APT packages"""
    return await router_handler(InstallRequestModel, Install)(
        common=common, packages=packages
    )
