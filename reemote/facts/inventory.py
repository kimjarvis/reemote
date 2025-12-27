from fastapi import Query
import logging
from fastapi import HTTPException
from reemote.config import Config
from typing import List, Dict, Any
from pydantic import BaseModel
from reemote.commands.inventory import ErrorResponse
from fastapi import FastAPI, Query, Response
from fastapi.routing import APIRouter


router = APIRouter()

class IsEntryResponse(BaseModel):
    value: Any

def isentry(host: str) -> bool:
    config = Config()
    inventory = config.get_inventory()

    for entry in inventory:
        if entry[0].get("host") == host:
            return True
    return False


@router.get(
    "/isentry",
    tags=["Inventory Facts"],
    response_model=None,  # No response model since we're returning raw values
)
async def isentry_route(
    host: str = Query(..., description="Host name"),
):
    exists = isentry(host)
    return Response(content=str(exists), media_type="text/plain")






class GetEntryResponse(BaseModel):
    """Response model for inventory creation endpoint"""

    status: str
    message: str
    data: List[Dict[str, Any]]  # Generic description without details

def get_entry(host: str) -> Dict:
    config = Config()
    inventory = config.get_inventory()

    for entry in inventory:
        if entry[0].get("host") == host:
            return {
                "status": "success",
                "message": "Inventory entry found",
                "data": entry,
            }
    raise ValueError(f"Entry for host not found: {host}")


@router.get(
    "/get/{host}",
    tags=["Inventory Facts"],
    response_model=GetEntryResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def get_inventory_entry(host: str):
    try:
        result = get_entry(host)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error getting inventory entry: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")



def read_inventory() -> List[List[Dict]]:
    config = Config()
    return config.get_inventory()


@router.get(
    "/read",
    tags=["Inventory Facts"],
    response_model=List[List[Dict]],
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Validation error - Invalid inventory structure",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def read_the_inventory():
    try:
        return read_inventory()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error getting inventory: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
