from fastapi import FastAPI

from reemote.apt import router as apt_router
from reemote.scp import router as scp_router
from reemote.host import router as server_router
from reemote.sftp import router as sftp_router
from reemote.inventory import router as inventory_router

app = FastAPI(
    title="Reemote",
    summary="An API for controlling remote systems.",
    version="0.1.2",
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

app.include_router(apt_router, prefix="/reemote/apt")

app.include_router(sftp_router, prefix="/reemote/sftp")
app.include_router(scp_router, prefix="/reemote/scp")

