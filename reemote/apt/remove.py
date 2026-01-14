from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query

from reemote.context import Context, HttpMethod
from reemote.operation import (
    Operation,
    CommonOperationRequestModel,
    common_operation_request,
)
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler

router = APIRouter()


class RemoveRequestModel(CommonOperationRequestModel):
    packages: list[str]


class Remove(Operation):
    request_model = RemoveRequestModel


    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt-get remove -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            method=HttpMethod.POST,
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None


@router.post(
    "/remove",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def remove(
    common: RemoveRequestModel = Depends(common_operation_request),
    packages: list[str] = Query(..., description="List of package names"),
) -> RemoveRequestModel:
    """# Remove APT packages"""
    return await router_handler(RemoveRequestModel, Remove)(
        common=common, packages=packages
    )
