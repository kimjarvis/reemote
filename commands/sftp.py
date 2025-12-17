import stat
from pathlib import PurePath
from typing import Callable, Optional, Sequence, Union

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator

from common.router_utils import create_router_handler
from local_model import Local, LocalModel, local_params

router = APIRouter()


class CopyModel(LocalModel):
    srcpaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]]  = Field(
        ...,  # Required field
    )
    dstpath: Optional[Union[PurePath, str, bytes]]  = Field(
        ...,  # Required field
    )
    remote_only: bool = False

    preserve: bool = False
    recurse: bool = False
    follow_symlinks: bool = False
    sparse: bool = True
    block_size: Optional[int] = -1
    max_requests: Optional[int] = -1
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None

    @field_validator('srcpaths', mode='before')
    @classmethod
    def validate_srcpaths(cls, v):
        """
        Ensure srcpaths is a list of PurePath objects.
        """
        if isinstance(v, (str, bytes, PurePath)):
            return [PurePath(v)]
        elif isinstance(v, Sequence) and not isinstance(v, (str, bytes)):
            try:
                return [PurePath(path) for path in v]
            except TypeError:
                raise ValueError("All elements in srcpaths must be convertible to PurePath.")
        raise ValueError("srcpaths must be a PurePath, str, bytes, or a sequence of these types.")

    @field_validator('dstpath', mode='before')
    @classmethod
    def validate_dstpath(cls, v):
        """
        Ensure dstpath is a PurePath object.
        """
        if v is None:
            raise ValueError("dstpath cannot be None.")
        try:
            return PurePath(v)
        except TypeError:
            raise ValueError(f"Cannot convert {v} to PurePath.")

class McopyModel(CopyModel):
    pass

class Copy(Local):
    Model = CopyModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.copy(
                    srcpaths=caller.srcpaths,
                    dstpath=caller.dstpath,
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    follow_symlinks=caller.follow_symlinks,
                    sparse=caller.sparse,
                    block_size=caller.block_size,
                    max_requests=caller.max_requests,
                    progress_handler=caller.progress_handler,
                    error_handler=caller.error_handler,
                    remote_only = caller.remote_only
                )

class Mcopy(Local):
    Model = McopyModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.mcopy(
                    srcpaths=caller.srcpaths,
                    dstpath=caller.dstpath,
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    follow_symlinks=caller.follow_symlinks,
                    sparse=caller.sparse,
                    block_size=caller.block_size,
                    max_requests=caller.max_requests,
                    progress_handler=caller.progress_handler,
                    error_handler=caller.error_handler,
                    remote_only = caller.remote_only
                )

@router.get("/commands/copy/",
            tags=["SFTP Commands"])
