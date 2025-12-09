import stat
from functools import partial
from pathlib import PurePath
from typing import AsyncGenerator
from typing import Union, Optional

import asyncssh
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel, Field

from command import Command
from common.base_classes import ShellBasedCommand
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from construction_tracker import ConstructionTracker, track_construction, track_yields
from response import Response

router = APIRouter()

from pathlib import PurePath
from typing import Union, Sequence, Callable, Optional
from pydantic import BaseModel


class GetModel(BaseModel):
    remotepaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]]
    localpath: Optional[Union[PurePath, str, bytes]] = None
    preserve: bool = False
    recurse: bool = False
    follow_symlinks: bool = False
    sparse: bool = True
    block_size: Optional[int] = -1
    max_requests: Optional[int] = -1
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None


@track_construction
class Get(ShellBasedCommand):
    Model = GetModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                await sftp.get(
                    remotepaths=caller.remotepaths,
                    localpath=caller.localpath,
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    follow_symlinks=caller.follow_symlinks,
                    sparse=caller.sparse,
                    block_size=caller.block_size,
                    max_requests=caller.max_requests,
                    progress_handler=caller.progress_handler,
                    error_handler=caller.error_handler
                )

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        self.mark_changed(result)
        return

get_handler = create_router_handler(GetModel, Get)

@router.get("/commands/get/", tags=["SFTP"])
async def shell_command(
        remotepaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
            ...,
            description="The paths of the remote files or directories to download"
        ),
        localpath: Optional[Union[PurePath, str, bytes]] = Query(
            None,
            description="The path of the local file or directory to download into"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        follow_symlinks: bool = Query(
            False,
            description="Whether or not to follow symbolic links"
        ),
        sparse: bool = Query(
            True,
            description="Whether or not to do a sparse file copy where it is supported"
        ),
        block_size: Optional[int] = Query(
            -1,
            ge=-1,
            description="The block size to use for file reads and writes"
        ),
        max_requests: Optional[int] = Query(
            -1,
            ge=-1,
            description="The maximum number of parallel read or write requests"
        ),
        progress_handler: Optional[str] = Query(
            None,
            description="Callback function name for upload progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            description="Callback function name for error handling"
        ),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await get_handler(
        remotepaths=remotepaths,
        localpath=localpath,
        preserve=preserve,
        recurse=recurse,
        follow_symlinks=follow_symlinks,
        sparse=sparse,
        block_size=block_size,
        max_requests=max_requests,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)










class PutModel(BaseModel):
    localpaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]]
    remotepath: Optional[Union[PurePath, str, bytes]] = None
    preserve: bool = False
    recurse: bool = False
    follow_symlinks: bool = False
    sparse: bool = True
    block_size: Optional[int] = -1
    max_requests: Optional[int] = -1
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None


@track_construction
class Put(ShellBasedCommand):
    Model = PutModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                await sftp.put(
                    localpaths=caller.localpaths,
                    remotepath=caller.remotepath,
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    follow_symlinks=caller.follow_symlinks,
                    sparse=caller.sparse,
                    block_size=caller.block_size,
                    max_requests=caller.max_requests,
                    progress_handler=caller.progress_handler,
                    error_handler=caller.error_handler
                )

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        self.mark_changed(result)
        return

put_handler = create_router_handler(PutModel, Put)

@router.get("/commands/put/", tags=["SFTP"])
async def shell_command(
        localpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
            ...,
            description="The paths of the local files or directories to upload"
        ),
        remotepath: Optional[Union[PurePath, str, bytes]] = Query(
            None,
            description="The path of the remote file or directory to upload into"
        ),
        preserve: bool = Query(
            False,
            description="Whether or not to preserve the original file attributes"
        ),
        recurse: bool = Query(
            False,
            description="Whether or not to recursively copy directories"
        ),
        follow_symlinks: bool = Query(
            False,
            description="Whether or not to follow symbolic links"
        ),
        sparse: bool = Query(
            True,
            description="Whether or not to do a sparse file copy where it is supported"
        ),
        block_size: Optional[int] = Query(
            -1,
            ge=-1,
            description="The block size to use for file reads and writes"
        ),
        max_requests: Optional[int] = Query(
            -1,
            ge=-1,
            description="The maximum number of parallel read or write requests"
        ),
        progress_handler: Optional[str] = Query(
            None,
            description="Callback function name for upload progress"
        ),
        error_handler: Optional[str] = Query(
            None,
            description="Callback function name for error handling"
        ),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await put_handler(
        localpaths=localpaths,
        remotepath=remotepath,
        preserve=preserve,
        recurse=recurse,
        follow_symlinks=follow_symlinks,
        sparse=sparse,
        block_size=block_size,
        max_requests=max_requests,
        progress_handler=progress_handler,
        error_handler=error_handler,
        common=common)



