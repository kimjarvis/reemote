from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query

from reemote.context import Context, Method, ContextType
from reemote.operation import (
    Operation,
    CommonOperationRequestModel,
    common_operation_request,
)
from reemote.response import ResponseModel
from reemote.router_handler import router_handler

router = APIRouter()


class InstallRequestModel(CommonOperationRequestModel):
    packages: list[str]


class Install(Operation):
    request_model = InstallRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt-get install -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.POST,
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None

@router.post(
    "/install",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def install(
    common: InstallRequestModel = Depends(common_operation_request),
    packages: list[str] = Query(..., description="List of package names"),
) -> InstallRequestModel:
    """# Install APT packages"""
    return await router_handler(InstallRequestModel, Install)(
        common=common, packages=packages
    )
