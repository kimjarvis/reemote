from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from pydantic import BaseModel, RootModel
from reemote.context import Context, Method
from reemote.operation import CommonOperationRequestModel, common_operation_request
from reemote.operation import Operation
from reemote.response import ResponseElement
from reemote.router_handler import router_handler
from reemote.callback import CommonCallbackRequestModel, common_callback_request
from reemote.response import GetResponseModel
from reemote.callback import Callback


router = APIRouter()


class ShellRequestModel(CommonOperationRequestModel):
    cmd: str = Field(...)


class SSHCompletedProcessModel(BaseModel):
    # env: Optional[Dict[str, str]] = Field(
    #     default=None,
    #     description="The environment the client requested to be set for the process."
    # )
    command: Optional[str] = Field(
        default=None,
        description="The command the client requested the process to execute (if any).",
    )
    subsystem: Optional[str] = Field(
        default=None,
        description="The subsystem the client requested the process to open (if any).",
    )
    exit_status: int = Field(
        description="The exit status returned, or -1 if an exit signal is sent."
    )
    exit_signal: Optional[Tuple[str, bool, str, str]] = Field(
        default=None,
        description="The exit signal sent (if any) in the form of a tuple containing "
        "the signal name, a bool for whether a core dump occurred, a message "
        "associated with the signal, and the language the message was in.",
    )
    returncode: int = Field(
        description="The exit status returned, or negative of the signal number when an exit signal is sent."
    )
    stdout: Union[str, bytes, None] = Field(
        default=None,
        description="The output sent by the process to stdout (if not redirected).",
    )
    stderr: Union[str, bytes, None] = Field(
        default=None,
        description="The output sent by the process to stderr (if not redirected).",
    )


class ShellResponseElement(ResponseElement):
    value: SSHCompletedProcessModel = Field(
        default=None, description="The results from the executed command."
    )


class ShellResponseModel(RootModel[List[ShellResponseElement]]):
    pass


class Shell(Operation):
    request_model = ShellRequestModel

    async def execute(self) -> AsyncGenerator[Context, ShellResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)
        yield Context(
            command=model_instance.cmd,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            method=Method.POST,
            **self.common_kwargs,
        )


@router.post("/shell", tags=["Host Operations"], response_model=ShellResponseModel)
async def shell(
    cmd: str = Query(
        ..., description="Shell command", examples=["echo Hello World!", "ls -ltr"]
    ),
    common: CommonOperationRequestModel = Depends(common_operation_request),
) -> ShellResponseModel:
    """# Execute a shell command on the remote host"""
    return await router_handler(ShellRequestModel, Shell)(cmd=cmd, common=common)


class Getcontext(Callback):
    request_model = CommonOperationRequestModel

    @staticmethod
    async def callback(context: Context) -> None:
        return context


@router.get(
    "/getcontext",
    tags=["Host Operations"],
    response_model=GetResponseModel,
)
async def get_context(
    common: CommonCallbackRequestModel = Depends(common_callback_request),
) -> BaseModel:
    return await router_handler(CommonOperationRequestModel, Getcontext)(common=common)
