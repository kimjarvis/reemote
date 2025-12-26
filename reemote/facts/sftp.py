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
                return await sftp.islink(caller.path)


@router.get("/fact/islink/", tags=["SFTP Facts"])
async def islink(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a link"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return if the remote path refers to a symbolic link"""
    return await router_handler(LocalPathModel, Islink)(path=path, common=common)


class Isfile(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isfile(caller.path)


@router.get("/fact/isfile/", tags=["SFTP Facts"])
async def isfile(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a file"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return if the remote path refers to a file"""
    return await router_handler(LocalPathModel, Isfile)(path=path, common=common)


class Isdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(caller.path)


@router.get("/fact/isdir/", tags=["SFTP Facts"])
async def isdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return if the remote path refers to a directory"""
    return await router_handler(LocalPathModel, Isdir)(path=path, common=common)


class Getsize(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getsize(caller.path)


@router.get("/fact/getsize/", tags=["SFTP Facts"])
async def getsize(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the size of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the size of a remote file or directory"""
    return await router_handler(LocalPathModel, Getsize)(path=path, common=common)


class Getatime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getatime(caller.path)


@router.get("/fact/getatime/", tags=["SFTP Facts"])
async def getatime(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the last access time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the last access time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getatime)(path=path, common=common)


class GetatimeNs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getatime_ns(caller.path)


@router.get("/fact/getatimens/", tags=["SFTP Facts"])
async def getatimens(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the last access time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the last access time of a remote file or directory"""
    return await router_handler(LocalPathModel, GetatimeNs)(path=path, common=common)


class Getmtime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getmtime(caller.path)


@router.get("/fact/getmtime/", tags=["SFTP Facts"])
async def getmtime(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="Return the last modification time of a remote file or directory",
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the last modification time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getmtime)(path=path, common=common)


class GetmtimeNs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getmtime_ns(caller.path)


@router.get("/fact/getmtimens/", tags=["SFTP Facts"])
async def getmtimens(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="Return the last modification time of a remote file or directory",
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the last modification time of a remote file or directory"""
    return await router_handler(LocalPathModel, GetmtimeNs)(path=path, common=common)


class Getcrtime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getcrtime(caller.path)


@router.get("/fact/getcrtime/", tags=["SFTP Facts"])
async def getcrtime(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the creation time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the creation time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getcrtime)(path=path, common=common)


class GetcrtimeNs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getcrtime_ns(caller.path)


@router.get("/fact/getcrtimens/", tags=["SFTP Facts"])
async def getcrtimens(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the creation time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the creation time of a remote file or directory"""
    return await router_handler(LocalPathModel, GetcrtimeNs)(path=path, common=common)


class Getcwd(Local):
    Model = LocalModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.getcwd()


@router.get("/fact/getcwd/", tags=["SFTP Facts"])
async def getcwd(common: LocalModel = Depends(localmodel)) -> list[dict]:
    """# Return the current remote working directory"""
    return await router_handler(LocalModel, Getcwd)(common=common)


class StatModel(LocalPathModel):
    follow_symlinks: bool = Field(
        True,  # Default value
    )


def sftp_attrs_to_dict(sftp_attrs):
    return {
                "uid": getattr(sftp_attrs, "uid"),
                "gid": getattr(sftp_attrs, "gid"),
                "permissions": getattr(sftp_attrs, "permissions") & 0o777,
                "atime": getattr(sftp_attrs, "atime"),
                "mtime": getattr(sftp_attrs, "mtime"),
                "size": getattr(sftp_attrs, "size"),
            }


class Stat(Local):
    Model = StatModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = await sftp.stat(
                    caller.path, follow_symlinks=caller.follow_symlinks
                )
                return sftp_attrs_to_dict(sftp_attrs)


@router.get("/fact/stat/", tags=["SFTP Facts"])
async def stat(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file or directory to get attributes for",
    ),
    follow_symlinks: bool = Query(
        True, description="Whether or not to follow symbolic links"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Get attributes of a remote file, directory, or symlink"""
    return await router_handler(StatModel, Stat)(
        path=path, follow_symlinks=follow_symlinks, common=common
    )


class ReadModel(LocalPathModel):
    encoding: Optional[str] = Field(
        "utf-8",
        description="The Unicode encoding to use for data read and written to the remote file",
    )
    errors: Optional[str] = Field(
        "strict",
        description="The error-handling mode if an invalid Unicode byte sequence is detected, defaulting to ‘strict’ which raises an exception",
    )
    block_size: Optional[int] = Field(
        -1, description="The block size to use for read and write requests"
    )
    max_requests: Optional[int] = Field(
        -1, description="The maximum number of parallel read or write requests"
    )


class Read(Local):
    Model = ReadModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                f = await sftp.open(
                    path=caller.path,
                    pflags_or_mode=FXF_READ,
                    encoding=caller.encoding,
                    errors=caller.errors,
                    block_size=caller.block_size,
                    max_requests=caller.max_requests,
                )
                content = await f.read()
                f.close()
                return content


@router.get("/command/read/", tags=["SFTP Facts"])
async def read(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The name of the remote file to read"
    ),
    encoding: Optional[str] = Query(
        "utf-8",
        description="The Unicode encoding to use for data read and written to the remote file",
    ),
    errors: Optional[str] = Query(
        "strict",
        description="The error-handling mode if an invalid Unicode byte sequence is detected, defaulting to ‘strict’ which raises an exception",
    ),
    block_size: Optional[int] = Query(
        -1, description="The block size to use for read and write requests"
    ),
    max_requests: Optional[int] = Query(
        -1, description="The maximum number of parallel read or write requests"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Read a remote file"""
    params = {"path": path}
    if encoding is not None:
        params["encoding"] = encoding
    if errors is not None:
        params["errors"] = errors
    if block_size is not None:
        params["block_size"] = block_size
    if max_requests is not None:
        params["max_requests"] = max_requests
    return await router_handler(ReadModel, Read)(**params, common=common)


class Listdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.listdir(caller.path)


@router.get("/fact/listdir/", tags=["SFTP Facts"])
async def listdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Read the names of the files in a remote directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Read the names of the files in a remote directory"""
    return await router_handler(LocalPathModel, Listdir)(path=path, common=common)

def sftp_names_to_dict(sftp_names):
    list = []
    for name in sftp_names:
        list.append(
            {
                "filename": name.filename,
                "longname": name.longname,
                "uid": getattr(name.attrs, "uid"),
                "gid": getattr(name.attrs, "gid"),
                "permissions": getattr(name.attrs, "permissions"),
                "atime": getattr(name.attrs, "atime"),
                "mtime": getattr(name.attrs, "mtime"),
                "size": getattr(name.attrs, "size"),
            }
        )
    return list


class Readdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_names = await sftp.readdir(caller.path)
                return sftp_names_to_dict(sftp_names)



@router.get("/fact/readdir/", tags=["SFTP Facts"])
async def readdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description=" The path of the remote directory to read"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Read the contents of a remote directory"""
    return await router_handler(LocalPathModel, Readdir)(path=path, common=common)


class Exists(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.exists(caller.path)


@router.get("/fact/exists/", tags=["SFTP Facts"])
async def exists(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The remote path to check"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return if the remote path exists and isn’t a broken symbolic link"""
    return await router_handler(LocalPathModel, Exists)(path=path, common=common)


class Lexists(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.lexists(caller.path)


@router.get("/fact/lexists/", tags=["SFTP Facts"])
async def exists(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The remote path to check"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return if the remote path exists, without following symbolic links"""
    return await router_handler(LocalPathModel, Lexists)(path=path, common=common)


class Lstat(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_attrs = await sftp.lstat(caller.path)
                return sftp_attrs_to_dict(sftp_attrs)


@router.get("/fact/lstat/", tags=["SFTP Facts"])
async def lstat(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file, directory, or link to get attributes for",
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Get attributes of a remote file, directory, or symlink"""
    return await router_handler(LocalPathModel, Lstat)(path=path, common=common)


class Readlink(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.readlink(caller.path)


@router.get("/fact/readlink/", tags=["SFTP Facts"])
async def readlink(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote symbolic link to follow"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the target of a remote symbolic link"""
    return await router_handler(LocalPathModel, Readlink)(path=path, common=common)


class Glob(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.glob(caller.path)


@router.get("/fact/glob/", tags=["SFTP Facts"])
async def glob(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Glob patterns to try and match remote files against"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Match remote files against glob patterns"""
    return await router_handler(LocalPathModel, Glob)(path=path, common=common)

class GlobSftpName(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_names = await sftp.glob_sftpname(caller.path)
                return sftp_names_to_dict(sftp_names)



@router.get("/fact/globsftpname/", tags=["SFTP Facts"])
async def globsftpname(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Glob patterns to try and match remote files against"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Match glob patterns and return SFTPNames"""
    return await router_handler(LocalPathModel, GlobSftpName)(path=path, common=common)

def sftp_vfs_attrs_to_dict(sftp_vfs_attrs):
    return {
                "bsize": getattr(sftp_vfs_attrs, "bsize"),
                "frsize": getattr(sftp_vfs_attrs, "frsize"),
                "blocks": getattr(sftp_vfs_attrs, "blocks"),
                "bfree": getattr(sftp_vfs_attrs, "bfree"),
                "bavail": getattr(sftp_vfs_attrs, "bavail"),
                "files": getattr(sftp_vfs_attrs, "files"),
                "ffree": getattr(sftp_vfs_attrs, "ffree"),
                "favail": getattr(sftp_vfs_attrs, "favail"),
                "fsid": getattr(sftp_vfs_attrs, "fsid"),
                "flags": getattr(sftp_vfs_attrs, "flags"),
                "namemax": getattr(sftp_vfs_attrs, "namemax"),
            }

class StatVfs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                sftp_vfs_attrs = await sftp.statvfs(
                    caller.path
                )
                return sftp_vfs_attrs_to_dict(sftp_vfs_attrs)


@router.get("/fact/statvfs/", tags=["SFTP Facts"])
async def statvfs(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file system to get attributes for",
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Get attributes of a remote file system"""
    return await router_handler(StatModel, StatVfs)(
        path=path, follow_symlinks=follow_symlinks, common=common
    )


class Realpath(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.realpath(caller.path)


@router.get("/fact/realpath/", tags=["SFTP Facts"])
async def realpath(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote directory to canonicalize"
    ),
    common: LocalModel = Depends(localmodel),
) -> list[dict]:
    """# Return the canonical version of a remote path"""
    return await router_handler(LocalPathModel, Islink)(path=path, common=common)
