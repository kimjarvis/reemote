from fastapi import FastAPI, Query, Depends
from pydantic import BaseModel, ValidationError
from common import CommonParams, common_params
from inventory import router as inventory_router  # Import the inventory router
from facts.apt import router as facts_apt_router
from commands.apt import router as commands_apt_router
from operations.apt import router as operations_apt_router
# Initialize FastAPI app
app = FastAPI(
    title="Reemote",
    description="API for server management",
    version="0.0.18",
    swagger_ui_parameters={"docExpansion": "none", "title": "Reemote - Swagger UI"},
    openapi_tags = [
        {
            "name": "Inventory",  # Rename "default" to "RestAPI"
            "description": "Inventory CRUD operations"
        },
        {
            "name": "APT Commands",  # Rename "default" to "RestAPI"
            "description": "APT Package Manager commands"
        },
        {
            "name": "APT Operations",  # Rename "default" to "RestAPI"
            "description": "APT Package Manager operations"
        },
        {
            "name": "APT Facts",  # Rename "default" to "RestAPI"
            "description": "APT Package Manager facts"
        }
    ]
)


# Include the inventory router under a specific prefix (optional)
app.include_router(inventory_router, prefix="/inventory")
app.include_router(facts_apt_router, prefix="/facts/apt")
app.include_router(commands_apt_router, prefix="/commands/apt")

app.include_router(operations_apt_router, prefix="/operations/apt")

