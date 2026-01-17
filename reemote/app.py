from fastapi import FastAPI

from reemote.scp import router as scp_router
from reemote.host import router as server_router
from reemote.sftp import router as sftp_router
from reemote.inventory_api import router as inventory_router
from reemote.sftp1.isdir import router as sftp_isdir_router


from reemote.apt.app import apt_router, APT_TAG
from reemote.core.app import core_router, CORE_TAG

app = FastAPI(
    title="Reemote",
    summary="An API for controlling remote systems.",
    version="0.1.3",
    swagger_ui_parameters={"docExpansion": "none", "title": "Reemote - Swagger UI"},
    openapi_tags=[
        {
            "name": "Inventory Management",
            "description": "Inventory management.",
        },
        CORE_TAG,
        APT_TAG,
        {
            "name": "SCP Operations",
            "description": "Copy files and directories to and from remote hosts.",
        },
        {
            "name": "SFTP Operations",
            "description": "Create files and directories on remote hosts and transfer files to from hosts.",
        },
    ],
)

# Include routers
app.include_router(inventory_router, prefix="/reemote/inventory")
app.include_router(server_router, prefix="/reemote/host")
app.include_router(apt_router)
app.include_router(core_router)


app.include_router(sftp_router, prefix="/reemote/sftp")
app.include_router(sftp_isdir_router, prefix="/reemote/sftp")
app.include_router(scp_router, prefix="/reemote/scp")