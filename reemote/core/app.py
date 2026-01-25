from fastapi import APIRouter

from reemote.core1.get.Fact import router as core_get_fact_router
from reemote.core1.get.Context import router as core_get_context_router
from reemote.core1.get.Return import router as core_get_return_router
from reemote.core1.post.Return import router as core_post_return_router
from reemote.core1.put.Return import router as core_put_return_router
from reemote.core1.get.Call import router as core_get_call_router
from reemote.core1.put.Call import router as core_put_call_router
from reemote.core1.post.Call import router as core_post_call_router
from reemote.core1.post.Command import router as core_post_command_router

core_router = APIRouter()

core_router.include_router(core_get_fact_router, prefix="/reemote/core")
core_router.include_router(core_get_context_router, prefix="/reemote/core")
core_router.include_router(core_put_return_router, prefix="/reemote/core")
core_router.include_router(core_post_return_router, prefix="/reemote/core")
core_router.include_router(core_get_return_router, prefix="/reemote/core")
core_router.include_router(core_get_call_router, prefix="/reemote/core")
core_router.include_router(core_put_call_router, prefix="/reemote/core")
core_router.include_router(core_post_call_router, prefix="/reemote/core")
core_router.include_router(core_post_command_router, prefix="/reemote/core")

__all__ = ["core_router", "CORE_TAG"]

CORE_TAG = {
    "name": "Core Operations",
    "description": "Get information about the hosts and issue shell commands.",
}
