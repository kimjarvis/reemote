import sys
import logging
import stat as stat_module
from pathlib import PurePath
from typing import Callable, Optional, Sequence, Union
import asyncssh
from asyncssh import SFTPFailure, SFTPError
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, ValidationError, root_validator
from reemote.router_handler import router_handler
from reemote.models import LocalModel, localmodel, LocalPathModel
from reemote.local import Local
from reemote.response import Response

router = APIRouter()


class CopyModel(LocalModel):
    srcpaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]] = (
        Field(
            ...,  # Required field
        )
    )
    dstpath: Optional[Union[PurePath, str, bytes]] = Field(
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

    @field_validator("srcpaths", mode="before")
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
                raise ValueError(
                    "All elements in srcpaths must be convertible to PurePath."
                )
        raise ValueError(
            "srcpaths must be a PurePath, str, bytes, or a sequence of these types."
        )

    @field_validator("dstpath", mode="before")
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
        try:
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
                        remote_only=caller.remote_only,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


class Mcopy(Local):
    Model = McopyModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
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
                        remote_only=caller.remote_only,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/commands/copy/", tags=["SFTP Commands"])
async def copy(
    srcpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
        ..., description="The paths of the remote files or directories to copy"
    ),
    dstpath: Optional[Union[PurePath, str, bytes]] = Query(
        ..., description="The path of the remote file or directory to copy into"
    ),
    preserve: bool = Query(
        False, description="Whether or not to preserve the original file attributes"
    ),
    recurse: bool = Query(
        False, description="Whether or not to recursively copy directories"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    sparse: bool = Query(
        True,
        description="Whether or not to do a sparse file copy where it is supported",
    ),
    block_size: Optional[int] = Query(
        -1, ge=-1, description="The block size to use for file reads and writes"
    ),
    max_requests: Optional[int] = Query(
        -1, ge=-1, description="The maximum number of parallel read or write requests"
    ),
    progress_handler: Optional[str] = Query(
        None, description="Callback function name for upload progress"
    ),
    error_handler: Optional[str] = Query(
        None, description="Callback function name for error handling"
    ),
    remote_only: bool = Query(
        False, description="Whether or not to only allow this to be a remote copy"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Copy remote files to a new location"""
    return await router_handler(CopyModel, Copy)(
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
        common=common,
    )


@router.get("/commands/mcopy/", tags=["SFTP Commands"])
async def mcopy(
    srcpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
        ..., description="The paths of the remote files or directories to copy"
    ),
    dstpath: Optional[Union[PurePath, str, bytes]] = Query(
        ..., description="The path of the remote file or directory to copy into"
    ),
    preserve: bool = Query(
        False, description="Whether or not to preserve the original file attributes"
    ),
    recurse: bool = Query(
        False, description="Whether or not to recursively copy directories"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    sparse: bool = Query(
        True,
        description="Whether or not to do a sparse file copy where it is supported",
    ),
    block_size: Optional[int] = Query(
        -1, ge=-1, description="The block size to use for file reads and writes"
    ),
    max_requests: Optional[int] = Query(
        -1, ge=-1, description="The maximum number of parallel read or write requests"
    ),
    progress_handler: Optional[str] = Query(
        None, description="Callback function name for upload progress"
    ),
    error_handler: Optional[str] = Query(
        None, description="Callback function name for error handling"
    ),
    remote_only: bool = Query(
        False, description="Whether or not to only allow this to be a remote copy"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Copy remote files to a new location"""
    return await router_handler(CopyModel, Mcopy)(
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
        common=common,
    )


class GetModel(LocalModel):
    remotepaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]] = (
        Field(
            ...,  # Required field
        )
    )
    localpath: Union[PurePath, str, bytes] = Field(
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

    @field_validator("remotepaths", mode="before")
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
                raise ValueError(
                    "All elements in remotepaths must be convertible to PurePath."
                )
        raise ValueError(
            "remotepaths must be a PurePath, str, bytes, or a sequence of these types."
        )

    @field_validator("localpath", mode="before")
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
        try:
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
                        error_handler=caller.error_handler,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


class Mget(Local):
    Model = MgetModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
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
                        error_handler=caller.error_handler,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/commands/get/", tags=["SFTP Commands"])
async def get(
    remotepaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
        ..., description="The paths of the remote files or directories to download"
    ),
    localpath: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the local file or directory to download into"
    ),
    preserve: bool = Query(
        False, description="Whether or not to preserve the original file attributes"
    ),
    recurse: bool = Query(
        False, description="Whether or not to recursively copy directories"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    sparse: bool = Query(
        True,
        description="Whether or not to do a sparse file copy where it is supported",
    ),
    block_size: Optional[int] = Query(
        -1, ge=-1, description="The block size to use for file reads and writes"
    ),
    max_requests: Optional[int] = Query(
        -1, ge=-1, description="The maximum number of parallel read or write requests"
    ),
    progress_handler: Optional[str] = Query(
        None, description="Callback function name for upload progress"
    ),
    error_handler: Optional[str] = Query(
        None, description="Callback function name for error handling"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Download remote files"""
    return await router_handler(GetModel, Get)(
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
        common=common,
    )


@router.get("/commands/mget/", tags=["SFTP Commands"])
async def mget(
    remotepaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
        ..., description="The paths of the remote files or directories to download"
    ),
    localpath: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the local file or directory to download into"
    ),
    preserve: bool = Query(
        False, description="Whether or not to preserve the original file attributes"
    ),
    recurse: bool = Query(
        False, description="Whether or not to recursively copy directories"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    sparse: bool = Query(
        True,
        description="Whether or not to do a sparse file copy where it is supported",
    ),
    block_size: Optional[int] = Query(
        -1, ge=-1, description="The block size to use for file reads and writes"
    ),
    max_requests: Optional[int] = Query(
        -1, ge=-1, description="The maximum number of parallel read or write requests"
    ),
    progress_handler: Optional[str] = Query(
        None, description="Callback function name for upload progress"
    ),
    error_handler: Optional[str] = Query(
        None, description="Callback function name for error handling"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Download remote files with glob pattern match"""
    return await router_handler(GetModel, Mget)(
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
        common=common,
    )


class PutModel(LocalModel):
    localpaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]] = (
        Field(
            ...,  # Required field
        )
    )
    remotepath: Union[PurePath, str, bytes] = Field(
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

    @field_validator("localpaths", mode="before")
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
                raise ValueError(
                    "All elements in localpaths must be convertible to PurePath."
                )
        raise ValueError(
            "localpaths must be a PurePath, str, bytes, or a sequence of these types."
        )

    @field_validator("remotepath", mode="before")
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
        try:
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
                        error_handler=caller.error_handler,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


class Mput(Local):
    Model = MputModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
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
                        error_handler=caller.error_handler,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/put/", tags=["SFTP Commands"])
async def put(
    localpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
        ..., description="The paths of the local files or directories to upload"
    ),
    remotepath: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file or directory to upload into"
    ),
    preserve: bool = Query(
        False, description="Whether or not to preserve the original file attributes"
    ),
    recurse: bool = Query(
        False, description="Whether or not to recursively copy directories"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    sparse: bool = Query(
        True,
        description="Whether or not to do a sparse file copy where it is supported",
    ),
    block_size: Optional[int] = Query(
        -1, ge=-1, description="The block size to use for file reads and writes"
    ),
    max_requests: Optional[int] = Query(
        -1, ge=-1, description="The maximum number of parallel read or write requests"
    ),
    progress_handler: Optional[str] = Query(
        None, description="Callback function name for upload progress"
    ),
    error_handler: Optional[str] = Query(
        None, description="Callback function name for error handling"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Upload local files"""
    return await router_handler(PutModel, Put)(
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
        common=common,
    )


@router.get("/command/mput/", tags=["SFTP Commands"])
async def mput(
    localpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
        ..., description="The paths of the local files or directories to upload"
    ),
    remotepath: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file or directory to upload into"
    ),
    preserve: bool = Query(
        False, description="Whether or not to preserve the original file attributes"
    ),
    recurse: bool = Query(
        False, description="Whether or not to recursively copy directories"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    sparse: bool = Query(
        True,
        description="Whether or not to do a sparse file copy where it is supported",
    ),
    block_size: Optional[int] = Query(
        -1, ge=-1, description="The block size to use for file reads and writes"
    ),
    max_requests: Optional[int] = Query(
        -1, ge=-1, description="The maximum number of parallel read or write requests"
    ),
    progress_handler: Optional[str] = Query(
        None, description="Callback function name for upload progress"
    ),
    error_handler: Optional[str] = Query(
        None, description="Callback function name for error handling"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Upload local files with glob pattern match"""
    return await router_handler(PutModel, Mput)(
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
        common=common,
    )


class MkdirModel(LocalPathModel):
    permissions: Optional[int] = Field(
        None,
        ge=0,
        le=0o7777,
    )
    uid: Optional[int] = Field(None, description="User ID")
    gid: Optional[int] = Field(None, description="Group ID")
    atime: Optional[int] = Field(None, description="Access time")
    mtime: Optional[int] = Field(None, description="Modification time")

    @root_validator(skip_on_failure=True)
    @classmethod
    def check_atime_and_mtime(cls, values):
        """Ensure that if `atime` is specified, `mtime` is also specified."""
        atime = values.get("atime")
        mtime = values.get("mtime")
        if atime is not None and mtime is None:
            raise ValueError("If `atime` is specified, `mtime` must also be specified.")
        return values

    # todo: rename to get_attributes
    def get_sftp_attrs(self) -> Optional[asyncssh.SFTPAttrs]:
        """Create SFTPAttrs object from provided attributes"""
        attrs_dict = {}
        if self.permissions is not None:
            attrs_dict["permissions"] = self.permissions
        if self.uid is not None:
            attrs_dict["uid"] = self.uid
        if self.gid is not None:
            attrs_dict["gid"] = self.gid
        if self.atime is not None:
            attrs_dict["atime"] = self.atime
        if self.mtime is not None:
            attrs_dict["mtime"] = self.mtime
        if attrs_dict:
            # Add directory bit if permissions are provided
            if "permissions" in attrs_dict:
                attrs_dict["permissions"] = (
                    attrs_dict["permissions"] | stat_module.S_IFDIR
                )
            return asyncssh.SFTPAttrs(**attrs_dict)
        return None

class Mkdir(Local):
    Model = MkdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_attrs = caller.get_sftp_attrs()
                    if sftp_attrs:
                        await sftp.mkdir(
                            path=caller.path, attrs=sftp_attrs if sftp_attrs else None
                        )
                    else:
                        await sftp.mkdir(path=caller.path)
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/mkdir/", tags=["SFTP Commands"])
async def mkdir(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="Directory permissions as integer"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    atime: Optional[int] = Query(None, description="Access time"),
    mtime: Optional[int] = Query(None, description="Modification time"),
    common: LocalModel = Depends(localmodel),
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
    return await router_handler(MkdirModel, Mkdir)(**params, common=common)


class Makedirs(Local):
    Model = MkdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_attrs = caller.get_sftp_attrs()
                    if sftp_attrs:
                        await sftp.makedirs(
                            path=caller.path, attrs=sftp_attrs if sftp_attrs else None
                        )
                    else:
                        await sftp.makedirs(path=caller.path)
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"



@router.get("/command/makedirs/", tags=["SFTP Commands"])
async def mkdirs(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="Directory permissions as integer"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    atime: Optional[int] = Query(None, description="Access time"),
    mtime: Optional[int] = Query(None, description="Modification time"),
    common: LocalModel = Depends(localmodel),
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
    return await router_handler(MkdirModel, Makedirs)(**params, common=common)


class Rmdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.rmdir(str(caller.path))
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/rmdir/", tags=["SFTP Commands"])
async def rmdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote directory to remove"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Remove a remote directory"""
    return await router_handler(LocalPathModel, Rmdir)(path=path, common=common)


class Rmtree(Local):
    # todo: There are other parameters
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.rmtree(str(caller.path))
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/rmtree/", tags=["SFTP Commands"])
async def islink(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the parent directory to remove"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Recursively delete a directory tree"""
    return await router_handler(LocalPathModel, Rmtree)(path=path, common=common)


class ChmodModel(LocalPathModel):
    permissions: Optional[int] = Field(
        None,
        ge=0,
        le=0o7777,
        description="Directory permissions as octal integer (e.g., 0o755)",
    )
    follow_symlinks: bool = False


class Chmod(Local):
    Model = ChmodModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.chmod(
                        path=caller.path,
                        mode=caller.permissions,
                        follow_symlinks=caller.follow_symlinks,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/chmod/", tags=["SFTP Commands"])
async def chmod(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="Directory permissions as integer"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Change the permissions of a remote file, directory, or symlink"""
    return await router_handler(ChmodModel, Chmod)(
        path=path,
        permissions=permissions,
        follow_symlinks=follow_symlinks,
        common=common,
    )


class ChownModel(LocalPathModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )
    # todo: These descriptions are never used
    uid: Optional[int] = Field(None, description="User ID")
    gid: Optional[int] = Field(None, description="Group ID")
    follow_symlinks: bool = False


class Chown(Local):
    Model = ChownModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.chown(
                        path=caller.path,
                        uid=caller.uid,
                        gid=caller.gid,
                        follow_symlinks=caller.follow_symlinks,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/chown/", tags=["SFTP Commands"])
async def chown(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Change the owner of a remote file, directory, or symlink"""
    return await router_handler(ChownModel, Chown)(
        path=path, uid=uid, gid=gid, follow_symlinks=follow_symlinks, common=common
    )


class UtimeModel(LocalPathModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )
    # todo: These descriptions are never used
    atime: int
    mtime: int
    follow_symlinks: bool = False

    @root_validator(skip_on_failure=True)
    @classmethod
    def check_atime_and_mtime(cls, values):
        """Ensure that if `atime` is specified, `mtime` is also specified."""
        atime = values.get("atime")
        mtime = values.get("mtime")
        if atime is not None and mtime is None:
            raise ValueError("If `atime` is specified, `mtime` must also be specified.")
        return values


class Utime(Local):
    Model = UtimeModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.utime(
                        path=caller.path,
                        times=(caller.atime, caller.mtime),
                        follow_symlinks=caller.follow_symlinks,
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/utime/", tags=["SFTP Commands"])
async def utime(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    atime: Optional[int] = Query(
        None, description="Access time, as seconds relative to the UNIX epoch"
    ),
    mtime: Optional[int] = Query(
        None, description="Modify time, as seconds relative to the UNIX epoch"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Change the timestamps of a remote file, directory, or symlink"""
    return await router_handler(UtimeModel, Utime)(
        path=path,
        atime=atime,
        mtime=mtime,
        follow_symlinks=follow_symlinks,
        common=common,
    )


class ChdirModel(LocalPathModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )


class Chdir(Local):
    Model = ChdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.chdir(path=caller.path)
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/chdir/", tags=["SFTP Commands"])
async def chdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path to set as the new remote working directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Change the current remote working directory"""
    return await router_handler(ChdirModel, Chdir)(path=path, common=common)


class RenameModel(LocalModel):
    oldpath: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )
    newpath: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )

    @field_validator("oldpath", "newpath", mode="before")
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


class Rename(Local):
    Model = RenameModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.rename(
                        oldpath=caller.oldpath, newpath=caller.newpath
                    )
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/rename/", tags=["SFTP Commands"])
async def rename(
    oldpath: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file, directory, or link to rename"
    ),
    newpath: Union[PurePath, str, bytes] = Query(
        ..., description="The new name for this file, directory, or link"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Rename a remote file, directory, or link"""
    return await router_handler(RenameModel, Rename)(
        oldpath=oldpath, newpath=newpath, common=common
    )


class Remove(Local):
    # todo: remove unnecessary models where its only path
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.remove(path=caller.path)
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/remove/", tags=["SFTP Commands"])
async def remove(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file or link to remove"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Remove a remote file"""
    return await router_handler(LocalPathModel, Remove)(path=path, common=common)


class WriteModel(LocalPathModel):
    text: str = Field(..., description="The text to write to the remote file")
    mode: Union[int, str] = Field("w", description="Mode")
    permissions: Optional[int] = Field(
        None,
        ge=0,
        le=0o7777,
    )
    uid: Optional[int] = Field(None, description="User ID")
    gid: Optional[int] = Field(None, description="Group ID")
    atime: Optional[int] = Field(None, description="Access time")
    mtime: Optional[int] = Field(None, description="Modification time")
    encoding: Optional[str] = Field(
        "utf-8",
        description="The Unicode encoding to use for data read and written to the remote file",
    )
    errors: Optional[str] = Field(
        "strict",
        description="The error-handling mode if an invalid Unicode byte sequence is detected, defaulting to â   strictâ    which raises an exception",
    )
    block_size: Optional[int] = Field(
        -1, description="The block size to use for read and write requests"
    )
    max_requests: Optional[int] = Field(
        -1, description="The maximum number of parallel read or write requests"
    )

    @root_validator(skip_on_failure=True)
    @classmethod
    def check_atime_and_mtime(cls, values):
        """Ensure that if `atime` is specified, `mtime` is also specified."""
        atime = values.get("atime")
        mtime = values.get("mtime")
        if atime is not None and mtime is None:
            raise ValueError("If `atime` is specified, `mtime` must also be specified.")
        return values

    # todo: rename to get_attributes
    def get_sftp_attrs(self) -> Optional[asyncssh.SFTPAttrs]:
        """Create SFTPAttrs object from provided attributes"""
        attrs_dict = {}
        if self.permissions is not None:
            attrs_dict["permissions"] = self.permissions
        if self.uid is not None:
            attrs_dict["uid"] = self.uid
        if self.gid is not None:
            attrs_dict["gid"] = self.gid
        if self.atime is not None:
            attrs_dict["atime"] = self.atime
        if self.mtime is not None:
            attrs_dict["mtime"] = self.mtime
        return asyncssh.SFTPAttrs(**attrs_dict)


class Write(Local):
    Model = WriteModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_attrs = caller.get_sftp_attrs()
                    f = await sftp.open(
                        path=caller.path,
                        pflags_or_mode=caller.mode,
                        attrs=sftp_attrs if sftp_attrs else None,
                        encoding=caller.encoding,
                        errors=caller.errors,
                        block_size=caller.block_size,
                        max_requests=caller.max_requests,
                    )
                    content = await f.write(caller.text)
                    f.close()
        except Exception as e:
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/command/write/", tags=["SFTP Commands"])
async def write(
    text: str = Query(..., description="The text to write to the remote file"),
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The name of the remote file to open"
    ),
    mode: Union[int, str] = Query("w", description="Mode"),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="File permissions as integer"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    atime: Optional[int] = Query(None, description="Access time"),
    mtime: Optional[int] = Query(None, description="Modification time"),
    encoding: Optional[str] = Query(
        "utf-8",
        description="The Unicode encoding to use for data read and written to the remote file",
    ),
    errors: Optional[str] = Query(
        "strict",
        description="The error-handling mode if an invalid Unicode byte sequence is detected, defaulting to â   strictâ    which raises an exception",
    ),
    block_size: Optional[int] = Query(
        -1, description="The block size to use for read and write requests"
    ),
    max_requests: Optional[int] = Query(
        -1, description="The maximum number of parallel read or write requests"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Open a remote file"""
    params = {"path": path}
    params["text"] = text
    if mode is not None:
        params["mode"] = mode
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
    if encoding is not None:
        params["encoding"] = encoding
    if errors is not None:
        params["errors"] = errors
    if block_size is not None:
        params["block_size"] = block_size
    if max_requests is not None:
        params["max_requests"] = max_requests
    return await router_handler(WriteModel, Write)(**params, common=common)
