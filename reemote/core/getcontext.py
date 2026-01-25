# from typing import List
# from fastapi import APIRouter, Depends
# from pydantic import BaseModel, Field, RootModel
#
# from reemote.callback import (
#     Callback,
#     CommonCallbackRequest,
#     common_callback_request,
# )
# from reemote.context import Context, Method
# from reemote.operation import (
#     CommonOperationRequest,
# )
# from reemote.response import GetResponseElement
# from reemote.router_handler import router_handler
#
# router = APIRouter()
#
#
# class GetContextResponseElement(GetResponseElement):
#     value: Context = Field(
#         default=None,
#         description="The operational context.",
#     )
#
#
# class GetContextResponse(RootModel):
#     root: List[GetContextResponseElement]
#
#
# class GetContext(Callback):
#     request_model = CommonOperationRequest
#     response_model = GetContextResponseElement
#
#     @staticmethod
#     async def callback(context: Context) -> None:
#         context.method = Method.GET
#         return context
#
#
# @router.get(
#     "/getcontext",
#     tags=["Core Operations"],
#     response_model=GetContextResponse,
# )
# async def getcontext(
#     common: CommonCallbackRequest = Depends(common_callback_request),
# ) -> BaseModel:
#     return await router_handler(CommonOperationRequest, GetContext)(common=common)
