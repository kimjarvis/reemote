from fastapi import APIRouter

from reemote.core.getfact import router as core_getfact_router
from reemote.core.getcontext import router as core_getcontext_router
from reemote.core.return_put import router as core_return_put_router
from reemote.core.return_get import router as core_return_get_router
from reemote.core.return_post import router as core_return_post_router
from reemote.core1.get.Call import router as core_get_call_router
from reemote.core1.put.Call import router as core_put_call_router
from reemote.core1.post.Call import router as core_post_call_router
from reemote.core.command import router as core_command_router

core_router = APIRouter()

core_router.include_router(core_getfact_router, prefix="/reemote/core")
core_router.include_router(core_getcontext_router, prefix="/reemote/core")
core_router.include_router(core_return_put_router, prefix="/reemote/core")
core_router.include_router(core_return_post_router, prefix="/reemote/core")
core_router.include_router(core_return_get_router, prefix="/reemote/core")
core_router.include_router(core_get_call_router, prefix="/reemote/core")
core_router.include_router(core_put_call_router, prefix="/reemote/core")
core_router.include_router(core_post_call_router, prefix="/reemote/core")
core_router.include_router(core_command_router, prefix="/reemote/core")

__all__ = ["core_router", "CORE_TAG"]

CORE_TAG = {
    "name": "Core Operations",
    "description": "Get information about the hosts and issue shell commands.",
}
