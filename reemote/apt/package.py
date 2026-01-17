from typing import AsyncGenerator
from pydantic import Field
from fastapi import APIRouter, Depends, Query

from reemote.apt import GetPackages, Install, Remove
from reemote.context import Context, Method, ContextType
from reemote.operation import (
    Operation,
    CommonOperationRequest,
    common_operation_request,
)
from reemote.response import PutResponse
from reemote.router_handler import router_handler
from reemote.system import Return

router = APIRouter()


class PackageRequest(CommonOperationRequest):
    packages: list[str] = Field(..., description="List of package names")
    present: bool = Field(
        True, description="Whether the packages should be present or not"
    )


class Package(Operation):
    async def execute(self) -> AsyncGenerator[Context, PutResponse]:
        model_instance = PackageRequest.model_validate(self.kwargs)

        pre = yield GetPackages()
        if model_instance.present:
            result = yield Install(
                **self.common_kwargs, packages=model_instance.packages
            )
        else:
            result = yield Remove(
                **self.common_kwargs, packages=model_instance.packages
            )
        post = yield GetPackages()

        changed = pre["value"] != post["value"]

        yield Return(method=Method.PUT, changed=changed)


@router.put("/package", tags=["APT Package Manager"], response_model=PutResponse)
async def package(
    packages: list[str] = Query(..., description="List of package names"),
    present: bool = Query(
        True, description="Whether the packages should be present or not"
    ),
    common: PackageRequest = Depends(common_operation_request),
) -> PackageRequest:
    """# Manage installed APT packages"""
    return await router_handler(PackageRequest, Package)(
        common=common,
        packages=packages,
        present=present,
    )