async def copy(
        srcpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
            ...,
            description="The paths of the remote files or directories to copy"
        ),
        dstpath: Optional[Union[PurePath, str, bytes]] = Query(
            ...,
            description="The path of the remote file or directory to copy into"
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
        remote_only: bool = Query(
            False,
            description="Whether or not to only allow this to be a remote copy"
        ),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Copy remote files to a new location"""
    return await create_router_handler(CopyModel, Copy)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        preserve=preserve,
        recurse=recurse,
        follow_symlinks=follow_symlinks,
        sparse=sparse,
        block_size=block_size,
        max_requests=max_requests,
        progress_handler=progress_handler,
        error_handler=error_handler,
        remote_only=remote_only,
        common=common)

@router.get("/commands/mcopy/",
            tags=["SFTP Commands"])
async def mcopy(
        srcpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
            ...,
            description="The paths of the remote files or directories to copy"
        ),
        dstpath: Optional[Union[PurePath, str, bytes]] = Query(
            ...,
            description="The path of the remote file or directory to copy into"
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
        remote_only: bool = Query(
            False,
            description="Whether or not to only allow this to be a remote copy"
        ),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Copy remote files to a new location"""
    return await create_router_handler(CopyModel, Mcopy)(
        srcpaths=srcpaths,
        dstpath=dstpath,
        preserve=preserve,
        recurse=recurse,
        follow_symlinks=follow_symlinks,
        sparse=sparse,
        block_size=block_size,
        max_requests=max_requests,
        progress_handler=progress_handler,
        error_handler=error_handler,
        remote_only=remote_only,
        common=common)


class GetModel(LocalModel):
    remotepaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]] = Field(
        ...,  # Required field
    )
    localpath: Optional[Union[PurePath, str, bytes]]  = Field(
        ...,  # Required field
    )

    preserve: bool = False
    recurse: bool = False
    follow_symlinks: bool = False
    sparse: bool = True
    block_size: Optional[int] = -1
    max_requests: Optional[int] = -1
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None

    @field_validator('remotepaths', mode='before')
    @classmethod
    def validate_remotepaths(cls, v):
        """
        Ensure remotepaths is a list of PurePath objects.
        """
        if isinstance(v, (str, bytes, PurePath)):
            return [PurePath(v)]
        elif isinstance(v, Sequence) and not isinstance(v, (str, bytes)):
            try:
                return [PurePath(path) for path in v]
            except TypeError:
                raise ValueError("All elements in remotepaths must be convertible to PurePath.")
        raise ValueError("remotepaths must be a PurePath, str, bytes, or a sequence of these types.")

    @field_validator('localpath', mode='before')
    @classmethod
    def validate_localpath(cls, v):
        """
        Ensure localpath is a PurePath object.
        """
        if v is None:
            raise ValueError("localpath cannot be None.")
        try:
            return PurePath(v)
        except TypeError:
            raise ValueError(f"Cannot convert {v} to PurePath.")

class MgetModel(GetModel):
    pass


