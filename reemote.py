from fastapi import FastAPI
from commands.server import router as server_router
from inventory import router as inventory_router  # Import the inventory router
from commands.apt import router as apt_router
from commands.sftp import router as sftp_router


# Initialize FastAPI app
app = FastAPI(
    title="Reemote",
    description="API for server management",
    version="0.0.18",
    swagger_ui_parameters={"docExpansion": "none", "title": "Reemote - Swagger UI"},
    openapi_tags = [
        {
            "name": "Inventory",
            "description": "Inventory CRUD operations"
        },
        {
            "name": "APT Package Manager",
            "description": "APT Package Manager facts, commands and operations"
        },
        {
            "name": "Server",
            "description": "Server facts, commands and operations"
        },
        {
            "name": "SFTP",
            "description": "SFTP commands"
        }
    ]
)


# Include the inventory router under a specific prefix (optional)
app.include_router(inventory_router, prefix="/inventory")
app.include_router(apt_router, prefix="/apt")
app.include_router(server_router, prefix="/server")

app.include_router(sftp_router, prefix="/sftp")