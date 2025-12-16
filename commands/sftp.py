import logging
import stat
from pathlib import PurePath
from typing import AsyncGenerator, Callable, Optional, Sequence, Union

import asyncssh
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from command import Command
from common.base_classes import BaseCommand
from common.router_utils import create_router_handler
from local_params import LocalParams, local_params, Local
from construction_tracker import track_construction, track_yields
from response import Response

router = APIRouter()


class BaseSftpModel(BaseModel):
    """Base model for SFTP operations."""
    preserve: bool = False
    recurse: bool = False
    follow_symlinks: bool = False
    sparse: bool = True
    block_size: Optional[int] = -1
    max_requests: Optional[int] = -1
    progress_handler: Optional[Callable] = None
    error_handler: Optional[Callable] = None

    model_config = ConfigDict(extra='forbid')  # Forbid extra fields

class GetModel(BaseSftpModel):
    """Model for SFTP get/retrieve operation."""
    remotepaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]]
    localpath: Optional[Union[PurePath, str, bytes]] = None


class PutModel(BaseSftpModel):
    """Model for SFTP put/upload operation."""
    localpaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]]
    remotepath: Optional[Union[PurePath, str, bytes]] = None


class CopyModel(BaseSftpModel):
    """Model for SFTP copy operation."""
    srcpaths: Union[PurePath, str, bytes, Sequence[Union[PurePath, str, bytes]]]
    dstpath: Optional[Union[PurePath, str, bytes]] = None
    remote_only: bool = False


@track_construction
class Copy(BaseCommand):
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
                    remote_only=caller.remote_only
                )

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)
        self.mark_changed(result)
        return

copy_handler = create_router_handler(CopyModel, Copy)

@router.get("/commands/copy/",
            tags=["SFTP"])
