from fastapi import FastAPI

from reemote.api.apt import router as apt_router
from reemote.api.scp import router as scp_router
from reemote.api.server import router as server_router
from reemote.api.sftp import router as sftp_router
from reemote.api.inventory import router as inventory_router

app = FastAPI(
    title="Reemote",
    summary="An API for controlling remote systems.",
    version="0.1.0",
    swagger_ui_parameters={"docExpansion": "none", "title": "Reemote - Swagger UI"},
    openapi_tags=[
        {
            "name": "Inventory Management",
            "description": """
Inventory management.
            """,
        },
        {
            "name": "Server Operations",
            "description": """
Get information about the servers and issue shell commands. 
                    """,
        },
        {
            "name": "APT Package Manager",
            "description": """
Manage software installed on the servers.
            """,
        },
        {
            "name": "SCP Operations",
            "description": """
Copy files and directories to and from remote servers.
            """,
        },
        {
            "name": "SFTP Operations",
            "description": """
Create files and directories on remote servers and transfer files to from servers.
                    """,
        },
    ],
)


app.include_router(inventory_router, prefix="/inventory")
app.include_router(server_router, prefix="/server")

app.include_router(apt_router, prefix="/apt")

app.include_router(sftp_router, prefix="/sftp")
app.include_router(scp_router, prefix="/scp")

