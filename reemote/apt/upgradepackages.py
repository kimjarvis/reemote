from typing import AsyncGenerator

from fastapi import APIRouter, Depends

from reemote.context import Context, Method
from reemote.operation import (
    Operation,
    CommonOperationRequest,
    common_operation_request,
)
from reemote.response import PutResponse, PutResponseElement
from reemote.router_handler import router_handler
from reemote.apt.getpackages import GetPackages
from reemote.system import Return

from reemote.apt.upgrade import Upgrade

router = APIRouter()


class UpgradePackages(Operation):
    async def execute(self) -> AsyncGenerator[Context, PutResponse]:
        model_instance = CommonOperationRequest.model_validate(self.kwargs)

        pre = yield GetPackages()
        yield Upgrade(
            **self.common_kwargs,
        )
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(method=Method.PUT, changed=changed, value=None)


@router.put(
    "/upgradepackages",
    tags=["APT Package Manager"],
    response_model=PutResponse,
)
async def upgradepackages(
    common: CommonOperationRequest = Depends(common_operation_request),
) -> CommonOperationRequest:
    """# Upgrade APT packages"""
    return await router_handler(CommonOperationRequest, UpgradePackages)(
        common=common
    )
