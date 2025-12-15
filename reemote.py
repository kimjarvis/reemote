import sys
from fastapi import FastAPI

from commands.apt import router as apt_router
from commands.scp import router as scp_router
from commands.server import router as server_router
from commands.sftp import router as sftp_router
from inventory import router as inventory_router  # Import the inventory router

import argparse
from config import Config

# Filter out FastAPI/Starlette server arguments
app_args = [arg for arg in sys.argv[1:] if not arg.startswith("--reload") and not arg.startswith("--port")]

parser = argparse.ArgumentParser(description='Server configuration')

# Add arguments
parser.add_argument(
    '--logging', '-l',
    type=str,
    help='Set the logging file path'
)

parser.add_argument(
    '--inventory', '-i',
    type=str,
    help='Set the inventory file path'
)

# Parse only the filtered arguments
args, _ = parser.parse_known_args(app_args)

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
            "name": "SCP",
            "description": """
Copy files and directories to and from remote servers.
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
app.include_router(scp_router, prefix="/scp")

@app.on_event("startup")
def initialize():
    config = Config()

    # Apply configurations if arguments were provided
    if args.logging:
        config.set_logging(args.logging)

    if args.inventory:
        config.set_inventory_path(args.inventory)

