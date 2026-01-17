from typing import AsyncGenerator
from pydantic import Field
from fastapi import APIRouter, Depends, Query

from reemote.context import Context, Method, ContextType
from reemote.operation import (
    Operation,
    CommonOperationRequest,
    common_operation_request,
)
from reemote.response import PostResponse, PostResponseElement
from reemote.router_handler import router_handler

router = APIRouter()


class InstallRequest(CommonOperationRequest):
    packages: list[str] = Field(..., description="List of package names")


class Install(Operation):
    async def execute(self) -> AsyncGenerator[Context, PostResponse]:
        model_instance = InstallRequest.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt-get install -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.POST,
            **self.common_kwargs,
        )
        PostResponseElement(root=result)


@router.post(
    "/install",
    tags=["APT Package Manager"],
    response_model=PostResponse,
)
async def install(
    common: InstallRequest = Depends(common_operation_request),
    packages: list[str] = Query(..., description="List of package names"),
) -> InstallRequest:
    """# Install APT packages"""
    return await router_handler(InstallRequest, Install)(
        common=common, packages=packages
    )
