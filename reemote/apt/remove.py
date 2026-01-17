from typing import AsyncGenerator

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


class RemoveRequest(CommonOperationRequest):
    packages: list[str]


class Remove(Operation):
    async def execute(self) -> AsyncGenerator[Context, PostResponse]:
        model_instance = RemoveRequest.model_validate(self.kwargs)

        result = yield Context(
            command=f"apt-get remove -y {' '.join(model_instance.packages)}",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.POST,
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None

        PostResponseElement(root=result)


@router.post(
    "/remove",
    tags=["APT Package Manager"],
    response_model=PostResponse,
)
async def remove(
    common: RemoveRequest = Depends(common_operation_request),
    packages: list[str] = Query(..., description="List of package names"),
) -> RemoveRequest:
    """# Remove APT packages"""
    return await router_handler(RemoveRequest, Remove)(
        common=common, packages=packages
    )
