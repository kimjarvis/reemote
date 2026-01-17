from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context, Method, ContextType
from reemote.operation import (
    Operation,
    CommonOperationRequest,
    common_operation_request,
)
from reemote.response import PutResponse, PutResponseElement
from reemote.router_handler import router_handler

router = APIRouter()


class Update(Operation):
    async def execute(self) -> AsyncGenerator[Context, PutResponse]:
        model_instance = CommonOperationRequest.model_validate(self.kwargs)

        result = yield Context(
            command="apt-get update",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.POST,
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None

        PutResponseElement(root=result)


@router.post(
    "/update",
    tags=["APT Package Manager"],
    response_model=PutResponse,
)
async def update(
    common: CommonOperationRequest = Depends(common_operation_request),
) -> CommonOperationRequest:
    """# Update APT packages"""
    return await router_handler(CommonOperationRequest, Update)(common=common)
