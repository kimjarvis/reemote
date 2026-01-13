from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context
from reemote.operation import Operation, CommonOperationRequestModel, common_operation_request
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler

router = APIRouter()


class Upgrade(Operation):
    Model = CommonOperationRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        result = yield Context(
            command="apt-get upgrade",
            call=self.__class__.child + "(" + str(model_instance) + ")",
            **self.common_kwargs,
        )
        if not result["error"]:
            result["value"] = None


@router.post(
    "/upgrade",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def upgrade(common: CommonOperationRequestModel = Depends(common_operation_request)) -> CommonOperationRequestModel:
    """# Upgrade APT packages"""
    return await router_handler(CommonOperationRequestModel, Upgrade)(common=common)
