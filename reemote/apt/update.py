from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context, HttpMethod
from reemote.operation import Operation, CommonOperationRequestModel, common_operation_request
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler

router = APIRouter()


class Update(Operation):
    request_model = CommonOperationRequestModel
    response_model = ResponseModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)

        result = yield Context(
            command="apt-get update",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            method=HttpMethod.POST,
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None



@router.post(
    "/update",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def update(common: CommonOperationRequestModel = Depends(common_operation_request)) -> CommonOperationRequestModel:
    """# Update APT packages"""
    return await router_handler(CommonOperationRequestModel, Update)(common=common)