class MkdirModel(BaseModel):
    path: Union[PurePath, str, bytes] = None
    permissions: Optional[int] = Field(
        None,
        ge=0,
        le=0o7777,
        description="Directory permissions as octal integer (e.g., 0o755)"
    )
    uid: Optional[int] = Field(None, description="User ID")
    gid: Optional[int] = Field(None, description="Group ID")
    atime: Optional[float] = Field(None, description="Access time")
    mtime: Optional[float] = Field(None, description="Modification time")

    def get_sftp_attrs(self) -> Optional[asyncssh.SFTPAttrs]:
        """Create SFTPAttrs object from provided attributes"""
        attrs_dict = {}

        if self.permissions is not None:
            attrs_dict['permissions'] = self.permissions
        if self.uid is not None:
            attrs_dict['uid'] = self.uid
        if self.gid is not None:
            attrs_dict['gid'] = self.gid
        if self.atime is not None:
            attrs_dict['atime'] = self.atime
        if self.mtime is not None:
            attrs_dict['mtime'] = self.mtime

        if attrs_dict:
            # Add directory bit if permissions are provided
            if 'permissions' in attrs_dict:
                attrs_dict['permissions'] = attrs_dict['permissions'] | stat.S_IFDIR
            return asyncssh.SFTPAttrs(**attrs_dict)
        return None


@track_construction
class Mkdir(ShellBasedCommand):
    Model = MkdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = caller.get_sftp_attrs()
                if sftp_attrs:
                    await sftp.mkdir(caller.path, sftp_attrs)
                else:
                    await sftp.mkdir(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        # Directory creation is inherently a changing operation
        self.mark_changed(result)
        return

# Create endpoint handler
mkdir_handler = create_router_handler(MkdirModel, Mkdir)

@router.get("/command/mkdir/", tags=["SFTP"])
async def shell_command(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        permissions: Optional[int] = Query(
            None,
            ge=0,
            le=0o7777,
            description="Directory permissions as octal integer (e.g., 755)"
        ),
        uid: Optional[int] = Query(None, description="User ID"),
        gid: Optional[int] = Query(None, description="Group ID"),
        atime: Optional[float] = Query(None, description="Access time"),
        mtime: Optional[float] = Query(None, description="Modification time"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    # Build parameters dictionary
    params = {"path": path}
    if permissions is not None:
        params["permissions"] = permissions
    if uid is not None:
        params["uid"] = uid
    if gid is not None:
        params["gid"] = gid
    if atime is not None:
        params["atime"] = atime
    if mtime is not None:
        params["mtime"] = mtime

    return await mkdir_handler(**params, common=common)

class StatModel(BaseModel):
    path: Union[PurePath, str, bytes] = None
    follow_symlinks: bool = True

@track_construction
class Stat(ShellBasedCommand):
    Model = StatModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = await sftp.stat(caller.path, follow_symlinks=caller.follow_symlinks)

                fields = [
                    'type', 'size', 'alloc_size', 'uid', 'gid', 'owner', 'group',
                    'permissions', 'atime', 'atime_ns', 'crtime', 'crtime_ns',
                    'mtime', 'mtime_ns', 'ctime', 'ctime_ns', 'acl', 'attrib_bits',
                    'attrib_valid', 'text_hint', 'mime_type', 'nlink', 'untrans_name',
                    'extended'
                ]

                # Create a dictionary by extracting each field from the SFTPAttrs object
                attrs_dict = {field: getattr(sftp_attrs, field) for field in fields}
                # print(attrs_dict)
                return attrs_dict

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        return

stat_handler = create_router_handler(StatModel, Stat)

@router.get("/facts/stat/", tags=["SFTP"])
async def shell_command(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        follow_symlinks: bool = Query(True, description="Whether or not to follow symbolic links"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await stat_handler(path=path, follow_symlinks=follow_symlinks, common=common)




class RmdirModel(BaseModel):
    path: Union[PurePath, str, bytes] = None

@track_construction
class Rmdir(ShellBasedCommand):
    Model = RmdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                await sftp.rmdir(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        # Directory deletion is inherently a changing operation
        self.mark_changed(result)
        return

rmdir_handler = create_router_handler(RmdirModel, Rmdir)

@router.get("/commands/rmdir/", tags=["SFTP"])
async def shell_command(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await rmdir_handler(path=path, common=common)


class IsModel(BaseModel):
    path: Union[PurePath, str, bytes] = None

class BaseFileCheck(ShellBasedCommand):
    Model = IsModel

    # Define the SFTP method name as a class attribute
    sftp_method_name = None

    @classmethod
    async def _callback(cls, sftp_method_name, host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                method = getattr(sftp, sftp_method_name)
                return await method(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self._data)

        # Create a partial function with the method name baked in
        callback = partial(self._callback, self.sftp_method_name)

        result = yield Command(local=True,
                               callback=callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        result.output = result.output[0]["value"]
        return


@track_construction
class Isdir(BaseFileCheck):
    sftp_method_name = "isdir"


@track_construction
class Isfile(BaseFileCheck):
    sftp_method_name = "isfile"


@track_construction
class Islink(BaseFileCheck):
    sftp_method_name = "islink"

isdir_handler = create_router_handler(IsModel, Isdir)
isfile_handler = create_router_handler(IsModel, Isfile)
islink_handler = create_router_handler(IsModel, Islink)


@router.get("/fact/isdir/", tags=["SFTP"])
async def shell_command(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await isdir_handler(path=path, common=common)

@router.get("/fact/isfile/", tags=["SFTP"])
async def shell_command(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await isfile_handler(path=path, common=common)

@router.get("/fact/islink/", tags=["SFTP"])
async def shell_command(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await islink_handler(path=path, common=common)

