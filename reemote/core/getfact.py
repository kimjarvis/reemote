from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from pydantic import BaseModel, RootModel
from reemote.context import Context, Method, ContextType
from reemote.operation import CommonOperationRequestModel, common_operation_request
from reemote.operation import Operation
from reemote.response import ResponseElement
from reemote.router_handler import router_handler
from reemote.callback import CommonCallbackRequestModel, common_callback_request
from reemote.response import GetResponseModel, GetResponseElement
from reemote.callback import Callback


router = APIRouter()


class GetFactRequestModel(CommonOperationRequestModel):
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


class GetFactResponseElement(GetResponseElement):
    value: SSHCompletedProcessModel = Field(
        default=None, description="The results from the executed command."
    )


# todo: move into class
class GetFactResponseModel(RootModel[List[GetFactResponseElement]]):
    pass


# todo: Two types of shell, get and put ?
class GetFact(Operation):
    request_model = GetFactRequestModel  # todo: remove

    async def execute(self) -> AsyncGenerator[Context, GetFactResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)
        yield Context(
            command=model_instance.cmd,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.GET,
            **self.common_kwargs,
        )
        # todo: validate


@router.post("/getfact", tags=["Core Operations"], response_model=GetFactResponseModel)
async def getfact(
    cmd: str = Query(
        ..., description="Shell command", examples=["echo Hello World!", "ls -ltr"]
    ),
    common: CommonOperationRequestModel = Depends(common_operation_request),
) -> GetFactResponseModel:
    """# Execute a shell command to get information from the remote host """
    return await router_handler(GetFactRequestModel, GetFact)(cmd=cmd, common=common)
