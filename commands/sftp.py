import asyncssh
from typing import Any, AsyncGenerator
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from common.base_classes import ShellBasedCommand
from command import Command
from response import Response
from common.router_utils import create_router_handler
from common_params import CommonParams, common_params
from construction_tracker import ConstructionTracker,track_construction, track_yields
import logging
router = APIRouter()


class MkdirModel(BaseModel):
    path: str

@track_construction
class Mkdir(ShellBasedCommand):
    Model = MkdirModel

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation"""
        # Validate host_info with proper error messages
        required_keys = ['host', 'username', 'password']
        missing_keys = []
        invalid_keys = []

        for key in required_keys:
            if key not in host_info:
                missing_keys.append(key)
            elif host_info[key] is None:
                invalid_keys.append(key)

        if missing_keys:
            raise ValueError(f"Missing required keys in host_info: {missing_keys}")
        if invalid_keys:
            raise ValueError(f"None values for keys in host_info: {invalid_keys}")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        # Clean host_info by removing None values and keys that asyncssh doesn't expect
        clean_host_info = {
            'host': host_info['host'],
            'username': host_info['username'],
            'password': host_info['password']
        }

        # Add optional parameters only if they exist and are not None
        optional_keys = ['port', 'known_hosts', 'client_keys', 'passphrase']
        for key in optional_keys:
            if key in host_info and host_info[key] is not None:
                clean_host_info[key] = host_info[key]

        # Validate caller - now it's MkdirModel instance
        if not hasattr(caller, 'path') or caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        try:
            async with asyncssh.connect(**clean_host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Create the remote directory
                    await sftp.mkdir(caller.path)
                    return f"Successfully created directory {caller.path} on {host_info['host']}"

        except (OSError, asyncssh.Error) as exc:
            # Provide more detailed error information
            raise Exception(f"Failed to create directory {caller.path} on {host_info['host']}: {str(exc)}")

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._mkdir_callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        # Directory creation is inherently a changing operation
        self.mark_changed(result)
        return

class IsdirModel(BaseModel):
    path: str

@track_construction
class Isdir(ShellBasedCommand):
    Model = IsdirModel

    @staticmethod
    async def _isdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for checking if a path is a directory"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Check if the path refers to a directory
                if caller.path:
                    is_dir = await sftp.isdir(caller.path)
                    return is_dir
                else:
                    raise ValueError("Path must be provided for isdir operation")

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # Convert dictionary to model instance
        model_instance = self.Model(**self._data)

        result = yield Command(local=True,
                               callback=self._isdir_callback,
                               caller=model_instance,
                               id=ConstructionTracker.get_current_id(),
                               parents=ConstructionTracker.get_parents(),
                               **self.extra_kwargs)
        result.output = result.cp.stdout
        return


# Create endpoint handler
mkdir_handler = create_router_handler(MkdirModel, Mkdir)
isdir_handler = create_router_handler(IsdirModel, Isdir)


@router.get("/command/mkdir/", tags=["SFTP"])
async def shell_command(
        path: str = Query(..., description="Directory path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await mkdir_handler(path=path, common=common)


@router.get("/fact/isdir/", tags=["SFTP"])
async def shell_command(
        path: str = Query(..., description="Directory path"),
        common: CommonParams = Depends(common_params)
) -> list[dict]:
    return await isdir_handler(path=path, common=common)