async def copy(
        srcpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
            ...,
            description="The paths of the remote files or directories to copy"
        ),
        dstpath: Optional[Union[PurePath, str, bytes]] = Query(
            None,
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
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    # Copy remote files to a new location

    This method copies one or more files or directories on the
    remote system to a new location. Either a single source path
    or a sequence of source paths to copy can be provided.

    When copying a single file or directory, the destination path
    can be either the full path to copy data into or the path to
    an existing directory where the data should be placed. In the
    latter case, the base file name from the source path will be
    used as the destination name.

    When copying multiple files, the destination path must refer
    to an existing remote directory.

    If no destination path is provided, the file is copied into
    the current remote working directory.

    If preserve is `True`, the access and modification times
    and permissions of the original file are set on the
    copied file.

    If recurse is `True` and the source path points at a
    directory, the entire subtree under that directory is
    copied.

    If follow_symlinks is set to `True`, symbolic links found
    in the source will have the contents of their target copied
    rather than creating a copy of the symbolic link. When
    using this option during a recursive copy, one needs to
    watch out for links that result in loops.

    The block_size argument specifies the size of read and write
    requests issued when copying the files, defaulting to the
    maximum allowed by the server, or 16 KB if the server
    doesn't advertise limits.

    The max_requests argument specifies the maximum number of
    parallel read or write requests issued, defaulting to a
    value between 16 and 128 depending on the selected block
    size to avoid excessive memory usage.

    If progress_handler is specified, it will be called after
    each block of a file is successfully copied. The arguments
    passed to this handler will be the source path, destination
    path, bytes copied so far, and total bytes in the file
    being copied. If multiple source paths are provided or
    recurse is set to `True`, the progress_handler will be
    called consecutively on each file being copied.

    If error_handler is specified and an error occurs during
    the copy, this handler will be called with the exception
    instead of it being raised. This is intended to primarily be
    used when multiple source paths are provided or when recurse
    is set to `True`, to allow error information to be collected
    without aborting the copy of the remaining files. The error
    handler can raise an exception if it wants the copy to
    completely stop. Otherwise, after an error, the copy will
    continue starting with the next file.
    """
    return await copy_handler(
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


@track_construction
class Mcopy(Copy):

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
                    remote_only=caller.remote_only
                )

mcopy_handler = create_router_handler(CopyModel, Mcopy)

@router.get("/commands/mcopy/",
            tags=["SFTP"])
async def mcopy(
        srcpaths: Union[PurePath, str, bytes, list[Union[PurePath, str, bytes]]] = Query(
            ...,
            description="The paths of the remote files or directories to copy"
        ),
        dstpath: Optional[Union[PurePath, str, bytes]] = Query(
            None,
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
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    # Copy remote files with glob pattern match

    This method downloads files and directories from the remote
    system matching one or more glob patterns.

    The arguments to this method are identical to the copy
    method, except that the remote paths specified can contain
    wildcard patterns.
    """
    return await mcopy_handler(
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


@track_construction
class Get(BaseCommand):
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

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)
        self.mark_changed(result)
        return

get_handler = create_router_handler(GetModel, Get)

@router.get("/commands/get/", tags=["SFTP"])
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
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    # Download remote files

    This method downloads one or more files or directories from
    the remote system. Either a single remote path or a sequence
    of remote paths to download can be provided.

    When downloading a single file or directory, the local path can
    be either the full path to download data into or the path to an
    existing directory where the data should be placed. In the
    latter case, the base file name from the remote path will be
    used as the local name.

    When downloading multiple files, the local path must refer to
    an existing directory.

    If no local path is provided, the file is downloaded
    into the current local working directory.

    If preserve is `True`, the access and modification times
    and permissions of the original file are set on the
    downloaded file.

    If recurse is `True` and the remote path points at a
    directory, the entire subtree under that directory is
    downloaded.

    If follow_symlinks is set to `True`, symbolic links found
    on the remote system will have the contents of their target
    downloaded rather than creating a local symbolic link. When
    using this option during a recursive download, one needs to
    watch out for links that result in loops.

    The block_size argument specifies the size of read and write
    requests issued when downloading the files, defaulting to
    the maximum allowed by the server, or 16 KB if the server
    doesn't advertise limits.

    The max_requests argument specifies the maximum number of
    parallel read or write requests issued, defaulting to a
    value between 16 and 128 depending on the selected block
    size to avoid excessive memory usage.

    If progress_handler is specified, it will be called after
    each block of a file is successfully downloaded. The arguments
    passed to this handler will be the source path, destination
    path, bytes downloaded so far, and total bytes in the file
    being downloaded. If multiple source paths are provided or
    recurse is set to `True`, the progress_handler will be
    called consecutively on each file being downloaded.

    If error_handler is specified and an error occurs during
    the download, this handler will be called with the exception
    instead of it being raised. This is intended to primarily be
    used when multiple remote paths are provided or when recurse
    is set to `True`, to allow error information to be collected
    without aborting the download of the remaining files. The
    error handler can raise an exception if it wants the download
    to completely stop. Otherwise, after an error, the download
    will continue starting with the next file.
    """
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


@track_construction
class Mget(Get):

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

mget_handler = create_router_handler(GetModel, Mget)

@router.get("/commands/mget/", tags=["SFTP"])
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
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """# Download remote files with glob pattern match

    This method downloads files and directories from the remote
    system matching one or more glob patterns.

    The arguments to this method are identical to the `get`
    method, except that the remote paths specified can contain
    wildcard patterns.
    """
    return await mget_handler(
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


@track_construction
class Put(BaseCommand):
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

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)
        self.mark_changed(result)
        return

put_handler = create_router_handler(PutModel, Put)

@router.get("/commands/put/", tags=["SFTP"])
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
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    # Upload local files

    This method uploads one or more files or directories to the
    remote system. Either a single local path or a sequence of
    local paths to upload can be provided.

    When uploading a single file or directory, the remote path can
    be either the full path to upload data into or the path to an
    existing directory where the data should be placed. In the
    latter case, the base file name from the local path will be
    used as the remote name.

    When uploading multiple files, the remote path must refer to
    an existing directory.

    If no remote path is provided, the file is uploaded into the
    current remote working directory.

    If preserve is `True`, the access and modification times
    and permissions of the original file are set on the
    uploaded file.

    If recurse is `True` and the local path points at a
    directory, the entire subtree under that directory is
    uploaded.

    If follow_symlinks is set to `True`, symbolic links found
    on the local system will have the contents of their target
    uploaded rather than creating a remote symbolic link. When
    using this option during a recursive upload, one needs to
    watch out for links that result in loops.

    The block_size argument specifies the size of read and write
    requests issued when uploading the files, defaulting to
    the maximum allowed by the server, or 16 KB if the server
    doesn't advertise limits.

    The max_requests argument specifies the maximum number of
    parallel read or write requests issued, defaulting to a
    value between 16 and 128 depending on the selected block
    size to avoid excessive memory usage.

    If progress_handler is specified, it will be called after
    each block of a file is successfully uploaded. The arguments
    passed to this handler will be the source path, destination
    path, bytes uploaded so far, and total bytes in the file
    being uploaded. If multiple source paths are provided or
    recurse is set to `True`, the progress_handler will be
    called consecutively on each file being uploaded.

    If error_handler is specified and an error occurs during
    the upload, this handler will be called with the exception
    instead of it being raised. This is intended to primarily be
    used when multiple local paths are provided or when recurse
    is set to `True`, to allow error information to be collected
    without aborting the upload of the remaining files. The
    error handler can raise an exception if it wants the upload
    to completely stop. Otherwise, after an error, the upload
    will continue starting with the next file.
    """
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


@track_construction
class Mput(Put):

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
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
                    error_handler=caller.error_handler
                )


