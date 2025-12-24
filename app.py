import sys
from fastapi import FastAPI

from reemote.commands.apt import router as commands_apt_router
from reemote.facts.apt import router as facts_apt_router
from reemote.operations.apt import router as operations_apt_router
from reemote.commands.scp import router as commands_scp_router
from reemote.commands.server import router as commands_server_router
from reemote.commands.sftp import router as commands_sftp_router
from reemote.facts.sftp import router as facts_sftp_router
from reemote.commands.inventory import router as inventory_router
from reemote.facts.inventory import router as facts_inventory_router

import argparse
from reemote.config import Config

# Filter out FastAPI/Starlette server arguments
app_args = [
    arg
    for arg in sys.argv[1:]
    if not arg.startswith("--reload") and not arg.startswith("--port")
]

parser = argparse.ArgumentParser(description="Server configuration")

# Add arguments
parser.add_argument("--logging", "-l", type=str, help="Set the logging file path")

parser.add_argument("--inventory", "-i", type=str, help="Set the inventory file path")

# Parse only the filtered arguments
args, _ = parser.parse_known_args(app_args)

# Initialize FastAPI app
app = FastAPI(
    title="Reemote",
    summary="An API for server management",
    # description="An API for server management",
    version="0.1.0",
    swagger_ui_parameters={"docExpansion": "none", "title": "Reemote - Swagger UI"},
    openapi_tags=[
        {
            "name": "Inventory Commands",
            "description": """
The inventory specifies the servers on which operations act.  These CRUD operations facilitate inventory management.
            """,
        },
        {
            "name": "Inventory Facts",
            "description": """
Get information about the Inventory.
                    """,
        },
        {
            "name": "APT Package Manager Commands",
            "description": """
Manage software installed on the servers.
            """,
        },
        {
            "name": "APT Package Manager Facts",
            "description": """
Manage software installed on the servers.
                    """,
        },
        {
            "name": "APT Package Manager Operations",
            "description": """Manage software installed on the servers.""",
        },
        {
            "name": "Server Commands",
            "description": """
Get information about the servers and issue shell commands. 
            """,
        },
        {
            "name": "SCP Commands",
            "description": """
Copy files and directories to and from remote servers.
            """,
        },
        {
            "name": "SFTP Commands",
            "description": """
Create files and directories on remote servers and transfer files to from servers.
            """,
        },
        {
            "name": "SFTP Facts",
            "description": """
Get information about files and directories on servers.
                """,
        },
    ],
)


# Include the inventory router under a specific prefix (optional)
app.include_router(inventory_router, prefix="/inventory/commands")
app.include_router(facts_inventory_router, prefix="/inventory/facts")
app.include_router(commands_apt_router, prefix="/commands/apt")
app.include_router(facts_apt_router, prefix="/facts/apt")
app.include_router(operations_apt_router, prefix="/operations/apt")
app.include_router(commands_server_router, prefix="/commands/server")
app.include_router(commands_sftp_router, prefix="/commands/sftp")
app.include_router(facts_sftp_router, prefix="/facts/sftp")
app.include_router(commands_scp_router, prefix="/commands/scp")


@app.on_event("startup")
def initialize():
    config = Config()

    # Apply configurations if arguments were provided
    if args.logging:
        config.set_logging(args.logging)

    if args.inventory:
        config.set_inventory_path(args.inventory)
