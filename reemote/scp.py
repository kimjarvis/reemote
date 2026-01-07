import logging
from typing import Callable, List, Optional

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.core.router_handler import router_handler
from reemote.core.local import LocalModel, localmodel
from reemote.core.local import Local
from reemote.core.response import ResponseModel
from reemote.context import Context

router = APIRouter()

class ScpModel(LocalModel):
    srcpaths: List[str] = Field(
        ...,  # Required field
    )
    dstpath: str = Field(
        ...,  # Required field
    )
    preserve: bool = False
    recurse: bool = False
    block_size: int = 16384
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None


class Upload(Local):
    Model = ScpModel

    @staticmethod
    async def _callback(context: Context):
        try:
            return await asyncssh.scp(
                srcpaths=context.caller.srcpaths,
                dstpath=(context.inventory_item.connection.host, context.caller.dstpath),
                username=context.inventory_item.connection.username,
                preserve=context.caller.preserve,
                recurse=context.caller.recurse,
                block_size=context.caller.block_size,
                progress_handler=context.caller.progress_handler,
                error_handler=context.caller.error_handler,
            )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.post("/upload", tags=["SCP Operations"], response_model=ResponseModel)
async def upload(
        srcpaths: List[str] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: str = Query(
            ...,
            description="The path of the destination file or directory to copy into"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        block_size: int = Query(
            16384,
            ge=1,
            description="The block size to use for file reads and writes"
        ),
        progress_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for download progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for error handling"
        ),
        common: LocalModel = Depends(localmodel)
) -> ResponseModel:
    """# Upload files to the host"""
    return await router_handler(ScpModel, Upload)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)

class Download(Local):
    Model = ScpModel

    @staticmethod
    async def _callback(context: Context):
        try:
            return await asyncssh.scp(
                srcpaths=[(context.inventory_item.connection.host, path) for path in context.caller.srcpaths],
                dstpath=context.caller.dstpath,
                username=context.inventory_item.connection.username,
                preserve=context.caller.preserve,
                recurse=context.caller.recurse,
                block_size=context.caller.block_size,
                progress_handler=context.caller.progress_handler,
                error_handler=context.caller.error_handler,
            )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.post("/download", tags=["SCP Operations"], response_model=ResponseModel)
async def download(
        srcpaths: List[str] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: str = Query(
            None,
            description="The path of the destination file or directory to copy into"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        block_size: int = Query(
            16384,
            ge=1,
            description="The block size to use for file reads and writes"
        ),
        progress_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for download progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for error handling"
        ),
        common: LocalModel = Depends(localmodel)
) -> ResponseModel:
    """# Download files from the host"""
    return await router_handler(ScpModel, Download)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)

class CopyModel(ScpModel):
    dsthost: str = Field(
        ...,  # Required field
    )

class Copy(Local):
    Model = CopyModel

    @staticmethod
    async def _callback(command: Context):
        try:
            return await asyncssh.scp(
                srcpaths=[(command.inventory_item.connection.host, path) for path in command.caller.srcpaths],
                dstpath=(command.caller.dsthost, command.caller.dstpath),
                username=command.inventory_item.connection.username,
                preserve=command.caller.preserve,
                recurse=command.caller.recurse,
                block_size=command.caller.block_size,
                progress_handler=command.caller.progress_handler,
                error_handler=command.caller.error_handler,
            )
        except Exception as e:
            command.error = True
            logging.error(f"{command.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.post("/copy", tags=["SCP Operations"], response_model=ResponseModel)
async def copy(
        srcpaths: List[str] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: str = Query(
            None,
            description="The path of the destination file or directory to copy into"
        ),
        dsthost: str = Query(
            None,
            description="The group of the host to copy to"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        block_size: int = Query(
            16384,
            ge=1,
            description="The block size to use for file reads and writes"
        ),
        progress_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for download progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            include_in_schema=False,  # This hides it from OpenAPI schema
            description="Callback function name for error handling"
        ),
        common: LocalModel = Depends(localmodel)
) -> ResponseModel:
    """# Copy files between hosts"""
    return await router_handler(ScpModel, Copy)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        dsthost=dsthost,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)