mput_handler = create_router_handler(PutModel, Mput)

@router.get("/commands/mput/", tags=["SFTP"])
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
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """# Upload local files with glob pattern match

    This method uploads files and directories to the remote
    system matching one or more glob patterns.

    The arguments to this method are identical to the :meth:`put`
    method, except that the local paths specified can contain
    wildcard patterns.
    """
    return await mput_handler(
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

    # model_config = ConfigDict(extra='forbid')  # Forbid extra fields

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
class Mkdir(BaseCommand):
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

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        print(f"debug {self.extra_kwargs}")

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)
        # Directory creation is inherently a changing operation
        self.mark_changed(result)
        return

# Create endpoint handler
mkdir_handler = create_router_handler(MkdirModel, Mkdir)

@router.get("/command/mkdir/", tags=["SFTP"])
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
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    # Create a remote directory with the specified attributes

    This method creates a new remote directory at the
    specified path with the requested attributes.
    """
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

    # model_config = ConfigDict(extra='forbid')  # Forbid extra fields

@track_construction
class Stat(BaseCommand):
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

        yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)


stat_handler = create_router_handler(StatModel, Stat)

@router.get("/facts/stat/", tags=["SFTP"])
async def stat(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        follow_symlinks: bool = Query(True, description="Whether or not to follow symbolic links"),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """# Get attributes of a remote file, directory, or symlink

    This method queries the attributes of a remote file, directory,
    or symlink. If the path provided is a symlink and follow_symlinks
    is `True`, the returned attributes will correspond to the target
    of the link.
    """
    return await stat_handler(path=path, follow_symlinks=follow_symlinks, common=common)




class RmdirModel(BaseModel):
    path: Union[PurePath, str, bytes] = None

    # model_config = ConfigDict(extra='forbid')  # Forbid extra fields

@track_construction
class Rmdir(BaseCommand):
    Model = RmdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.rmdir(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)
        # Directory deletion is inherently a changing operation
        self.mark_changed(result)
        return

rmdir_handler = create_router_handler(RmdirModel, Rmdir)

@router.get("/commands/rmdir/", tags=["SFTP"])
async def rmdir(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """
    # Remove a remote directory

    This method removes a remote directory. The directory
    must be empty for the removal to succeed.
    """
    return await rmdir_handler(path=path, common=common)




class IsfileModel(BaseModel):
    path: Union[PurePath, str, bytes] = None

    # model_config = ConfigDict(extra='forbid')  # Forbid extra fields

class Isfile(BaseCommand):
    Model = IsfileModel

    # Define the SFTP method name as a class attribute
    sftp_method_name = None

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isfile(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)

isfile_handler = create_router_handler(IsfileModel, Isfile)

@router.get("/fact/isfile/", tags=["SFTP"])
async def isfile(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """# Return if the remote path refers to a file"""
    return await isfile_handler(path=path, common=common)


class IslinkModel(BaseModel):
    path: Union[PurePath, str, bytes] = None

    # model_config = ConfigDict(extra='forbid')  # Forbid extra fields

class Islink(BaseCommand):
    Model = IslinkModel

    # Define the SFTP method name as a class attribute
    sftp_method_name = None

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.islink(caller.path)

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        yield Command(local=True,
                               callback=self._callback,
                               caller=model_instance,
                               **self.extra_kwargs)

islink_handler = create_router_handler(IslinkModel, Islink)

@router.get("/fact/islink/", tags=["SFTP"])
async def islink(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """# Return if the remote path refers to a link"""
    return await islink_handler(path=path, common=common)


class IsdirModel(LocalParams):
    """Model for Isdir command, combining LocalParams with path"""
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
        description="Path to check if it's a directory"
    )

    def __init__(self, **data):
        # Ensure path is converted to PurePath if it's a string/bytes
        if 'path' in data and isinstance(data['path'], (str, bytes)):
            data['path'] = PurePath(data['path'])
        super().__init__(**data)

class Isdir(Local):
    Model = IsdirModel

    @staticmethod
    async def _callback(host_info, global_info, command, cp, caller):
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                return await sftp.isdir(str(caller.path))

isdir_handler = create_router_handler(IsdirModel, Isdir)

@router.get("/fact/isdir/", tags=["SFTP"])
async def isdir(
        path: Union[PurePath, str, bytes] = Query(..., description="Directory path"),
        common: LocalParams = Depends(local_params)
) -> list[dict]:
    """# Return if the remote path refers to a directory"""
    return await isdir_handler(path=path, common=common)
