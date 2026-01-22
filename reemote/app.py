from fastapi import FastAPI

from reemote.scp import router as scp_router
from reemote.inventory_api import router as inventory_router
from reemote.sftp1.get.IsDir import router as sftp_isdir_router


from reemote.apt.app import apt_router, APT_TAG
from reemote.core.app import core_router, CORE_TAG
from reemote.sftp1.app import sftp_router, SFTP_TAG

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
        SFTP_TAG,
    ],
)

# Include routers
app.include_router(inventory_router, prefix="/reemote/inventory")
app.include_router(apt_router)
app.include_router(core_router)
app.include_router(sftp_router)
app.include_router(sftp_isdir_router, prefix="/reemote/sftp")
app.include_router(scp_router, prefix="/reemote/scp")
