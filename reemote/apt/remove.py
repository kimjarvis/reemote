from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query

from reemote.context import Context
from reemote.core.remote import Remote, RemoteModel, remotemodel
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler
from reemote.apt.getpackages import GetPackages
from reemote.system import Return

router = APIRouter()


class RemoveRequestModel(RemoteModel):
    packages: list[str]


class _Remove(Remote):
    Model = RemoveRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt-get remove -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None
        return


class Remove(Remote):
    Model = RemoveRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        pre = yield GetPackages()
        result = yield _Remove(**self.kwargs)
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(changed=changed, value=result["value"])

        return


@router.put(
    "/remove",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def remove(
    common: RemoveRequestModel = Depends(remotemodel),
    packages: list[str] = Query(..., description="List of package names"),
) -> RemoveRequestModel:
    """# Remove APT packages"""
    return await router_handler(RemoveRequestModel, Remove)(
        common=common, packages=packages
    )
