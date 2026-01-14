from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context, HttpMethod
from reemote.operation import Operation, CommonOperationRequestModel, common_operation_request
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler
from reemote.apt.getpackages import GetPackages
from reemote.system import Return

from reemote.apt.upgrade import Upgrade

router = APIRouter()

class UpgradePackages(Operation):
    request_model = CommonOperationRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:

        pre = yield GetPackages()
        yield Upgrade(
            **self.common_kwargs,
        )
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(method=HttpMethod.PUT, changed=changed, value=None)




@router.put(
    "/upgradepackages",
    tags=["APT Package Manager"],
    response_model=ResponseModel,
)
async def upgradepackages(common: CommonOperationRequestModel = Depends(common_operation_request)) -> CommonOperationRequestModel:
    """# Upgrade APT packages"""
    return await router_handler(CommonOperationRequestModel, UpgradePackages)(common=common)
