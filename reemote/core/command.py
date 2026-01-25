# from typing import AsyncGenerator
#
# from fastapi import APIRouter, Depends, Query
# from pydantic import Field
# from reemote.context import Context, Method, ContextType
# from reemote.operation import CommonOperationRequest, common_operation_request
# from reemote.operation import Operation
# from reemote.router_handler import router_handler
# from reemote.response import PostResponse, PostResponseElement
#
#
# router = APIRouter()
#
#
# class CommandRequest(CommonOperationRequest):
#     cmd: str = Field(...)
#
#
# class Command(Operation):
#     async def execute(self) -> AsyncGenerator[Context, PostResponseElement]:
#         model_instance = CommandRequest.model_validate(self.kwargs)
#         result = yield Context(
#             command=model_instance.cmd,
#             call=self.__class__.child + "(" + str(model_instance) + ")",
#             type=ContextType.OPERATION,
#             method=Method.GET,
#             **self.common_kwargs,
#         )
#         PostResponseElement(root=result)
#
#
# @router.post(
#     "/command",
#     tags=["Core Operations"],
#     response_model=PostResponse,
# )
# async def command(
#     cmd: str = Query(
#         ..., description="Shell command", examples=["systemctl start firewalld"]
#     ),
#     common: CommonOperationRequest = Depends(common_operation_request),
# ) -> PostResponse:
#     """# Execute a shell command on the remote host"""
#     return await router_handler(CommandRequest, Command)(cmd=cmd, common=common)
