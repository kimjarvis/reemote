"""Test-framework stand-in for a real machine.

``SimulatedHost`` is part of the *test framework*, not of the library, so it
lives here in ``tests/`` rather than in ``src/reemote5/``. Tests and examples
inject it via ``run``'s ``host_factory`` argument (or hand it to
``run_prepare`` directly); the library never imports or constructs one.

The library relies only on the seams this class provides at the points where
a real asyncssh transport is missing:

- ``execute_command(cmd)``: applies the simulated effect of a command
  descriptor to the in-memory state. ``reemote5.drivers.send_command``
  forwards to it when the host provides it, and raises ``NotImplementedError``
  otherwise.
- ``packages`` / ``links`` / ``existing_files``: the state the library's
  facts (``DnfPackages``, ``FileLink``, ``FileExists``) read as a stand-in
  for gathering over SSH.

Holding this state is what makes the "fresh fact cache per lockstep step"
property observable in the tests and the demo: ``send_command`` mutates the
state when an operation is changed, and the next operation's fact reads see
the mutation.
"""

from dataclasses import dataclass, field

from reemote5.commands import Command, StringCommand
from reemote5.hosts import Host
from reemote5.operations.dnf import DnfInstallCommand, DnfRemoveCommand
from reemote5.operations.files import FileCommand, LinkCommand


@dataclass
class SimulatedHost(Host):
    """In-memory stand-in for a real machine.

    Inherits the inventory fields from ``Host`` and adds the state facts
    read and ``execute_command`` mutates. Only ever constructed by
    tests/examples (injected via ``run``'s ``host_factory``), never by the
    library itself.
    """

    packages: set[str] = field(default_factory=set)
    links: dict[str, str] = field(default_factory=dict)  # src -> target
    existing_files: set[str] = field(default_factory=set)
    # Side effects of installing a package: package -> {link src: target}
    # and package -> {file path}. This is what lets the demo reproduce the
    # design doc section 9 trace: installing a package creates the default
    # site's link or ships files (e.g. httpd's welcome.conf), which the
    # *next* operation then sees because the fact cache is recreated per
    # step.
    installs_links: dict[str, dict[str, str]] = field(default_factory=dict)
    installs_files: dict[str, set[str]] = field(default_factory=dict)

    # Command seam: reemote5.drivers.send_command forwards here. The match
    # on the typed descriptors lives in the test framework, not the library.

    def execute_command(self, cmd: Command) -> None:
        """Apply the simulated effect of a command descriptor.

        A raw ``StringCommand`` is accepted but has no simulated effect; an
        unknown descriptor is a hard error so new command types cannot be
        silently ignored.
        """
        match cmd:
            case DnfInstallCommand(packages=packages):
                self.install_packages(packages)
            case DnfRemoveCommand(packages=packages):
                self.remove_packages(packages)
            case LinkCommand(src=src, target=None):
                self.remove_link(src)
            case LinkCommand(src=src, target=target):
                self.make_link(src, target)
            case FileCommand(src=src, present=False):
                self.remove_file(src)
            case FileCommand(src=src):
                self.create_file(src)
            case StringCommand():
                pass  # no simulated effect for raw shell strings
            case _:
                raise TypeError(f"unknown command descriptor: {cmd!r}")

    # State mutations, invoked only via execute_command.

    def install_packages(self, packages: tuple[str, ...]) -> None:
        for package in packages:
            if package in self.packages:
                continue
            self.packages.add(package)
            for src, target in self.installs_links.get(package, {}).items():
                self.links[src] = target
            for path in self.installs_files.get(package, set()):
                self.existing_files.add(path)

    def remove_packages(self, packages: tuple[str, ...]) -> None:
        self.packages.difference_update(packages)

    def make_link(self, src: str, target: str) -> None:
        self.links[src] = target

    def remove_link(self, src: str) -> None:
        self.links.pop(src, None)

    def create_file(self, src: str) -> None:
        self.existing_files.add(src)

    def remove_file(self, src: str) -> None:
        self.existing_files.discard(src)


__all__ = ["SimulatedHost"]
