from fastapi import APIRouter

from reemote.core.getfact import router as core_getfact_router
from reemote.core.getcontext import router as core_getcontext_router
from reemote.core.putreturn import router as core_return_router
from reemote.core.getcall import router as core_call_router
from reemote.core.command import router as core_command_router
core_router = APIRouter()

core_router.include_router(core_getfact_router, prefix="/reemote/core")
core_router.include_router(core_getcontext_router, prefix="/reemote/core")
core_router.include_router(core_return_router, prefix="/reemote/core")
core_router.include_router(core_call_router, prefix="/reemote/core")
core_router.include_router(core_command_router, prefix="/reemote/core")

__all__ = ['core_router', 'CORE_TAG']

CORE_TAG = {
    "name": "Core Operations",
    "description": "Get information about the hosts and issue shell commands.",
}