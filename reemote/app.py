from fastapi import FastAPI

from reemote.scp import router as scp_router
from reemote.host import router as server_router
from reemote.sftp import router as sftp_router
from reemote.inventory import router as inventory_router

from reemote.apt.update import router as apt_update_router
from reemote.apt.getpackages import router as apt_getpackages_router
from reemote.apt.upgrade import router as apt_upgrade_router
from reemote.apt.upgradepackages import router as apt_upgradepackages_router
from reemote.apt.install import router as apt_install_router
from reemote.apt.remove import router as apt_remove_router
from reemote.apt.package import router as apt_package_router

app = FastAPI(
    title="Reemote",
    summary="An API for controlling remote systems.",
    version="0.1.3",
    swagger_ui_parameters={"docExpansion": "none", "title": "Reemote - Swagger UI"},
    openapi_tags=[
        {
            "name": "Inventory Management",
            "description": """
Inventory management.
            """,
        },
        {
            "name": "Host Operations",
            "description": """
Get information about the hosts and issue shell commands.
                    """,
        },
        {
            "name": "APT Package Manager",
            "description": """
Manage software installed on the hosts.
            """,
        },
        {
            "name": "SCP Operations",
            "description": """
Copy files and directories to and from remote hosts.
            """,
        },
        {
            "name": "SFTP Operations",
            "description": """
Create files and directories on remote hosts and transfer files to from hosts.
                    """,
        },
    ],
)


app.include_router(inventory_router, prefix="/reemote/inventory")
app.include_router(server_router, prefix="/reemote/host")

app.include_router(apt_update_router, prefix="/reemote/apt")
app.include_router(apt_upgrade_router, prefix="/reemote/apt")
app.include_router(apt_upgradepackages_router, prefix="/reemote/apt")
app.include_router(apt_getpackages_router, prefix="/reemote/apt")
app.include_router(apt_install_router, prefix="/reemote/apt")
app.include_router(apt_remove_router, prefix="/reemote/apt")

app.include_router(apt_package_router, prefix="/reemote/apt")
app.include_router(sftp_router, prefix="/reemote/sftp")
app.include_router(scp_router, prefix="/reemote/scp")