class Get(Local):
    Model = GetModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.get(
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

class Mget(Local):
    Model = MgetModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.mget(
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



@router.get("/commands/get/", tags=["SFTP Commands"])
async def get(
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
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Download remote files"""
    return await create_router_handler(GetModel, Get)(
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


@router.get("/commands/mget/", tags=["SFTP Commands"])
async def mget(
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
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Download remote files with glob pattern match"""
    return await create_router_handler(GetModel, Mget)(
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





class PutModel(LocalModel):
    localpaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]] = Field(
        ...,  # Required field
    )
    remotepath: Optional[Union[PurePath, str, bytes]] = Field(
        ...,  # Required field
    )

    preserve: bool = False
    recurse: bool = False
    follow_symlinks: bool = False
    sparse: bool = True
    block_size: Optional[int] = -1
    max_requests: Optional[int] = -1
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None

    @field_validator('localpaths', mode='before')
    @classmethod
    def validate_localpaths(cls, v):
        """
        Ensure localpaths is a list of PurePath objects.
        """
        if isinstance(v, (str, bytes, PurePath)):
            return [PurePath(v)]
        elif isinstance(v, Sequence) and not isinstance(v, (str, bytes)):
            try:
                return [PurePath(path) for path in v]
            except TypeError:
                raise ValueError("All elements in localpaths must be convertible to PurePath.")
        raise ValueError("localpaths must be a PurePath, str, bytes, or a sequence of these types.")

    @field_validator('remotepath', mode='before')
    @classmethod
    def validate_remotepath(cls, v):
        """
        Ensure remotepath is a PurePath object.
        """
        if v is None:
            raise ValueError("remotepath cannot be None.")
        try:
            return PurePath(v)
        except TypeError:
            raise ValueError(f"Cannot convert {v} to PurePath.")

class MputModel(PutModel):
    pass


class Put(Local):
    Model = PutModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.put(
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

class Mput(Local):
    Model = MputModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                print("debug mput")
                return await sftp.mput(
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

@router.get("/command/put/", tags=["SFTP Commands"])
async def put(
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
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Upload local files"""
    return await create_router_handler(PutModel, Put)(
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


@router.get("/command/mput/", tags=["SFTP Commands"])
async def mput(
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
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Upload local files with glob pattern match"""
    return await create_router_handler(PutModel, Mput)(
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




class MkdirModel(LocalModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )
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

    @field_validator('path', mode='before')
    @classmethod
    def ensure_path_is_purepath(cls, v):
        """
        Ensure the 'path' field is converted to a PurePath object.
        This runs before the field is validated by Pydantic.
        """
        if v is None:
            raise ValueError("path cannot be None.")
        if not isinstance(v, PurePath):
            try:
                return PurePath(v)
            except TypeError:
                raise ValueError(f"Cannot convert {v} to PurePath.")
        return v

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


    def __init__(self, **data):
        # Ensure path is converted to PurePath if it's a string/bytes
        if 'path' in data and isinstance(data['path'], (str, bytes)):
            data['path'] = PurePath(data['path'])
        super().__init__(**data)

class Mkdir(Local):
    Model = MkdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = caller.get_sftp_attrs()
                if sftp_attrs:
                    return await sftp.mkdir(caller.path, sftp_attrs)
                else:
                    return await sftp.mkdir(caller.path)

@router.get("/command/mkdir/", tags=["SFTP Commands"])
async def mkdir(
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
    common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Create a remote directory with the specified attributes"""
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

    return await create_router_handler(MkdirModel, Mkdir)(**params, common=common)


class StatModel(LocalModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )
    follow_symlinks: bool = Field(
        True,  # Default value
    )

    @field_validator('path', mode='before')
    @classmethod
    def ensure_path_is_purepath(cls, v):
        """
        Ensure the 'path' field is converted to a PurePath object.
        This runs before the field is validated by Pydantic.
        """
        if v is None:
            raise ValueError("path cannot be None.")
        if not isinstance(v, PurePath):
            try:
                return PurePath(v)
            except TypeError:
                raise ValueError(f"Cannot convert {v} to PurePath.")
        return v

    def __init__(self, **data):
        # Ensure path is converted to PurePath if it's a string/bytes
        if 'path' in data and isinstance(data['path'], (str, bytes)):
            data['path'] = PurePath(data['path'])
        super().__init__(**data)

class Stat(Local):
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


@router.get("/fact/stat/", tags=["SFTP Commands"])
async def stat(
        path: Union[PurePath, str, bytes] = Query(..., description="The path of the remote file or directory to get attributes for"),
        follow_symlinks: bool = Query(True, description="Whether or not to follow symbolic links"),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Get attributes of a remote file, directory, or symlink"""
    return await create_router_handler(StatModel, Stat)(path=path, follow_symlinks=follow_symlinks, common=common)



class RmdirModel(LocalModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )

    @field_validator('path', mode='before')
    @classmethod
    def ensure_path_is_purepath(cls, v):
        """
        Ensure the 'path' field is converted to a PurePath object.
        This runs before the field is validated by Pydantic.
        """
        if v is None:
            raise ValueError("path cannot be None.")
        if not isinstance(v, PurePath):
            try:
                return PurePath(v)
            except TypeError:
                raise ValueError(f"Cannot convert {v} to PurePath.")
        return v

    def __init__(self, **data):
        # Ensure path is converted to PurePath if it's a string/bytes
        if 'path' in data and isinstance(data['path'], (str, bytes)):
            data['path'] = PurePath(data['path'])
        super().__init__(**data)

class Rmdir(Local):
    Model = RmdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.rmdir(str(caller.path))

@router.get("/command/rmdir/", tags=["SFTP Commands"])
async def islink(
        path: Union[PurePath, str, bytes] = Query(..., description="The path of the remote directory to remove"),
        common: LocalModel = Depends(local_params)
) -> list[dict]:
    """# Remove a remote directory"""
    return await create_router_handler(RmdirModel, Rmdir)(path=path, common=common)


