from fastapi import APIRouter

from reemote.core.getfact import router as core_getfact_router

core_router = APIRouter()

core_router.include_router(core_getfact_router, prefix="/reemote/core")

__all__ = ['core_router', 'CORE_TAG']

CORE_TAG = {
    "name": "Core Operations",
    "description": "Get information about the hosts and issue shell commands.",
}