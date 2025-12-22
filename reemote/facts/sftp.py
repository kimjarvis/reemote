from pathlib import PurePath
from typing import Union, Optional

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator

from reemote.router_handler import router_handler
from reemote.models import LocalModel, localmodel, LocalPathModel
from reemote.local import Local
from asyncssh.sftp import FXF_READ
from pydantic import BaseModel, Field, field_validator, ValidationError, root_validator


router = APIRouter()


class Islink(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.islink(str(caller.path))


@router.get("/fact/islink/", tags=["SFTP Facts"])
async def islink(
        path: Union[PurePath, str, bytes] = Query(..., description="Path to check if it's a link"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return if the remote path refers to a symbolic link"""
    return await router_handler(LocalPathModel, Islink)(path=path, common=common)

class Isfile(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isfile(str(caller.path))

@router.get("/fact/isfile/", tags=["SFTP Facts"])
async def isfile(
        path: Union[PurePath, str, bytes] = Query(..., description="Path to check if it's a file"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return if the remote path refers to a file"""
    return await router_handler(LocalPathModel, Isfile)(path=path, common=common)

class Isdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(str(caller.path))


@router.get("/fact/isdir/", tags=["SFTP Facts"])
async def isdir(
        path: Union[PurePath, str, bytes] = Query(..., description="Path to check if it's a directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return if the remote path refers to a directory"""
    return await router_handler(LocalPathModel, Isdir)(path=path, common=common)



class Getsize(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getsize(str(caller.path))

@router.get("/fact/getsize/", tags=["SFTP Facts"])
async def getsize(
        path: Union[PurePath, str, bytes] = Query(..., description="Return the size of a remote file or directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the size of a remote file or directory"""
    return await router_handler(LocalPathModel, Getsize)(path=path, common=common)


class Getatime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getatime(str(caller.path))


@router.get("/fact/getatime/", tags=["SFTP Facts"])
async def getatime(
        path: Union[PurePath, str, bytes] = Query(..., description="Return the last access time of a remote file or directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the last access time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getatime)(path=path, common=common)


class Getatime_ns(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getatime_ns(str(caller.path))

@router.get("/fact/getatime_ns/", tags=["SFTP Facts"])
async def getatime_ns(
        path: Union[PurePath, str, bytes] = Query(..., description="Return the last access time of a remote file or directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the last access time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getatime_ns)(path=path, common=common)


class Getmtime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getmtime(str(caller.path))

@router.get("/fact/getmtime/", tags=["SFTP Facts"])
async def getmtime(
        path: Union[PurePath, str, bytes] = Query(..., description="Return the last modification time of a remote file or directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the last modification time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getmtime)(path=path, common=common)


class Getmtime_ns(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getmtime_ns(str(caller.path))

@router.get("/fact/getmtime_ns/", tags=["SFTP Facts"])
async def getmtime(
        path: Union[PurePath, str, bytes] = Query(..., description="Return the last modification time of a remote file or directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the last modification time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getmtime_ns)(path=path, common=common)


class Getcrtime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getcrtime(str(caller.path))


@router.get("/fact/getcrtime/", tags=["SFTP Facts"])
async def getcrtime(
        path: Union[PurePath, str, bytes] = Query(..., description="Return the creation time of a remote file or directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the creation time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getcrtime)(path=path, common=common)


class Getcrtime_ns(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getcrtime_ns(str(caller.path))

@router.get("/fact/getcrtime_ns/", tags=["SFTP Facts"])
async def getcrtime_ns(
        path: Union[PurePath, str, bytes] = Query(..., description="Return the creation time of a remote file or directory"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the creation time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getcrtime_ns)(path=path, common=common)


class Getcwd(Local):
    Model = LocalModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getcwd()

@router.get("/fact/getcwd/", tags=["SFTP Facts"])
async def getcwd(
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Return the current remote working directory"""
    return await router_handler(LocalModel, Getcwd)(common=common)


class StatModel(LocalPathModel):
    follow_symlinks: bool = Field(
        True,  # Default value
    )

class Stat(Local):
    Model = StatModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = await sftp.stat(caller.path, follow_symlinks=caller.follow_symlinks)

                # Extract each field from the SFTPAttrs object and create the dictionary
                attrs_dict = {
                    'uid': getattr(sftp_attrs, 'uid'),
                    'gid': getattr(sftp_attrs, 'gid'),
                    'permissions': getattr(sftp_attrs, 'permissions') & 0o777,
                    'atime': getattr(sftp_attrs, 'atime'),
                    'mtime': getattr(sftp_attrs, 'mtime'),
                    'size': getattr(sftp_attrs, 'size')
                }

                # print(attrs_dict)
                return attrs_dict

@router.get("/fact/stat/", tags=["SFTP Facts"])
async def stat(
        path: Union[PurePath, str, bytes] = Query(..., description="The path of the remote file or directory to get attributes for"),
        follow_symlinks: bool = Query(True, description="Whether or not to follow symbolic links"),
        common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Get attributes of a remote file, directory, or symlink"""
    return await router_handler(StatModel, Stat)(path=path, follow_symlinks=follow_symlinks, common=common)










class ReadModel(LocalPathModel):
    permissions: Optional[int] = Field(
        None,
        ge=0,
        le=0o7777,
    )
    uid: Optional[int] = Field(None, description="User ID")
    gid: Optional[int] = Field(None, description="Group ID")
    atime: Optional[float] = Field(None, description="Access time")
    mtime: Optional[float] = Field(None, description="Modification time")
    encoding: Optional[str] = Field('utf-8', description="The Unicode encoding to use for data read and written to the remote file")
    errors: Optional[str] = Field('strict', description="The error-handling mode if an invalid Unicode byte sequence is detected, defaulting to ‘strict’ which raises an exception")
    block_size: Optional[int] = Field(-1, description="The block size to use for read and write requests")
    max_requests: Optional[int] = Field(-1, description="The maximum number of parallel read or write requests")

    @root_validator(skip_on_failure=True)
    @classmethod
    def check_atime_and_mtime(cls, values):
        """Ensure that if `atime` is specified, `mtime` is also specified."""
        atime = values.get('atime')
        mtime = values.get('mtime')

        if atime is not None and mtime is None:
            raise ValueError("If `atime` is specified, `mtime` must also be specified.")
        return values

    # todo: rename to get_attributes
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

        return asyncssh.SFTPAttrs(**attrs_dict)

class Read(Local):
    Model = ReadModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = caller.get_sftp_attrs()
                f = await sftp.open(path=caller.path, pflags_or_mode=FXF_READ , attrs=sftp_attrs if sftp_attrs else None,
                                   encoding=caller.encoding, errors=caller.errors,
                                   block_size=caller.block_size, max_requests=caller.max_requests)
                content = await f.read()
                f.close()
                return content

@router.get("/command/read/", tags=["SFTP Facts"])
async def read(
    path: Union[PurePath, str, bytes] = Query(..., description="The name of the remote file to read"),
    permissions: Optional[int] = Query(
        None,
        ge=0,
        le=0o7777,
        description="File permissions as integer"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    atime: Optional[float] = Query(None, description="Access time"),
    mtime: Optional[float] = Query(None, description="Modification time"),
    encoding: Optional[str] = Query('utf-8', description="The Unicode encoding to use for data read and written to the remote file"),
    errors: Optional[str] = Query('strict', description="The error-handling mode if an invalid Unicode byte sequence is detected, defaulting to ‘strict’ which raises an exception"),
    block_size: Optional[int] = Query(-1, description="The block size to use for read and write requests"),
    max_requests: Optional[int] = Query(-1, description="The maximum number of parallel read or write requests"),
    common: LocalModel = Depends(localmodel)
) -> list[dict]:
    """# Read a remote file"""
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
    if encoding is not None:
        params["encoding"] = encoding
    if errors is not None:
        params["errors"] = errors
    if block_size is not None:
        params["block_size"] = block_size
    if max_requests is not None:
        params["max_requests"] = max_requests
    return await router_handler(ReadModel, Read)(**params, common=common)



