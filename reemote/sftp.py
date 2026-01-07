import logging
import stat as stat_module
from pathlib import PurePath
from typing import AsyncGenerator, Callable, List, Optional, Sequence, Union

import asyncssh
from asyncssh.sftp import FXF_READ
from fastapi import APIRouter, Depends, Query
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)
from reemote.context import Context
from reemote.system import Return
from reemote.core.local import Local
from reemote.core.local import LocalModel, LocalPathModel, localmodel
from reemote.core.response import Response, ResponseElement, ResponseModel
from reemote.core.router_handler import router_handler, router_handler_put

router = APIRouter()


class IslinkResponse(ResponseElement):
    value: Union[str, bool] = Field(
        default=False,
        description="Whether or not the path is a link, or an error message",
    )


class Islink(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.islink(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/islink", tags=["SFTP Operations"], response_model=List[IslinkResponse])
async def islink(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a link"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[IslinkResponse]:
    """# Return if the remote path refers to a symbolic link"""
    return await router_handler(LocalPathModel, Islink)(path=path, common=common)


class IsfileResponse(ResponseElement):
    value: Union[str, bool] = Field(
        default=False,
        description="Whether or not the path is a file, or an error message",
    )


class Isfile(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.isfile(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/isfile", tags=["SFTP Operations"], response_model=List[IsfileResponse])
async def isfile(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a file"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[IsfileResponse]:
    """# Return if the remote path refers to a file"""
    return await router_handler(LocalPathModel, Isfile)(path=path, common=common)


class IsdirResponse(ResponseElement):
    value: Union[str, bool] = Field(
        default=False,
        description="Whether or not the path is a directory, or an error message",
    )


class Isdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.isdir(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/isdir", tags=["SFTP Operations"], response_model=List[IsdirResponse])
async def isdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[IsdirResponse]:
    """# Return if the remote path refers to a directory"""
    return await router_handler(LocalPathModel, Isdir)(path=path, common=common)


class GetsizeResponse(ResponseElement):
    value: Union[str, int] = Field(
        default=0,
        description="Size of the remove file or directory in bytes, or an error message",
    )


class Getsize(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getsize(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getsize", tags=["SFTP Operations"], response_model=List[GetsizeResponse])
async def getsize(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the size of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GetsizeResponse]:
    """# Return the size of a remote file or directory"""
    return await router_handler(LocalPathModel, Getsize)(path=path, common=common)


class GettimeResponse(ResponseElement):
    value: Union[str, int] = Field(
        default=0,
        description="The time in seconds since start of epoch, or an error message",
    )


class Getatime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getatime(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getatime", tags=["SFTP Operations"], response_model=List[GettimeResponse])
async def getatime(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the last access time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimeResponse]:
    """# Return the last access time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getatime)(path=path, common=common)


class GettimensResponse(ResponseElement):
    value: Union[str, int] = Field(
        default=0,
        description="The time in nano seconds since start of epoch, or an error message",
    )


class GetatimeNs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getatime_ns(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getatimens", tags=["SFTP Operations"], response_model=List[GettimensResponse])
async def getatimens(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the last access time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimensResponse]:
    """# Return the last access time of a remote file or directory"""
    return await router_handler(LocalPathModel, GetatimeNs)(path=path, common=common)


class Getmtime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getmtime(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getmtime", tags=["SFTP Operations"], response_model=List[GettimeResponse])
async def getmtime(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="Return the last modification time of a remote file or directory",
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimeResponse]:
    """# Return the last modification time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getmtime)(path=path, common=common)


class GetmtimeNs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getmtime_ns(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getmtimens", tags=["SFTP Operations"], response_model=List[GettimensResponse])
async def getmtimens(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="Return the last modification time of a remote file or directory",
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimensResponse]:
    """# Return the last modification time of a remote file or directory"""
    return await router_handler(LocalPathModel, GetmtimeNs)(path=path, common=common)


class Getcrtime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getcrtime(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getcrtime", tags=["SFTP Operations"], response_model=List[GettimeResponse])
async def getcrtime(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the creation time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimeResponse]:
    """# Return the creation time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getcrtime)(path=path, common=common)


class GetcrtimeNs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getcrtime_ns(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getcrtimens", tags=["SFTP Operations"], response_model=List[GettimensResponse])
async def getcrtimens(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the creation time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimensResponse]:
    """# Return the creation time of a remote file or directory"""
    return await router_handler(LocalPathModel, GetcrtimeNs)(path=path, common=common)


class GetcwdRequest(LocalModel):
    pass

class GetcwdResponse(ResponseElement):
    value: str = Field(
        default="",
        description="The path of the current working directory, or an error message",
    )


class Getcwd(Local):
    Model = GetcwdRequest

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.getcwd()
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/getcwd", tags=["SFTP Operations"], response_model=List[GetcwdResponse])
async def getcwd(common: LocalModel = Depends(localmodel)) -> List[GetcwdResponse]:
    """# Return the current remote working directory"""
    return await router_handler(LocalModel, Getcwd)(common=common)


class StatModel(LocalPathModel):
    follow_symlinks: bool = Field(
        True,  # Default value
    )


class StatAttrs(BaseModel):
    uid: int = Field(default=0, description="User id of file owner")
    gid: int = Field(default=0, description="Group id of file owner")
    permissions: int = Field(
        default=0, description="Bit mask of POSIX file permissions"
    )
    atime: int = Field(default=0, description="Last access time, UNIX epoch seconds")
    mtime: int = Field(default=0, description="Last modify time, UNIX epoch seconds")
    size: int = Field(default=0, description="File size in bytes")


class StatResponse(ResponseElement):
    value: Union[str, StatAttrs] = Field(
        default="", description="SFTP file attributes, or an error message"
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    sftp_attrs = await sftp.stat(
                        context.caller.path, follow_symlinks=context.caller.follow_symlinks
                    )
                    return sftp_attrs_to_dict(sftp_attrs)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/stat", tags=["SFTP Operations"], response_model=List[StatResponse])
async def stat(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file or directory to get attributes for",
    ),
    follow_symlinks: bool = Query(
        True, description="Whether or not to follow symbolic links"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[StatResponse]:
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


class ReadResponse(ResponseElement):
    value: str = Field(default="", description="File contents, or an error message")


class Read(Local):
    Model = ReadModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    f = await sftp.open(
                        path=context.caller.path,
                        pflags_or_mode=FXF_READ,
                        encoding=context.caller.encoding,
                        errors=context.caller.errors,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                    )
                    content = await f.read()
                    await f.close()
                    return content
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/read", tags=["SFTP Operations"], response_model=List[ReadResponse])
async def read(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The name of the remote file to read"
    ),
    encoding: Optional[str] = Query(
        "utf-8",
        description="The Unicode encoding to use for data read from the remote file",
    ),
    errors: Optional[str] = Query(
        "strict",
        description="The error-handling mode if an invalid Unicode byte sequence is detected, defaulting to ‘strict’ which raises an exception",
    ),
    block_size: Optional[int] = Query(
        -1, description="The block size to use for read requests"
    ),
    max_requests: Optional[int] = Query(
        -1, description="The maximum number of parallel read requests"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ReadResponse]:
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


class ListdirResponse(ResponseElement):
    value: Union[str, List[str]] = Field(
        default="", description="List of files in directory, or an error message"
    )


class Listdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.listdir(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/listdir", tags=["SFTP Operations"], response_model=List[ListdirResponse])
async def listdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Read the names of the files in a remote directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ListdirResponse]:
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


class SFTPFileAttributes(BaseModel):
    filename: Union[str, bytes] = Field(default="", description="Filename")
    longname: Union[str, bytes] = Field(
        default="", description="Expanded form of filename and attributes"
    )
    uid: int = Field(default=0, description="User ID of the file owner")
    gid: int = Field(default=0, description="Group ID of the file owner")
    permissions: int = Field(default=0, description="File permissions (mode)")
    atime: int = Field(default=0, description="Last access time of the file")
    mtime: int = Field(default=0, description="Last modification time of the file")
    size: int = Field(default=0, description="Size of the file in bytes")


class ReaddirResponse(ResponseElement):
    value: Union[str, List[SFTPFileAttributes]] = Field(
        default="", description="List of file entries, or an error message"
    )


class Readdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    sftp_names = await sftp.readdir(context.caller.path)
                    return sftp_names_to_dict(sftp_names)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/readdir", tags=["SFTP Operations"], response_model=List[ReaddirResponse])
async def readdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description=" The path of the remote directory to read"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ReaddirResponse]:
    """# Read the contents of a remote directory"""
    return await router_handler(LocalPathModel, Readdir)(path=path, common=common)


class ExistsResponse(ResponseElement):
    value: Union[str, bool] = Field(
        default=False,
        description="Whether or not the remote path exists, or an error message",
    )


class Exists(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.exists(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/exists", tags=["SFTP Operations"], response_model=List[ExistsResponse])
async def exists(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The remote path to check"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ExistsResponse]:
    """# Return if the remote path exists and isn’t a broken symbolic link"""
    return await router_handler(LocalPathModel, Exists)(path=path, common=common)


class Lexists(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.lexists(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/lexists", tags=["SFTP Operations"], response_model=List[ExistsResponse])
async def lexists(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The remote path to check"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ExistsResponse]:
    """# Return if the remote path exists, without following symbolic links"""
    return await router_handler(LocalPathModel, Lexists)(path=path, common=common)


class Lstat(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    sftp_attrs = await sftp.lstat(context.caller.path)
                    return sftp_attrs_to_dict(sftp_attrs)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/lstat", tags=["SFTP Operations"], response_model=List[StatResponse])
async def lstat(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file, directory, or link to get attributes for",
    ),
    common: LocalModel = Depends(localmodel),
) -> List[StatResponse]:
    """# Get attributes of a remote file, directory, or symlink"""
    return await router_handler(LocalPathModel, Lstat)(path=path, common=common)


class ReadlinkResponse(ResponseElement):
    value: str = Field(
        default="",
        description="Target of the remote symbolic link, or an error message",
    )


class Readlink(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.readlink(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/readlink", tags=["SFTP Operations"], response_model=List[ReadlinkResponse])
async def readlink(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote symbolic link to follow"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ReadlinkResponse]:
    """# Return the target of a remote symbolic link"""
    return await router_handler(LocalPathModel, Readlink)(path=path, common=common)


class GlobResponse(ResponseElement):
    value: Union[str, List[str]] = Field(
        default="", description="File paths matching the pattern, or an error message"
    )


class Glob(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.glob(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/glob", tags=["SFTP Operations"], response_model=List[GlobResponse])
async def glob(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Glob patterns to try and match remote files against"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GlobResponse]:
    """# Match remote files against glob patterns"""
    return await router_handler(LocalPathModel, Glob)(path=path, common=common)


class GlobSftpName(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    sftp_names = await sftp.glob_sftpname(context.caller.path)
                    return sftp_names_to_dict(sftp_names)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/globsftpname", tags=["SFTP Operations"], response_model=List[SFTPFileAttributes])
async def globsftpname(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Glob patterns to try and match remote files against"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[SFTPFileAttributes]:
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


# Define the Pydantic model for the response schema (without examples)
class SFTPVFSAttrsResponse(BaseModel):
    bsize: int = Field(default=0, description="File system block size (I/O size)")
    frsize: int = Field(
        default=0, description="Fundamental block size (allocation size)"
    )
    blocks: int = Field(default=0, description="Total data blocks (in frsize units)")
    bfree: int = Field(default=0, description="Free data blocks")
    bavail: int = Field(default=0, description="Available data blocks (for non-root)")
    files: int = Field(default=0, description="Total file inodes")
    ffree: int = Field(default=0, description="Free file inodes")
    favail: int = Field(default=0, description="Available file inodes (for non-root)")
    fsid: int = Field(default=0, description="File system id")
    flags: int = Field(
        default=0, description="File system flags (read-only, no-setuid)"
    )
    namemax: int = Field(default=0, description="Maximum filename length")


class StatVfsResponse(ResponseElement):
    value: Union[str, SFTPVFSAttrsResponse] = Field(
        default="",
        description="The response containing file system attributes, or an error message",
    )


class StatVfs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_vfs_attrs = await sftp.statvfs(context.caller.path)
                    context.changed = False
                    return sftp_vfs_attrs_to_dict(sftp_vfs_attrs)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/statvfs", tags=["SFTP Operations"], response_model=List[StatVfsResponse])
async def statvfs(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file system to get attributes for",
    ),
    common: LocalModel = Depends(localmodel),
) -> List[StatVfsResponse]:
    """# Get attributes of a remote file system"""
    return await router_handler(StatModel, StatVfs)(path=path, common=common)


class RealpathResponse(ResponseElement):
    value: str = Field(
        default="", description="Canonicalized directory path, or an error message"
    )


class Realpath(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return await sftp.realpath(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/realpath", tags=["SFTP Operations"], response_model=List[RealpathResponse])
async def realpath(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote directory to canonicalize"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[RealpathResponse]:
    """# Return the canonical version of a remote path"""
    return await router_handler(LocalPathModel, Realpath)(path=path, common=common)


class ClientResponse(LocalModel):
    pass

class Client(Local):
    Model = ClientResponse

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    context.changed = False
                    return {
                        "version": sftp.version,
                        # "logger": sftp.logger,
                        "max_packet_len": sftp.limits.max_packet_len,
                        "max_read_len": sftp.limits.max_read_len,
                        "max_write_len": sftp.limits.max_write_len,
                        "max_open_handles": sftp.limits.max_open_handles,
                        "supports_remote_copy": sftp.supports_remote_copy,
                    }
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


class SFTPInfo(BaseModel):
    version: int = Field(
        default=0, description="SFTP version associated with this SFTP session"
    )
    # logger: Optional[asyncssh.logging.SSHLogger] = Field(
    #     None, description="Logger associated with this SFTP client"
    # )
    max_packet_len: int = Field(
        default=0, description="Max allowed size of an SFTP packet"
    )
    max_read_len: int = Field(
        default=0, description="Max allowed size of an SFTP read request"
    )
    max_write_len: int = Field(
        default=0, description="Max allowed size of an SFTP write request"
    )
    max_open_handles: int = Field(
        default=0, description="Max allowed number of open file handles"
    )
    supports_remote_copy: bool = Field(
        default=0, description="Return whether or not SFTP remote copy is supported"
    )


class ClientResponse(ResponseElement):
    value: Union[str, SFTPInfo] = Field(
        default="", description="SFTP Information, or an error message"
    )


@router.get("/client", tags=["SFTP Operations"], response_model=List[ClientResponse])
async def client(
    common: LocalModel = Depends(localmodel),
) -> List[ClientResponse]:
    """# Return sftp client information"""
    return await router_handler(LocalModel, Client)(common=common)


class CopyRequestModel(LocalModel):
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


class McopyModel(CopyRequestModel):
    pass


class Copy(Local):
    Model = CopyRequestModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.copy(
                        srcpaths=context.caller.srcpaths,
                        dstpath=context.caller.dstpath,
                        preserve=context.caller.preserve,
                        recurse=context.caller.recurse,
                        follow_symlinks=context.caller.follow_symlinks,
                        sparse=context.caller.sparse,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                        progress_handler=context.caller.progress_handler,
                        error_handler=context.caller.error_handler,
                        remote_only=context.caller.remote_only,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


class Mcopy(Local):
    Model = McopyModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.mcopy(
                        srcpaths=context.caller.srcpaths,
                        dstpath=context.caller.dstpath,
                        preserve=context.caller.preserve,
                        recurse=context.caller.recurse,
                        follow_symlinks=context.caller.follow_symlinks,
                        sparse=context.caller.sparse,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                        progress_handler=context.caller.progress_handler,
                        error_handler=context.caller.error_handler,
                        remote_only=context.caller.remote_only,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/copy", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
    """# Copy remote files to a new location"""
    return await router_handler(CopyRequestModel, Copy)(
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


@router.post("/mcopy", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
    """# Copy remote files to a new location"""
    return await router_handler(CopyRequestModel, Mcopy)(
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.get(
                        remotepaths=context.caller.remotepaths,
                        localpath=context.caller.localpath,
                        preserve=context.caller.preserve,
                        recurse=context.caller.recurse,
                        follow_symlinks=context.caller.follow_symlinks,
                        sparse=context.caller.sparse,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                        progress_handler=context.caller.progress_handler,
                        error_handler=context.caller.error_handler,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


class Mget(Local):
    Model = MgetModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.mget(
                        remotepaths=context.caller.remotepaths,
                        localpath=context.caller.localpath,
                        preserve=context.caller.preserve,
                        recurse=context.caller.recurse,
                        follow_symlinks=context.caller.follow_symlinks,
                        sparse=context.caller.sparse,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                        progress_handler=context.caller.progress_handler,
                        error_handler=context.caller.error_handler,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/get", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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


@router.post("/mget", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.put(
                        localpaths=context.caller.localpaths,
                        remotepath=context.caller.remotepath,
                        preserve=context.caller.preserve,
                        recurse=context.caller.recurse,
                        follow_symlinks=context.caller.follow_symlinks,
                        sparse=context.caller.sparse,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                        progress_handler=context.caller.progress_handler,
                        error_handler=context.caller.error_handler,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


class Mput(Local):
    Model = MputModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.mput(
                        localpaths=context.caller.localpaths,
                        remotepath=context.caller.remotepath,
                        preserve=context.caller.preserve,
                        recurse=context.caller.recurse,
                        follow_symlinks=context.caller.follow_symlinks,
                        sparse=context.caller.sparse,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                        progress_handler=context.caller.progress_handler,
                        error_handler=context.caller.error_handler,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/put/", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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


@router.post("/mput", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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

    @model_validator(mode='before')
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_attrs = context.caller.get_sftp_attrs()
                    if sftp_attrs:
                        await sftp.mkdir(
                            path=context.caller.path, attrs=sftp_attrs if sftp_attrs else None
                        )
                    else:
                        await sftp.mkdir(path=context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/mkdir", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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


class Setstat(Local):
    Model = MkdirModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_attrs = context.caller.get_sftp_attrs()
                    if sftp_attrs:
                        await sftp.setstat(path=context.caller.path, attrs=sftp_attrs)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/setstat", tags=["SFTP Operations"], response_model=ResponseModel)
async def mkdir(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file or directory to set attributes for",
    ),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="Directory permissions as integer"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    atime: Optional[int] = Query(None, description="Access time"),
    mtime: Optional[int] = Query(None, description="Modification time"),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Set attributes of a remote file, directory, or symlink"""
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
    return await router_handler(MkdirModel, Setstat)(**params, common=common)


class Makedirs(Local):
    Model = MkdirModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_attrs = context.caller.get_sftp_attrs()
                    if sftp_attrs:
                        await sftp.makedirs(
                            path=context.caller.path, attrs=sftp_attrs if sftp_attrs else None
                        )
                    else:
                        await sftp.makedirs(path=context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/makedirs", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.rmdir(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/rmdir", tags=["SFTP Operations"], response_model=ResponseModel)
async def rmdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote directory to remove"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Remove a remote directory"""
    return await router_handler(LocalPathModel, Rmdir)(path=path, common=common)


class Rmtree(Local):
    # todo: There are other parameters
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.rmtree(context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/rmtree", tags=["SFTP Operations"], response_model=ResponseModel)
async def rmtree(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the parent directory to remove"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.chmod(
                        path=context.caller.path,
                        mode=context.caller.permissions,
                        follow_symlinks=context.caller.follow_symlinks,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/chmod", tags=["SFTP Operations"], response_model=ResponseModel)
async def chmod(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="Directory permissions as integer"
    ),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.chown(
                        path=context.caller.path,
                        uid=context.caller.uid,
                        gid=context.caller.gid,
                        follow_symlinks=context.caller.follow_symlinks,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/chown", tags=["SFTP Operations"], response_model=ResponseModel)
async def chown(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
    follow_symlinks: bool = Query(
        False, description="Whether or not to follow symbolic links"
    ),
    uid: Optional[int] = Query(None, description="User ID"),
    gid: Optional[int] = Query(None, description="Group ID"),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
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

    @model_validator(mode='before')
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.utime(
                        path=context.caller.path,
                        times=(context.caller.atime, context.caller.mtime),
                        follow_symlinks=context.caller.follow_symlinks,
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/utime", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.chdir(path=context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/chdir", tags=["SFTP Operations"], response_model=ResponseModel)
async def chdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path to set as the new remote working directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.rename(
                        oldpath=context.caller.oldpath, newpath=context.caller.newpath
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/rename", tags=["SFTP Operations"], response_model=ResponseModel)
async def rename(
    oldpath: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file, directory, or link to rename"
    ),
    newpath: Union[PurePath, str, bytes] = Query(
        ..., description="The new name for this file, directory, or link"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Rename a remote file, directory, or link"""
    return await router_handler(RenameModel, Rename)(
        oldpath=oldpath, newpath=newpath, common=common
    )


class Remove(Local):
    # todo: remove unnecessary models where its only path
    Model = LocalPathModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.remove(path=context.caller.path)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/remove", tags=["SFTP Operations"], response_model=ResponseModel)
async def remove(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file or link to remove"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
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

    @model_validator(mode='before')
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
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_attrs = context.caller.get_sftp_attrs()
                    f = await sftp.open(
                        path=context.caller.path,
                        pflags_or_mode=context.caller.mode,
                        attrs=sftp_attrs if sftp_attrs else None,
                        encoding=context.caller.encoding,
                        errors=context.caller.errors,
                        block_size=context.caller.block_size,
                        max_requests=context.caller.max_requests,
                    )
                    content = await f.write(context.caller.text)
                    await f.close()
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/write", tags=["SFTP Operations"], response_model=ResponseModel)
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
) -> ResponseModel:
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


class LinkModel(LocalModel):
    file_path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )
    link_path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )

    @field_validator("file_path", "link_path", mode="before")
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


class Link(Local):
    Model = LinkModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.link(
                        oldpath=context.caller.file_path, newpath=context.caller.link_path
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/link", tags=["SFTP Operations"], response_model=ResponseModel)
async def link(
    file_path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file the hard link should point to"
    ),
    link_path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of where to create the remote hard link"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Rename a remote file, directory, or link"""
    return await router_handler(LinkModel, Link)(
        file_path=file_path, link_path=link_path, common=common
    )


class Symlink(Local):
    Model = LinkModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.symlink(
                        oldpath=context.caller.file_path, newpath=context.caller.link_path
                    )
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/symlink", tags=["SFTP Operations"], response_model=ResponseModel)
async def symlink(
    file_path: Union[PurePath, str, bytes] = Query(
        ..., description="The path the link should point to"
    ),
    link_path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of where to create the remote symbolic link"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Create a remote symbolic link"""
    return await router_handler(LinkModel, Symlink)(
        file_path=file_path, link_path=link_path, common=common
    )


class Unlink(Remove):
    pass


@router.post("/unlink", tags=["SFTP Operations"], response_model=ResponseModel)
async def remove(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote link to remove"
    ),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Remove a remote link"""
    return await router_handler(LocalPathModel, Remove)(path=path, common=common)


class TruncateModel(LocalPathModel):
    size: int = Field(
        ...,  # Required field
        description="The desired size of the file, in bytes",
    )


class Truncate(Local):
    Model = TruncateModel

    @staticmethod
    async def _callback(context: Context):
        try:
            async with asyncssh.connect(**context.inventory_item.connection.to_json_serializable()) as conn:
                async with conn.start_sftp_client() as sftp:
                    return await sftp.truncate(path=context.caller.file_path, size=context.caller.size)
        except Exception as e:
            context.error = True
            logging.error(f"{context.inventory_item.connection.host}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.post("/truncate", tags=["SFTP Operations"], response_model=ResponseModel)
async def truncate(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote file to be truncated"
    ),
    size: int = Query(..., description=" The desired size of the file, in bytes"),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Truncate a remote file to the specified size"""
    return await router_handler(LinkModel, Symlink)(path=path, size=size, common=common)


class DirectoryRequestModel(LocalPathModel):
    present: bool = True
    permissions: Optional[int] = Field(None, ge=0, le=0o7777)
    uid: Optional[int] = Field(None, ge=0)
    gid: Optional[int] = Field(None, ge=0)
    atime: Optional[int] = Field(None, ge=0)
    mtime: Optional[int] = Field(None, ge=0)


class Directory(Local):
    Model = DirectoryRequestModel

    async def execute(
        self,
    ) -> AsyncGenerator[
        Isdir | Rmdir | Mkdir | Stat | Chmod | Chown | Utime | Return, Response
    ]:
        model_instance = self.Model.model_validate(self.kwargs)

        changed = False
        isdir = yield Isdir(path=model_instance.path, group=model_instance.group)
        if isdir:
            if not model_instance.present and not isdir["value"]:
                changed = False
            elif not model_instance.present and isdir["value"]:
                yield Rmdir(path=model_instance.path, group=model_instance.group)
                changed = True
            elif model_instance.present and not isdir["value"]:
                yield Mkdir(path=model_instance.path,
                            permissions=model_instance.permissions,
                            atime=model_instance.atime,
                            mtime=model_instance.mtime,
                            group=model_instance.group)
                changed = True
            elif model_instance.present and isdir["value"]:
                r = yield Stat(path="/home/user/freddy", group=model_instance.group)
                if (
                    model_instance.permissions
                    and r["value"]["permissions"] != model_instance.permissions
                ):
                    yield Chmod(
                        path=model_instance.path,
                        permissions=model_instance.permissions,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.uid and r["value"]["uid"] != model_instance.uid:
                    yield Chown(
                        path=model_instance.path,
                        uid=model_instance.uid,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.uid and r["value"]["gid"] != model_instance.gid:
                    yield Chown(
                        path=model_instance.path,
                        gid=model_instance.gid,
                        group=model_instance.group,
                    )
                    changed = True
                if model_instance.atime and r["value"]["atime"] != model_instance.atime:
                    yield Utime(
                        path=model_instance.path,
                        atime=model_instance.atime,
                        mtime=model_instance.mtime,
                        group=model_instance.group,
                    )
                    changed = True
            yield Return(value=None, changed=changed)


@router.put("/directory", tags=["SFTP Operations"], response_model=ResponseModel)
async def directory(
    path: Union[PurePath, str, bytes] = Query(..., description="Directory path on the remote host", examples=["/home/user","testdata/new_dir"]),
    present: Optional[bool] = Query(...,
        description="Whether the directory should be present or not"
    ),
    permissions: Optional[int] = Query(
        None, ge=0, le=0o7777, description="Directory permissions as integer", examples=["0o644"]
    ),
    uid: Optional[int] = Query(None, description="User ID, user must exist on the remote host"),
    gid: Optional[int] = Query(None, description="Group ID, user must exist on the remote host"),
    atime: Optional[int] = Query(None, description="Access time in seconds since epoch", examples=["0xDEADCAFE"]),
    mtime: Optional[int] = Query(None, description="Modification time in seconds since epoch", examples=["0xACAFEDAD"]),
    common: LocalModel = Depends(localmodel),
) -> ResponseModel:
    """# Ensure directory exists"""
    params = {"path": path, "present": present}
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
    return await router_handler_put(DirectoryRequestModel, Directory)(
        **params,
        common=common,
    )
