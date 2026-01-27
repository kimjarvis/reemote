from typing import AsyncGenerator, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.context import Context, ContextType, Method
from reemote.exceptions import (
    BadRequestErrorResponse,
    InternalServerErrorResponse,
    ServiceUnavailableErrorResponse,
)
from reemote.operation import (
    CommonOperationRequest,
    Operation,
    common_operation_request,
)
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1

router = APIRouter()


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


class CoreGetFactRequest(CommonOperationRequest):
    cmd: str = Field(...)


class CoreGetFactResponse(GetResponseElement):
    request: CoreGetFactRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )
    value: SSHCompletedProcess = Field(
        default=None, description="The results from the executed command."
    )


class CoreGetFactResponses(RootModel):
    root: List[CoreGetFactResponse]


class Fact(Operation):
    request = CoreGetFactRequest
    response = CoreGetFactResponse
    responses = CoreGetFactResponses

    method = Method.GET

    async def execute(self) -> AsyncGenerator[Context, CoreGetFactResponse]:
        model_instance = self.request.model_validate(self.kwargs)
        result = yield Context(
            command=model_instance.cmd,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            type=ContextType.OPERATION,
            method=self.method,
            request_instance=model_instance,
            response=self.response,
            **self.common_kwargs,
        )
        self.__class__.response(root=result)

    @staticmethod
    @router.get(
        "/fact",
        tags=["Core Operations"],
        response_model=CoreGetFactResponses,
        responses={
            # block insert examples/core/get/Fact_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": {
                                    "command": "echo Hello World!",
                                    "subsystem": None,
                                    "exit_status": 0,
                                    "exit_signal": None,
                                    "returncode": 0,
                                    "stdout": "Hello World!\n",
                                    "stderr": ""
                                },
                                "request": {
                                    "group": "all",
                                    "name": None,
                                    "sudo": False,
                                    "su": False,
                                    "cmd": "echo Hello World!"
                                }
                            },
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "value": {
                                    "command": "echo Hello World!",
                                    "subsystem": None,
                                    "exit_status": 0,
                                    "exit_signal": None,
                                    "returncode": 0,
                                    "stdout": "Hello World!\n",
                                    "stderr": ""
                                },
                                "request": {
                                    "group": "all",
                                    "name": None,
                                    "sudo": False,
                                    "su": False,
                                    "cmd": "echo Hello World!"
                                }
                            }
                        ]
                    }
                }
            }
            # block end
            ,
            # block insert scripts/boilerplate/operation_error_examples.txt
            "400": {
                "description": "Bad Request",
                "model": BadRequestErrorResponse,  # Reference to the Pydantic model
                "content": {
                    "application/json": {
                        "example": {
                            "detail": "server105 - ReturnCodeNotZeroError bash: line 1: tree: command not found"
                        }
                    }
                },
            },
            "500": {
                "description": "Internal Server Error",
                "model": InternalServerErrorResponse,  # Reference to the Pydantic model
                "content": {
                    "application/json": {
                        "example": {
                            "detail": "server105 - processerror [-2] Process exited with non-zero exit status"
                        }
                    }
                },
            },
            "503": {
                "description": "Service Unavailable",
                "model": ServiceUnavailableErrorResponse,  # Reference to the Pydantic model
                "content": {
                    "application/json": {
                        "example": {
                            "detail": "server105 - Address resolution failed gaierror [Errno -2] Name or service not known"
                        }
                    }
                },
            },
            # block end
        },
    )
    async def fact(
        cmd: str = Query(
            ..., description="Shell command", examples=["echo Hello World!", "ls -ltr"]
        ),
        common: CommonOperationRequest = Depends(common_operation_request),
    ):
        """# Execute a shell command to get information from the remote host

        <!-- block insert examples/core/get/Fact_example.generated -->
        
        ## core.get.Fact
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote import core
        
            responses = await execute(lambda: core.get.Fact(cmd='echo Hello World!'), inventory)
        
            for item in responses:
                assert "Hello World" in item.value.stdout, "Expected the coroutine to yield the output of the command"
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(Fact))(
            cmd=cmd,
            common=common,
        )
