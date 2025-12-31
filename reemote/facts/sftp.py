from pathlib import PurePath
from typing import List, Union, Optional, Any
import logging
import asyncssh
from fastapi import APIRouter, Depends, Query
from reemote.router_handler import router_handler
from reemote.models import LocalModel, localmodel, LocalPathModel
from reemote.local import Local
from asyncssh.sftp import FXF_READ
from pydantic import BaseModel, Field, field_validator
from pydantic import BaseModel, Field, field_validator, TypeAdapter, ConfigDict, field_serializer, ValidationInfo
from typing import Optional, List
from fastapi import Depends
from reemote.response import ResponseElement


router = APIRouter()


class IslinkResponse(ResponseElement):
    value: Union[str,  bool ] = Field(default=False, description="Whether or not the path is a link, or an error message")

class Islink(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.islink(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/islink", tags=["SFTP Facts"], response_model=List[IslinkResponse])
async def islink(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a link"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[IslinkResponse]:
    """# Return if the remote path refers to a symbolic link"""
    return await router_handler(LocalPathModel, Islink)(path=path, common=common)

class IsfileResponse(ResponseElement):
    value: Union[str,  bool] = Field(default=False, description="Whether or not the path is a file, or an error message")

class Isfile(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.isfile(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/isfile", tags=["SFTP Facts"], response_model=List[IsfileResponse])
async def isfile(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a file"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[IsfileResponse]:
    """# Return if the remote path refers to a file"""
    return await router_handler(LocalPathModel, Isfile)(path=path, common=common)


class IsdirResponse(ResponseElement):
    value: Union[str,  bool] = Field(default=False, description="Whether or not the path is a directory, or an error message")

class Isdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.isdir(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/isdir", tags=["SFTP Facts"], response_model=List[IsdirResponse])
async def isdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Path to check if it's a directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[IsdirResponse]:
    """# Return if the remote path refers to a directory"""
    return await router_handler(LocalPathModel, Isdir)(path=path, common=common)


class GetsizeResponse(ResponseElement):
    value: Union[str,  int] = Field(default=0, description="Size of the remove file or directory in bytes, or an error message")

class Getsize(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getsize(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getsize", tags=["SFTP Facts"], response_model=List[GetsizeResponse])
async def getsize(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the size of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GetsizeResponse]:
    """# Return the size of a remote file or directory"""
    return await router_handler(LocalPathModel, Getsize)(path=path, common=common)

class GettimeResponse(ResponseElement):
    value: Union[str,  int ]= Field(default=0, description="The time in seconds since start of epoch, or an error message")

class Getatime(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getatime(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getatime", tags=["SFTP Facts"],response_model=List[GettimeResponse])
async def getatime(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the last access time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimeResponse]:
    """# Return the last access time of a remote file or directory"""
    return await router_handler(LocalPathModel, Getatime)(path=path, common=common)

class GettimensResponse(ResponseElement):
    value: Union[str,  int ]= Field(default=0, description="The time in nano seconds since start of epoch, or an error message")

class GetatimeNs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getatime_ns(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getatimens", tags=["SFTP Facts"], response_model=List[GettimensResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getmtime(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getmtime", tags=["SFTP Facts"], response_model=List[GettimeResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getmtime_ns(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getmtimens", tags=["SFTP Facts"], response_model=List[GettimensResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getcrtime(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getcrtime", tags=["SFTP Facts"], response_model=List[GettimeResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getcrtime_ns(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getcrtimens", tags=["SFTP Facts"], response_model=List[GettimensResponse])
async def getcrtimens(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="Return the creation time of a remote file or directory"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[GettimensResponse]:
    """# Return the creation time of a remote file or directory"""
    return await router_handler(LocalPathModel, GetcrtimeNs)(path=path, common=common)


class GetcwdResponse(ResponseElement):
    value: str = Field(default="", description="The path of the current working directory, or an error message")

class Getcwd(Local):
    Model = LocalModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.getcwd()
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/getcwd", tags=["SFTP Facts"], response_model=List[GetcwdResponse])
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
    permissions: int = Field(default=0, description="Bit mask of POSIX file permissions")
    atime: int = Field(default=0, description="Last access time, UNIX epoch seconds")
    mtime: int = Field(default=0, description="Last modify time, UNIX epoch seconds")
    size: int = Field(default=0, description="File size in bytes")


class StatResponse(ResponseElement):
    value: Union[str,  StatAttrs] = Field(default="", description="SFTP file attributes, or an error message")

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
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    sftp_attrs = await sftp.stat(
                        caller.path, follow_symlinks=caller.follow_symlinks
                    )
                    return sftp_attrs_to_dict(sftp_attrs)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/stat", tags=["SFTP Facts"], response_model=List[StatResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
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
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"


@router.get("/read", tags=["SFTP Facts"], response_model=List[ReadResponse])
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
    value: Union[str,  List[str]] = Field(default="", description="List of files in directory, or an error message")

class Listdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.listdir(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/listdir", tags=["SFTP Facts"], response_model=List[ListdirResponse])
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
    filename: Union[str, bytes] = Field(default="",description="Filename")
    longname: Union[str, bytes] = Field(default="",description="Expanded form of filename and attributes")
    uid: int = Field(default=0,description="User ID of the file owner")
    gid: int = Field(default=0,description="Group ID of the file owner")
    permissions: int = Field(default=0,description="File permissions (mode)")
    atime: int = Field(default=0,description="Last access time of the file")
    mtime: int = Field(default=0,description="Last modification time of the file")
    size: int = Field(default=0,description="Size of the file in bytes")

class ReaddirResponse(ResponseElement):
    value: Union[str,  List[SFTPFileAttributes]] = Field(default="", description="List of file entries, or an error message")

class Readdir(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    sftp_names = await sftp.readdir(caller.path)
                    return sftp_names_to_dict(sftp_names)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/readdir", tags=["SFTP Facts"], response_model=List[ReaddirResponse])
async def readdir(
    path: Union[PurePath, str, bytes] = Query(
        ..., description=" The path of the remote directory to read"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ReaddirResponse]:
    """# Read the contents of a remote directory"""
    return await router_handler(LocalPathModel, Readdir)(path=path, common=common)

class ExistsResponse(ResponseElement):
    value: Union[str,  bool ]= Field(default=False, description="Whether or not the remote path exists, or an error message")

class Exists(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.exists(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/exists", tags=["SFTP Facts"], response_model=List[ExistsResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.lexists(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/lexists", tags=["SFTP Facts"], response_model=List[ExistsResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    sftp_attrs = await sftp.lstat(caller.path)
                    return sftp_attrs_to_dict(sftp_attrs)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/lstat", tags=["SFTP Facts"], response_model=List[StatResponse])
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
    value: str = Field(default="", description="Target of the remote symbolic link, or an error message")

class Readlink(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.readlink(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/readlink", tags=["SFTP Facts"], response_model=List[ReadlinkResponse])
async def readlink(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote symbolic link to follow"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[ReadlinkResponse]:
    """# Return the target of a remote symbolic link"""
    return await router_handler(LocalPathModel, Readlink)(path=path, common=common)


class GlobResponse(ResponseElement):
    value: Union[str,  List[str]] = Field(default="", description="File paths matching the pattern, or an error message")

class Glob(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.glob(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/glob", tags=["SFTP Facts"], response_model=List[GlobResponse])
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
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    sftp_names = await sftp.glob_sftpname(caller.path)
                    return sftp_names_to_dict(sftp_names)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/globsftpname", tags=["SFTP Facts"], response_model=List[SFTPFileAttributes])
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
    bsize: int = Field(default=0,description="File system block size (I/O size)")
    frsize: int = Field(default=0,description="Fundamental block size (allocation size)")
    blocks: int = Field(default=0,description="Total data blocks (in frsize units)")
    bfree: int = Field(default=0,description="Free data blocks")
    bavail: int = Field(default=0,description="Available data blocks (for non-root)")
    files: int = Field(default=0,description="Total file inodes")
    ffree: int = Field(default=0,description="Free file inodes")
    favail: int = Field(default=0,description="Available file inodes (for non-root)")
    fsid: int = Field(default=0,description="File system id")
    flags: int = Field(default=0,description="File system flags (read-only, no-setuid)")
    namemax: int = Field(default=0,description="Maximum filename length")


class StatVfsResponse(ResponseElement):
     value: Union[str,  SFTPVFSAttrsResponse] = Field(default="", description="The response containing file system attributes, or an error message")

class StatVfs(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    sftp_vfs_attrs = await sftp.statvfs(caller.path)
                    command.changed = False
                    return sftp_vfs_attrs_to_dict(sftp_vfs_attrs)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/statvfs", tags=["SFTP Facts"], response_model=List[StatVfsResponse])
async def statvfs(
    path: Union[PurePath, str, bytes] = Query(
        ...,
        description="The path of the remote file system to get attributes for",
    ),
    common: LocalModel = Depends(localmodel),
) -> List[StatVfsResponse]:
    """# Get attributes of a remote file system"""
    return await router_handler(StatModel, StatVfs)(
        path=path, common=common
    )


class RealpathResponse(ResponseElement):
    value: str = Field(default="", description="Canonicalized directory path, or an error message")


class Realpath(Local):
    Model = LocalPathModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
                    return await sftp.realpath(caller.path)
        except Exception as e:
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

@router.get("/realpath", tags=["SFTP Facts"], response_model=List[RealpathResponse])
async def realpath(
    path: Union[PurePath, str, bytes] = Query(
        ..., description="The path of the remote directory to canonicalize"
    ),
    common: LocalModel = Depends(localmodel),
) -> List[RealpathResponse]:
    """# Return the canonical version of a remote path"""
    return await router_handler(LocalPathModel, Realpath)(path=path, common=common)


class Client(Local):
    Model = LocalModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    command.changed = False
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
            command.error = True
            logging.error(f"{host_info['host']}: {e.__class__.__name__}")
            return f"{e.__class__.__name__}"

class SFTPInfo(BaseModel):
    version: int = Field(default=0,description="SFTP version associated with this SFTP session")
    # logger: Optional[asyncssh.logging.SSHLogger] = Field(
    #     None, description="Logger associated with this SFTP client"
    # )
    max_packet_len: int = Field(default=0,description="Max allowed size of an SFTP packet")
    max_read_len: int = Field(default=0,description="Max allowed size of an SFTP read request")
    max_write_len: int = Field(default=0,description="Max allowed size of an SFTP write request")
    max_open_handles: int = Field(default=0,description="Max allowed number of open file handles")
    supports_remote_copy: bool = Field(default=0,
        description="Return whether or not SFTP remote copy is supported"
    )

class ClientResponse(ResponseElement):
    value: Union[str,  SFTPInfo] = Field(default="", description="SFTP Information, or an error message")

@router.get("/client", tags=["SFTP Facts"], response_model=List[ClientResponse])
async def client(
    common: LocalModel = Depends(localmodel),
) -> List[ClientResponse]:
    """# Return sftp client information"""
    return await router_handler(LocalModel, Client)(common=common)
