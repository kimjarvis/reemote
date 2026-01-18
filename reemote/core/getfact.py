from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from pydantic import BaseModel, RootModel
from reemote.context import Context, Method, ContextType
from reemote.operation import CommonOperationRequest, common_operation_request
from reemote.operation import Operation
from reemote.router_handler import router_handler
from reemote.response import GetResponseElement


router = APIRouter()


class GetFactRequest(CommonOperationRequest):
    cmd: str = Field(...)


class SSHCompletedProcess(BaseModel):
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
    value: SSHCompletedProcess = Field(
        default=None, description="The results from the executed command."
    )


# todo: move into class
class GetFactResponse(RootModel[List[GetFactResponseElement]]):
    pass


# todo: Two types of shell, get and put, where is the put ?
class GetFact(Operation):
    async def execute(self) -> AsyncGenerator[Context, GetFactResponse]:
        model_instance = GetFactRequest.model_validate(self.kwargs)
        result = yield Context(
            command=model_instance.cmd,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=Method.GET,
            **self.common_kwargs,
        )
        GetFactResponseElement(root=result)


@router.get(
    "/getfact",
    tags=["Core Operations"],
    response_model=GetFactResponse,
)
async def getfact(
    cmd: str = Query(
        ..., description="Shell command", examples=["echo Hello World!", "ls -ltr"]
    ),
    common: CommonOperationRequest = Depends(common_operation_request),
) -> GetFactRequest:
    """# Execute a shell command to get information from the remote host"""
    return await router_handler(GetFactRequest, GetFact)(cmd=cmd, common=common)
