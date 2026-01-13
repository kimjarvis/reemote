from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query

from reemote.apt import GetPackages, Install, Remove
from reemote.context import Context
from reemote.operation import Operation, CommonOperationRequestModel, common_operation_request
from reemote.core.response import ResponseModel
from reemote.core.router_handler import router_handler
from reemote.system import Return

router = APIRouter()

class PackageRequestModel(CommonOperationRequestModel):
    packages: list[str]
    present: bool

class Package(Operation):
    Model = PackageRequestModel

    async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
        model_instance = self.Model.model_validate(self.kwargs)

        pre = yield GetPackages()
        if model_instance.present:
            result = yield Install(**self.common_kwargs, packages=model_instance.packages)
        else:
            result = yield Remove(**self.common_kwargs, packages=model_instance.packages)
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(changed=changed, value=result["value"])




@router.put("/package", tags=["APT Package Manager"], response_model=ResponseModel)
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(
        True, description="Whether the packages should be present or not"
    ),
    common: PackageRequestModel = Depends(common_operation_request),
) -> PackageRequestModel:
    """# Manage installed APT packages"""
    return await router_handler(PackageRequestModel, Package)(
        common=common,
        packages=packages,
        present=present,
    )
