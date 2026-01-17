from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context, Method, ContextType
from reemote.operation import (
    Operation,
    CommonOperationRequest,
    common_operation_request,
)
from reemote.response import PostResponse, PostResponseElement
from reemote.router_handler import router_handler

router = APIRouter()


class Upgrade(Operation):
    async def execute(self) -> AsyncGenerator[Context, PostResponse]:
        model_instance = CommonOperationRequest.model_validate(self.kwargs)

        result = yield Context(
            command="apt-get upgrade",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.POST,
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None

        PostResponseElement(root=result)


@router.post(
    "/upgrade",
    tags=["APT Package Manager"],
    response_model=PostResponse,
)
async def upgrade(
    common: CommonOperationRequest = Depends(common_operation_request),
) -> CommonOperationRequest:
    """# Upgrade APT packages"""
    return await router_handler(CommonOperationRequest, Upgrade)(common=common)
