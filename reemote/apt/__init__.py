from reemote.apt.getpackages import GetPackages
from reemote.apt.update import Update
from reemote.apt.upgrade import Upgrade
from reemote.apt.install import Install
from reemote.apt.remove import Remove
# from reemote.apt.package import Package

__all__ = [
    "Update",
    "GetPackages",
    "Upgrade",
    "Install",
    "Remove",
    # "Package",
]
