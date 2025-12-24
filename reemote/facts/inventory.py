from typing import List, Dict, Any
from fastapi import APIRouter, Query
from pydantic import BaseModel
from reemote.config import Config

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
    "/facts/isentry",
    tags=["Inventory Facts"],
    response_model=IsEntryResponse,
)
async def isentry_route(
    host: str = Query(..., description="Host name"),
) -> IsEntryResponse:
    exists = isentry(host)
    if exists:
        return IsEntryResponse(value=True)
    else:
        return IsEntryResponse(value=False)
