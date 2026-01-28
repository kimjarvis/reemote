from pathlib import PurePath
from typing import List, Union

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel

from reemote.callback import Callback, CommonCallbackRequest, common_callback_request
from reemote.context import Context, Method
from reemote.response import GetResponseElement
from reemote.router_handler import router_handler1
from reemote.sftp1.common import PathRequest

router = APIRouter()

class SftpGetIsDirRequest(PathRequest):
    pass

class SftpGetIsDirResponse(GetResponseElement):
    value: bool = False
    request: SftpGetIsDirRequest = Field(
        default=None,
        description="The request object used to execute the operation.",
    )

class SftpGetIsDirResponses(RootModel):
    root: List[SftpGetIsDirResponse]


class IsDir(Callback):

    request = SftpGetIsDirRequest
    response = SftpGetIsDirResponse
    responses = SftpGetIsDirResponses
    method = Method.GET

    @classmethod
    async def callback(cls, context: Context) -> None:
        context.response = cls.response
        context.method = cls.method
        async with asyncssh.connect(
            **context.inventory_item.connection.to_json_serializable()
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(context.caller.path)

    @staticmethod
    @router.get(
        "/is_dir",
        tags=["SFTP Operations"],
        response_model=SftpGetIsDirResponses,
        responses={
            # block insert examples/sftp/get/IsDir_responses.generated -4
            "200": {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "host": "server104",
                                "error": False,
                                "message": "",
                                "value": True
                            },
                            {
                                "host": "server105",
                                "error": False,
                                "message": "",
                                "value": True
                            }
                        ]
                    }
                }
            }
            # block end
        },
    )
    async def is_dir(
        path: Union[PurePath, str, bytes] = Query(
            ..., description="Path to check if it's a directory"
        ),
        common: CommonCallbackRequest = Depends(common_callback_request),
    ):
        """# Return if the remote path refers to a directory

        <!-- block insert examples/sftp/get/IsDir_example.generated -->
        
        ## sftp.get.IsDir
        
        Example:
        
        ```python
        async def example(inventory):
            from reemote.execute import execute
            from reemote import sftp1
        
            responses = await execute(lambda: sftp1.get.IsDir(path=".."), inventory)
            for item in responses:
                assert item.value, (
                    "Expected the coroutine to report that the current working directory exists on all hosts."
                )
        
            return responses
        ```
        <!-- block end -->
        """
        return await (router_handler1(IsDir))(
            path=path,
            common=common,
        )
