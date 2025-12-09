from fastapi import FastAPI
from commands.server import router as server_router
from inventory import router as inventory_router  # Import the inventory router
from commands.apt import router as apt_router
from commands.sftp import router as sftp_router


# Initialize FastAPI app
app = FastAPI(
    title="Reemote",
    summary="An API for server management",
    # description="An API for server management",
    version="0.1.0",
    swagger_ui_parameters={"docExpansion": "none", "title": "Reemote - Swagger UI"},
    openapi_tags = [
        {
            "name": "Inventory",
            "description": """
The inventory specifies the servers on which Reemote operations act.  These CRUD operations facilitate inventory management.
            """
        },
        {
            "name": "APT Package Manager",
            "description": """
Manage software installed on the servers.
            """
        },
        {
            "name": "Server",
            "description": """
Get information about the servers and issue shell commands. 
            """
        },
        {
            "name": "SFTP",
            "description": """
Create files and directories on remote servers and transfer files to from servers.
            """
        }
    ]
)


# Include the inventory router under a specific prefix (optional)
app.include_router(inventory_router, prefix="/inventory")
app.include_router(apt_router, prefix="/apt")
app.include_router(server_router, prefix="/server")

app.include_router(sftp_router, prefix="/sftp")