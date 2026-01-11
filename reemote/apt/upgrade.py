from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context
from reemote.core.remote import Remote, RemoteModel, remotemodel
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler
from reemote.apt.getpackages import GetPackages
from reemote.system import Return

router = APIRouter()


class _Upgrade(Remote):
    Model = RemoteModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        result = yield Context(
            command="apt-get upgrade",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None
        return


class Upgrade(Remote):
    Model = RemoteModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:

        pre = yield GetPackages()
        yield _Upgrade(
            **self.common_kwargs,
        )
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(changed=changed, value=None)

        return


@router.put(
    "/upgrade",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def upgrade(common: RemoteModel = Depends(remotemodel)) -> RemoteModel:
    """# Upgrade APT packages"""
    return await router_handler(RemoteModel, Upgrade)(common=common)
