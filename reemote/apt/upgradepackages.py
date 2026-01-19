from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends

from reemote.context import Context, Method
from reemote.operation import (
    Operation,
    CommonOperationRequest,
    common_operation_request,
)
from reemote.response import PutResponseElement
from reemote.router_handler import router_handler
from reemote.apt.getpackages import GetPackages
from reemote.core import return_put

from reemote.apt.upgrade import Upgrade

router = APIRouter()


class UpgradePackages(Operation):
    class Request(CommonOperationRequest):
        pass

    class Response(PutResponseElement):
        pass

    async def execute(self) -> AsyncGenerator[Context, List[Response]]:
        self.Request.model_validate(self.kwargs)

        pre = yield GetPackages()
        yield Upgrade(
            **self.common_kwargs,
        )
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield return_put(method=Method.PUT, changed=changed)

    @staticmethod
    @router.put(
        "/upgradepackages",
        tags=["APT Package Manager"],
        response_model=List[Response],
    )
    async def upgradepackages(
        common: CommonOperationRequest = Depends(common_operation_request),
    ) -> CommonOperationRequest:
        """# Upgrade APT packages"""
        return await router_handler(UpgradePackages.Request, UpgradePackages)(
            common=common
        )
