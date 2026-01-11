from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context
from reemote.core.remote import Remote, RemoteModel, remotemodel
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler

router = APIRouter()


class Update(Remote):
    Model = RemoteModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        result = yield Context(
            command="apt-get update",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None
        return


@router.put(
    "/update",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def update(common: RemoteModel = Depends(remotemodel)) -> RemoteModel:
    """# Update APT packages"""
    return await router_handler(RemoteModel, Update)(common=common)
