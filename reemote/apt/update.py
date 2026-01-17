from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context, Method, ContextType
from reemote.operation import (
    Operation,
    CommonOperationRequestModel,
    common_operation_request,
)
from reemote.response import PutResponseModel, PutResponseElement
from reemote.router_handler import router_handler

router = APIRouter()


class Update(Operation):
    async def execute(self) -> AsyncGenerator[Context, PutResponseModel]:
        model_instance = CommonOperationRequestModel.model_validate(self.kwargs)

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
    response_model=PutResponseModel,
)
async def update(
    common: CommonOperationRequestModel = Depends(common_operation_request),
) -> CommonOperationRequestModel:
    """# Update APT packages"""
    return await router_handler(CommonOperationRequestModel, Update)(common=common)
