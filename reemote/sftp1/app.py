from fastapi import APIRouter

from reemote.sftp1.get.IsDir import router as sftp_isdir_router

sftp_router = APIRouter()

sftp_router.include_router(sftp_isdir_router, prefix="/reemote/sftp")

__all__ = ["sftp_router", "SFTP_TAG"]

SFTP_TAG = {
    "name": "SFTP Operations",
    "description": "Create files and directories on remote hosts and transfer files to from hosts.",
}
