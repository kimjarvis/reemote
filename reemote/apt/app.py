from fastapi import APIRouter

from reemote.apt.update import router as apt_update_router
from reemote.apt1.get.Packages import router as apt_get_packages_router
from reemote.apt.upgrade import router as apt_upgrade_router
# from reemote.apt.upgradepackages import router as apt_upgradepackages_router
from reemote.apt.install import router as apt_install_router
from reemote.apt.remove import router as apt_remove_router
# from reemote.apt.package import router as apt_package_router

# Create a router for all APT-related endpoints
apt_router = APIRouter()

# Include all APT routers
apt_router.include_router(apt_get_packages_router, prefix="/reemote/apt")
apt_router.include_router(apt_install_router, prefix="/reemote/apt")
# apt_router.include_router(apt_package_router, prefix="/reemote/apt")
apt_router.include_router(apt_remove_router, prefix="/reemote/apt")
apt_router.include_router(apt_update_router, prefix="/reemote/apt")
apt_router.include_router(apt_upgrade_router, prefix="/reemote/apt")
# apt_router.include_router(apt_upgradepackages_router, prefix="/reemote/apt")

# Export the router and the OpenAPI tag definition
__all__ = ["apt_router", "APT_TAG"]

# Define the OpenAPI tag for APT
APT_TAG = {
    "name": "APT Package Manager",
    "description": "Manage software installed on the hosts.",
}
