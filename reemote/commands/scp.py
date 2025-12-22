from typing import Callable, List, Optional

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from reemote.router_handler import router_handler
from reemote.models import LocalModel, localmodel
from reemote.local import Local

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
    async def _callback(host_info, global_info, command, cp, caller):

        return await asyncssh.scp(
            srcpaths=caller.srcpaths,
            dstpath=(host_info.get("host"), caller.dstpath),
            username=host_info.get("username"),
            preserve=caller.preserve,
            recurse=caller.recurse,
            block_size=caller.block_size,
            progress_handler=caller.progress_handler,
            error_handler=caller.error_handler,
        )

@router.get("/commands/upload/", tags=["SCP Commands"])
async def upload(
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
) -> list[dict]:
    """
    will continue starting with the next file.
    """
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
    async def _callback(host_info, global_info, command, cp, caller):

        return await asyncssh.scp(
            srcpaths=[(host_info.get("host"), path) for path in caller.srcpaths],
            dstpath=caller.dstpath,
            username=host_info.get("username"),
            preserve=caller.preserve,
            recurse=caller.recurse,
            block_size=caller.block_size,
            progress_handler=caller.progress_handler,
            error_handler=caller.error_handler,
        )

@router.get("/commands/download/", tags=["SCP Commands"])
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
) -> list[dict]:
    """
    will continue starting with the next file.
    """
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
    async def _callback(host_info, global_info, command, cp, caller):

        return await asyncssh.scp(
            srcpaths=[(host_info.get("host"), path) for path in caller.srcpaths],
            dstpath=(caller.dsthost, caller.dstpath),
            username=host_info.get("username"),
            preserve=caller.preserve,
            recurse=caller.recurse,
            block_size=caller.block_size,
            progress_handler=caller.progress_handler,
            error_handler=caller.error_handler,
        )

@router.get("/commands/copy/", tags=["SCP Commands"])
async def copy(
        srcpaths: List[str] = Query(
            ...,
            description="The paths of the source files or directories to copy"
        ),
        dstpath: str = Query(
            None,
            description="The path of the destination file or directory to copy into"
        ),
        dstgroup: str = Query(
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
) -> list[dict]:
    """
    will continue starting with the next file.
    """
    return await router_handler(ScpModel, Copy)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        dstgroup=dstgroup,
        preserve=preserve,
        recurse=recurse,
        block_size=block_size,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)
