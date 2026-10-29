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
- ``packages`` / ``links`` / ``existing_files`` / ``remote_files`` /
  ``repositories`` / ``enabled_modules`` / ``disabled_modules`` /
  ``services`` / ``enabled_services``: the state the
  library's facts (``DnfPackages``, ``FileLink``, ``FileExists``,
  ``RemoteFileSha1``, ``DnfRepositories``, ``DnfEnabledModules``,
  ``DnfDisabledModules``, ``SystemdServiceState``, ``SystemdServiceEnabled``)
  read as a stand-in for gathering over SSH.

Holding this state is what makes the "fresh fact cache per lockstep step"
property observable in the tests and the demo: ``send_command`` mutates the
state when an operation is changed, and the next operation's fact reads see
the mutation.
"""

from dataclasses import dataclass, field

from reemote5.commands import Command, StringCommand, WaitTimeoutError
from reemote5.hosts import Host
from reemote5.modules.dnf.dnf import (
    DnfCleanCommand,
    DnfInstallCommand,
    DnfRemoveCommand,
    DnfUpdateCommand,
)
from reemote5.modules.files.files import (
    FileCommand,
    GetCommand,
    LinkCommand,
    PutCommand,
    read_source_bytes,
    write_dest_bytes,
)
from reemote5.modules.server.server import (
    SystemctlCommand,
    SystemdServiceState,
    WaitForFactCommand,
)


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
    # Remote path -> the content bytes the simulated remote filesystem holds
    # there. This single store backs both transfer directions: ``RemoteFileSha1``
    # derives its digest from it, a simulated ``files.put`` writes the source
    # stream's bytes into it, and a simulated ``files.get`` reads them back out
    # into the destination stream. Storing content (not just a hash) is what
    # lets a put then a get of the same path round-trip, and what makes a repeat
    # transfer a no-op: execute_command mutates it, and the next step's fresh
    # fact cache sees the mutation.
    remote_files: dict[str, bytes] = field(default_factory=dict)
    repositories: list[dict[str, str]] = field(default_factory=list)
    enabled_modules: dict[str, str] = field(default_factory=dict)  # name -> stream
    disabled_modules: list[str] = field(default_factory=list)  # disabled module names
    # Simulated systemd: full unit name (``httpd.service``) -> its ActiveState
    # string (``active``/``inactive``/...), backing ``SystemdServiceState``;
    # and the set of unit names enabled on boot, backing
    # ``SystemdServiceEnabled``. A unit absent from ``services`` reads as
    # None (as the fact reports when systemd answers nothing).
    services: dict[str, str] = field(default_factory=dict)
    enabled_services: set[str] = field(default_factory=set)
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
            case DnfUpdateCommand() | DnfCleanCommand():
                pass  # the simulation tracks no versions/metadata to update
            case LinkCommand(src=src, target=None):
                self.remove_link(src)
            case LinkCommand(src=src, target=target):
                self.make_link(src, target)
            case FileCommand(src=src, present=False):
                self.remove_file(src)
            case FileCommand(src=src):
                self.create_file(src)
            case PutCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case GetCommand(src=src, dest=dest):
                self.get_file(src, dest)
            case StringCommand():
                pass  # no simulated effect for raw shell strings
            case SystemctlCommand(action="daemon-reload"):
                pass  # the simulation tracks no unit files to reload
            case SystemctlCommand(action="start" | "restart", unit=unit):
                self.services[unit] = "active"
            case SystemctlCommand(action="stop", unit=unit):
                self.services[unit] = "inactive"
            case SystemctlCommand(action="enable", unit=unit):
                self.enabled_services.add(unit)
            case SystemctlCommand(action="disable", unit=unit):
                self.enabled_services.discard(unit)
            case WaitForFactCommand():
                self.check_wait(cmd)
            case _:
                raise TypeError(f"unknown command descriptor: {cmd!r}")

    # State mutations, invoked only via execute_command.

    def install_packages(self, packages: tuple[str, ...]) -> None:
        for package in packages:
            # Version pins (``pkg=version``) carry no simulated state: only
            # the name is tracked, matching the name-only DnfPackages fact.
            package = package.split("=", 1)[0]
            if package in self.packages:
                continue
            self.packages.add(package)
            for src, target in self.installs_links.get(package, {}).items():
                self.links[src] = target
            for path in self.installs_files.get(package, set()):
                self.existing_files.add(path)

    def remove_packages(self, packages: tuple[str, ...]) -> None:
        self.packages.difference_update(pkg.split("=", 1)[0] for pkg in packages)

    def make_link(self, src: str, target: str) -> None:
        self.links[src] = target

    def remove_link(self, src: str) -> None:
        self.links.pop(src, None)

    def create_file(self, src: str) -> None:
        self.existing_files.add(src)

    def remove_file(self, src: str) -> None:
        self.existing_files.discard(src)

    def put_file(self, src, dest: str) -> None:
        """Record a simulated scp upload: store the IO-like source's content.

        The source is read through the library's own ``read_source_bytes``
        (rewind + UTF-8 normalization), so the bytes stored are exactly what
        ``LocalSha1`` hashes and what the real transport would copy. Storing
        the content -- rather than only its hash -- lets ``RemoteFileSha1``
        derive the digest and lets a later ``files.get`` read the same bytes
        back out.
        """
        self.remote_files[dest] = read_source_bytes(src)

    def get_file(self, src: str, dest) -> None:
        """Record a simulated scp download: write the remote content into dest.

        The mirror of ``put_file``: the bytes stored at the remote path are
        written into the IO-like destination through the library's own
        ``write_dest_bytes`` (rewind + truncate + UTF-8 for a text stream), so
        the destination ends up holding exactly the content ``RemoteFileSha1``
        describes and a repeat get is a no-op. An absent remote path has no
        content to copy, so the destination is left untouched.
        """
        data = self.remote_files.get(src)
        if data is not None:
            write_dest_bytes(dest, data)

    def check_wait(self, cmd: WaitForFactCommand) -> None:
        """Evaluate a simulated wait once, without polling or sleeping.

        The in-memory state never changes on its own, so there is nothing to
        wait *for*: the condition either already holds -- the wait succeeds
        -- or it never will, and the wait fails at once with the same
        ``WaitTimeoutError`` the real poll loop raises at its deadline. The
        fact's value is read straight from the seam the fact itself would
        read (``execute_command`` is synchronous, so the async ``process``
        cannot be awaited here); a wait on a fact this simulation does not
        model is a hard error, like any unknown descriptor.
        """
        if cmd.fact_cls is SystemdServiceState:
            (unit,) = cmd.args
            value = self.services.get(unit)
        else:
            raise TypeError(f"unsupported simulated wait fact: {cmd.fact_cls!r}")
        if not cmd.predicate(value):
            raise WaitTimeoutError(
                f"host {self.name}: {cmd} -- condition not met within "
                f"{cmd.timeout}s (last value: {value!r})"
            )


__all__ = ["SimulatedHost"]
