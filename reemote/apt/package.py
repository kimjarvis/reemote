# from typing import AsyncGenerator, List
# from pydantic import Field
# from fastapi import APIRouter, Depends, Query
#
# from reemote.apt import GetPackages, Install, Remove
# from reemote.context import Context, Method
# from reemote.operation import (
#     Operation,
#     CommonOperationRequest,
#     common_operation_request,
# )
# from reemote.response import PutResponseElement
# from reemote.router_handler import router_handler
# from reemote.core import return_put
#
# router = APIRouter()
#
#
# class Package(Operation):
#     class Request(CommonOperationRequest):
#         packages: list[str] = Field(..., description="List of package names.")
#         present: bool = Field(
#             ..., description="Whether the packages should be present or not."
#         )
#
#     class Response(PutResponseElement):
#         pass
#
#     async def execute(self) -> AsyncGenerator[Context, List[Response]]:
#         model_instance = self.Request.model_validate(self.kwargs)
#
#         pre = yield GetPackages()
#         if model_instance.present:
#             yield Install(**self.common_kwargs, packages=model_instance.packages)
#         else:
#             yield Remove(**self.common_kwargs, packages=model_instance.packages)
#         post = yield GetPackages()
#
#         changed = pre["value"] != post["value"]
#         yield return_put(changed=changed)
#
#     @staticmethod
#     @router.put("/package", tags=["APT Package Manager"], response_model=List[Response])
#     async def package(
#         packages: list[str] = Query(..., description="List of package names."),
#         present: bool = Query(
#             ..., description="Whether the packages should be present or not."
#         ),
#         common: Request = Depends(common_operation_request),
#     ) -> Request:
#         """# Manage installed APT packages"""
#         return await router_handler(Package.Request, Package)(
#             common=common,
#             packages=packages,
#             present=present,
#         )
