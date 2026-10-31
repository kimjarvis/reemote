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
  ``repositories`` / ``repo_list`` / ``enabled_modules`` / ``disabled_modules``
  / ``installed_modules`` / ``installed_groups`` / ``available_groups``
  / ``rpm_files`` / ``rpm_packages`` / ``services`` / ``enabled_services``
  / ``arch`` / ``authorized_keys`` / ``block_devices`` / ``cpu_info`` / ``cpus``
  / ``date`` / ``disk_stats`` / ``dmi_info`` / ``os_groups`` / ``has_gui``
  / ``home_directories`` / ``os_hostname`` / ``ipv4_addrs`` / ``ipv6_addrs``
  / ``os_kernel``
  / ``kernel_modules`` / ``os_kernel_version`` / ``login_records``
  / ``linux_distribution``
  / ``linux_gui`` / ``locales`` / ``lsb_release`` / ``lsblk_devices`` / ``mounts``
  / ``network_devices`` / ``os_path``
  / ``processes`` / ``smbios_info`` / ``os_timezone`` / ``os_user`` / ``os_users``
  / ``uptime_seconds`` / ``command_paths``:
  the state the library's facts (``DnfPackages``, ``FileLink``, ``FileExists``,
  ``RemoteFileSha1``, ``FileContents``, ``DnfRepoFile``, ``DnfRepositories``,
  ``DnfRepoList``,
  ``DnfEnabledModules``, ``DnfDisabledModules``, ``DnfInstalledModules``,
  ``DnfGroups``, ``DnfAvailableGroups``, ``RpmPackage``,
  ``SystemdServiceState``, ``SystemdServiceEnabled``, ``Arch``,
  ``AuthorizedKeys``, ``BlockDevices``, ``CpuInfo``, ``Cpus``, ``Date``,
  ``DiskStats``, ``DmiInfo``, ``Groups``,
  ``HasGui``, ``Home``, ``Hostname``, ``Ipv4Addrs``, ``Ipv6Addrs``, ``Kernel``,
  ``KernelModules``, ``KernelVersion``, ``Last``,
  ``LinuxDistribution``, ``LinuxGui``, ``LinuxName``, ``Locales``,
  ``LsbRelease``, ``LsBlk``, ``Mounts``, ``NetworkDevices``, ``Path``, ``Processes``,
  ``SmbiosInfo``, ``Timezone``,
  ``Uptime``, ``User``, ``Users``, ``Which``) read as a
  stand-in for gathering over SSH.

Holding this state is what makes the "fresh fact cache per lockstep step"
property observable in the tests and the demo: ``send_command`` mutates the
state when an operation is changed, and the next operation's fact reads see
the mutation.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from reemote5.commands import Command, StringCommand, WaitTimeoutError
from reemote5.hosts import Host
from reemote5.modules.dnf.dnf import (
    DnfCleanCommand,
    DnfInstallCommand,
    DnfKeyImportCommand,
    DnfKeyUploadCommand,
    DnfModuleDisableCommand,
    DnfModuleEnableCommand,
    DnfRemoveCommand,
    DnfRepoCommand,
    DnfUpdateCommand,
    RpmInstallCommand,
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
    # derives its digest from it, ``FileContents`` decodes its text back out,
    # a simulated ``files.put`` writes the source
    # stream's bytes into it, and a simulated ``files.get`` reads them back out
    # into the destination stream. Storing content (not just a hash) is what
    # lets a put then a get of the same path round-trip, and what makes a repeat
    # transfer a no-op: execute_command mutates it, and the next step's fresh
    # fact cache sees the mutation.
    remote_files: dict[str, bytes] = field(default_factory=dict)
    repositories: list[dict[str, str]] = field(default_factory=list)
    # Runtime repository list (``dnf repolist --all``), backing ``DnfRepoList``:
    # each entry is a ``{repoid, name, status}`` dict.
    repo_list: list[dict[str, str]] = field(default_factory=list)
    enabled_modules: dict[str, str] = field(default_factory=dict)  # name -> stream
    disabled_modules: list[str] = field(default_factory=list)  # disabled module names
    installed_modules: dict[str, str] = field(default_factory=dict)  # name -> stream
    # Installed/available package-group names (``dnf group list``), backing
    # ``DnfGroups``/``DnfAvailableGroups``. Named distinctly from ``Host.group``
    # (the inventory classifier) so the two are never confused.
    installed_groups: set[str] = field(default_factory=set)
    available_groups: set[str] = field(default_factory=set)
    # ``.rpm`` files present on the host (path -> {name, version}) and the
    # packages installed from them (name -> version), backing ``RpmPackage``.
    # ``dnf.rpm`` installs the file's package: ``install_rpm`` copies the file's
    # name/version into ``rpm_packages`` (and the name into ``packages``), so a
    # repeat is a no-op and a removal (``present=False``) clears both.
    rpm_files: dict[str, dict[str, str]] = field(default_factory=dict)
    rpm_packages: dict[str, str] = field(default_factory=dict)
    # GPG key content imported via ``dnf.key`` (the raw bytes of each imported
    # key). ``dnf.key`` is non-idempotent and reads no fact, so nothing in the
    # library reads this back; it exists so a test can observe that the
    # simulated ``rpm --import`` ran. ``import_key`` fills it and clears the
    # ``/tmp`` spool entry the paired ``DnfKeyUploadCommand`` wrote into
    # ``remote_files``.
    imported_keys: set[bytes] = field(default_factory=set)
    # Simulated systemd: full unit name (``httpd.service``) -> its ActiveState
    # string (``active``/``inactive``/...), backing ``SystemdServiceState``;
    # and the set of unit names enabled on boot, backing
    # ``SystemdServiceEnabled``. A unit absent from ``services`` reads as
    # None (as the fact reports when systemd answers nothing).
    services: dict[str, str] = field(default_factory=dict)
    enabled_services: set[str] = field(default_factory=set)
    # The machine architecture (``uname -m``), backing ``Arch``. Defaults to
    # ``x86_64`` -- a real machine always answers ``uname -m`` with something.
    arch: str = "x86_64"
    # SSH public keys, backing ``AuthorizedKeys``: keyed by username for the
    # default ``~user/.ssh/authorized_keys`` path and by the explicit path
    # when the fact is given one; each value is the list of full key strings
    # the file lists (already stripped, no comments -- the parsed form).
    authorized_keys: dict[str, list[str]] = field(default_factory=dict)
    # Mounted block devices (device -> {blocks, used, available,
    # used_percent, mount}), backing ``BlockDevices``. Each value is the
    # parsed form the fact returns -- all five entries raw strings, as
    # ``df`` prints them. Defaults to {} -- a simulated machine with no
    # devices listed, like pyinfra's ``default = dict`` for a failed read.
    block_devices: dict[str, dict[str, str]] = field(default_factory=dict)
    # CPU details (the parsed ``LANG=C lscpu`` rows), backing ``CpuInfo``:
    # each key maps to its stripped string value, except a ``flag``-bearing
    # key (e.g. ``Flags``) whose value is the list of whitespace-split tokens.
    # Defaults to {} -- a simulated machine reporting no lscpu rows; the real
    # transport reads None on a failed/empty read (pyinfra's ``invalid``
    # fixture), but the seam stays a non-None dict so it never falls through
    # to the shell.
    cpu_info: dict[str, str | list[str]] = field(default_factory=dict)
    # The number of online CPUs (the parsed ``getconf NPROCESSORS_ONLN``
    # answer), backing ``Cpus``. Defaults to pyinfra's own fact fixture value
    # (1) -- a real machine always answers ``getconf``, and a fixed default
    # keeps simulated reads deterministic.
    cpus: int = 1
    # The server's clock, backing ``Date``. Defaults to pyinfra's own fact
    # fixture timestamp (2019-01-01T00:00:00+00:00) -- a real machine always
    # answers ``date``, so the seam never falls through to the shell, and a
    # fixed default keeps simulated reads deterministic.
    date: datetime = datetime(2019, 1, 1, tzinfo=UTC)
    # Per-device IO counters (device -> the int counter dict the fact
    # returns), backing ``DiskStats``. Each value holds whichever counter
    # groups the simulated kernel prints -- the eleven classic counters,
    # plus the discard/flush groups on a modern kernel. Defaults to {} --
    # a simulated machine with no /proc/diskstats rows, like the fact's
    # {} fallback for a failed read.
    disk_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    # DMI identity fields (the parsed /sys/class/dmi/id/ dump: sys_vendor,
    # product_name, bios_version, chassis_type, ...), backing ``DmiInfo``.
    # Each value is the raw string the sysfs file holds, with uevent/
    # modalias and empty values already absent. Defaults to {} -- a
    # simulated machine with no DMI identity (a container), like the
    # fact's {} fallback for a failed read.
    dmi_info: dict[str, str] = field(default_factory=dict)
    # Group names present on the system (the first ``/etc/group`` field of
    # each row, in file order), backing ``Groups``. Named distinctly from
    # ``Host.group`` (the inventory classifier) so the two are never confused.
    os_groups: list[str] = field(default_factory=list)
    # Whether a known desktop-session binary is installed, backing
    # ``HasGui``. Defaults to False -- a simulated server is headless, like
    # pyinfra's gui_absent fixture (``ls /usr/bin/*session`` lists nothing).
    has_gui: bool = False
    # Home directories, backing ``Home``: username -> home directory path,
    # with "" keying the login user (the bare ``~``). An absent user reads as
    # None, like the shell failing to expand ``~user`` for an unknown user.
    home_directories: dict[str, str] = field(default_factory=dict)
    # The machine's hostname (``uname -n``), backing ``Hostname``. Named
    # ``os_hostname`` to stay distinct from ``Host.name`` (the inventory
    # label) and the inventory's ``connection.hostname`` address alias.
    # Defaults to pyinfra's own fact fixture hostname -- a real machine
    # always answers ``uname -n``, and a fixed default keeps simulated
    # reads deterministic.
    os_hostname: str = "my-host.net"
    # IPv4 addresses per network interface (the parsed `ip addr show` /
    # `ifconfig -a` listing), backing ``Ipv4Addrs``: interface name -> the
    # list of address strings the fact returns, primary first, with any
    # interfaces that carry no IPv4 already absent. Defaults to {} -- a
    # simulated machine with no interfaces listed, like pyinfra's
    # ``default = dict`` for a failed read.
    ipv4_addrs: dict[str, list[str]] = field(default_factory=dict)
    # IPv6 addresses per network interface (the parsed `ip addr show` /
    # `ifconfig -a` listing), backing ``Ipv6Addrs``: interface name -> the
    # list of address strings the fact returns, primary first, with any
    # interfaces that carry no IPv6 already absent. Defaults to {} -- a
    # simulated machine with no interfaces listed, like pyinfra's
    # ``default = dict`` for a failed read.
    ipv6_addrs: dict[str, list[str]] = field(default_factory=dict)
    # The kernel name (``uname -s``), backing ``Kernel``. Named ``os_kernel``
    # with the os_ prefix (like ``os_hostname``) so it stays clear of any
    # inventory field. Defaults to ``Linux`` -- a real machine always answers
    # ``uname -s``, and the simulated fleet is Linux servers (pyinfra's own
    # fixture value is the literal placeholder "os", not a real kernel name).
    os_kernel: str = "Linux"
    # Loaded kernel modules (module name -> info dict), backing
    # ``KernelModules``. Each value is the parsed form the fact returns: on
    # Linux {size, instances, state} plus an optional depends list, on
    # FreeBSD {id, refs, address, size} -- the seam holds whichever shape
    # matches the host's ``os_kernel``. Defaults to {} -- a simulated machine
    # with no modules listed, like pyinfra's ``default = dict`` for a failed
    # or absent /proc/modules read.
    kernel_modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The kernel release (``uname -r``), backing ``KernelVersion``. Named
    # ``os_kernel_version`` with the os_ prefix (like ``os_kernel``) so it
    # stays clear of any inventory field. Defaults to a real Fedora kernel
    # release -- a real machine always answers ``uname -r``, and the
    # simulated fleet is dnf-based Linux servers (pyinfra's own fixture
    # value is the literal placeholder "os_version", not a real release).
    os_kernel_version: str = "6.11.4-301.fc41.x86_64"
    # Login records (the parsed ``last`` output), backing ``Last``. Each
    # entry is the dict the fact returns -- {user, tty, host, time} with
    # time the raw trailing string -- in ``last`` order. Defaults to [] --
    # a simulated machine with no recorded logins, like pyinfra's
    # ``default = list`` for an empty or failed ``last`` read.
    login_records: list[dict[str, str]] = field(default_factory=list)
    # The Linux distribution info (the parsed /etc/*-release dump), backing
    # ``LinuxDistribution``: {name, major, minor, release_meta} in the shape
    # the fact returns. ``LinuxName`` delegates to ``LinuxDistribution`` and
    # so reads this same seam -- there is deliberately no separate name
    # field, keeping the two facts consistent by construction. Defaults to
    # pyinfra's ``default()`` -- all None with an empty release_meta, the
    # "no release files matched" answer -- so a simulated machine makes no
    # distro claim until a test sets one.
    linux_distribution: dict[str, Any] = field(
        default_factory=lambda: {
            "name": None,
            "major": None,
            "minor": None,
            "release_meta": {},
        }
    )
    # Installed GUI names (the parsed ``ls /usr/bin/*session`` listing),
    # backing ``LinuxGui``: the list of names the fact returns, in listing
    # order. Defaults to [] -- a simulated server is headless, matching the
    # ``has_gui`` flag's False default; a test simulating a desktop should
    # set both seams consistently (``has_gui`` is ``bool(linux_gui)``).
    linux_gui: list[str] = field(default_factory=list)
    # Installed locale names (the parsed ``locale -a`` listing, with ``utf8``
    # already normalized to ``UTF-8``), backing ``Locales``: the list of names
    # the fact returns, in command order. Defaults to [] -- a simulated
    # machine with no locales listed, like pyinfra's ``default = list`` for an
    # empty or failed ``locale -a`` read.
    locales: list[str] = field(default_factory=list)
    # Release information (the parsed ``lsb_release -ca`` rows), backing
    # ``LsbRelease``: the lower-cased key -> stripped value dict the fact
    # returns (``Distributor ID`` already reduced to ``id``). Defaults to
    # {} -- a simulated machine makes no LSB claim until a test sets one,
    # like the fact's answer when the ``lsb_release`` read fails (a missing
    # ``lsb_release`` raises ``CommandNotFoundError`` on the real transport).
    lsb_release: dict[str, str] = field(default_factory=dict)
    # The block device tree (the parsed ``lsblk -J`` node list), backing
    # ``LsBlk``: each node is the dict the fact returns -- name, type,
    # size (int bytes), mountpoint (None when unmounted) -- with stacked
    # devices nested under ``children``. Defaults to [] -- a simulated
    # machine with no block devices listed, like the fact's [] fallback
    # for a failed or undecodable read.
    lsblk_devices: list[dict[str, Any]] = field(default_factory=list)
    # Mounted filesystems (mount point -> {device, type, options}), backing
    # ``Mounts``. Each value is the parsed form the fact returns, with the
    # options already combined and octal escapes decoded -- the seam holds
    # whichever kernel's listing the host's ``os_kernel`` implies. Defaults
    # to {} -- a simulated machine with no mounts listed, like pyinfra's
    # ``default = dict`` for a failed read.
    mounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Network devices (interface -> {state, ether?, mtu?, ipv4?, ipv6?}),
    # backing ``NetworkDevices``. Each value is the parsed form the fact
    # returns -- an ipv4/ipv6 block holds the primary address plus its
    # mask/broadcast details and any ``additional_ips`` aliases. Defaults
    # to {} -- a simulated machine with no interfaces listed, like pyinfra's
    # ``default = dict`` for a failed read.
    network_devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The login user's PATH environment variable (the raw colon-separated
    # ``echo $PATH`` string), backing ``Path``. Named ``os_path`` with the
    # os_ prefix (like ``os_kernel``) so it stays clear of any inventory
    # field. Defaults to a standard Linux search path -- a real machine
    # always answers ``echo $PATH``, and a fixed default keeps simulated
    # reads deterministic (pyinfra ships no fixture for this fact).
    os_path: str = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin"
    # Running processes (PID -> {user, state, cpu_percent, mem_percent,
    # command, args}), backing ``Processes``. Each value is the parsed form
    # the fact returns -- the seam holds the joined record regardless of
    # which kernel's ps listing ``os_kernel`` implies. Defaults to {} -- a
    # simulated machine with no processes listed, like pyinfra's
    # ``default = dict`` for a failed ps read.
    processes: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Decoded SMBIOS tables (section title -> the field dict the fact
    # returns), backing ``SmbiosInfo``. Each field value is a raw string,
    # except a multi-item field (e.g. ``Characteristics``) whose value is
    # the list of item strings. Defaults to {} -- a simulated machine
    # whose dmidecode read fails (no binary, no root or passwordless
    # sudo), like the fact's {} fallback for a failed read.
    smbios_info: dict[str, dict[str, str | list[str]]] = field(default_factory=dict)
    # The system timezone (the ``readlink -f /etc/localtime | sed`` answer),
    # backing ``Timezone``. Named ``os_timezone`` with the os_ prefix (like
    # ``os_kernel``) so it stays clear of any inventory field. Defaults to
    # pyinfra's own fact fixture timezone -- a real machine always resolves
    # ``/etc/localtime``, and a fixed default keeps simulated reads
    # deterministic.
    os_timezone: str = "Europe/Amsterdam"
    # The current user's name (the ``echo $USER`` answer), backing ``User``.
    # Named ``os_user`` with the os_ prefix (like ``os_path``) so it stays
    # clear of the inventory's ``connection.user`` login name. Defaults to
    # ``root`` -- a real machine always answers ``echo $USER``, and a fixed
    # default keeps simulated reads deterministic (pyinfra ships no fixture
    # for this fact).
    os_user: str = "root"
    # System users (username -> the details dict ``Users`` returns -- home,
    # comment, shell, group, groups, uid, gid, lastlog, login_time,
    # password), backing ``Users``. Named ``os_users`` with the os_ prefix
    # (like ``os_user``) so it stays clear of the inventory's
    # ``connection.user`` login name. Defaults to {} -- a simulated machine
    # with no users listed, like pyinfra's ``default = dict`` for a failed
    # or empty read.
    os_users: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The seconds since boot (the parsed ``cut -d. -f1 /proc/uptime`` answer,
    # or the BSD now-minus-boottime difference), backing ``Uptime``. Defaults
    # to pyinfra's own fact fixture uptime (86400 -- one day) -- a real
    # machine always answers with an uptime, and a fixed default keeps
    # simulated reads deterministic.
    uptime_seconds: int = 86400
    # Resolvable commands (command name -> the path ``command -v`` prints),
    # backing ``Which``. An absent command reads as None, like the swallowed
    # non-zero exit of ``command -v nosuchcommand || true``. Defaults to {} --
    # a simulated machine resolves no commands until a test names them.
    command_paths: dict[str, str] = field(default_factory=dict)
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
            case DnfRepoCommand(path=path, content=None):
                self.remove_repo_file(path)
            case DnfRepoCommand(path=path, content=content):
                self.write_repo_file(path, content)
            case RpmInstallCommand(src=src):
                self.install_rpm(src)
            case DnfKeyUploadCommand(src=src, dest=dest):
                self.put_file(src, dest)
            case DnfKeyImportCommand(path=path):
                self.import_key(path)
            case DnfModuleEnableCommand(module=module, stream=stream):
                self.enable_module(module, stream)
            case DnfModuleDisableCommand(module=module):
                self.disable_module(module)
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
        for package in packages:
            name = package.split("=", 1)[0]
            self.packages.discard(name)
            # A package installed from a ``.rpm`` (``dnf.rpm`` ``present=False``
            # reuses DnfRemoveCommand) must also leave ``rpm_packages``, or
            # ``RpmPackage`` would still report it installed.
            self.rpm_packages.pop(name, None)

    def install_rpm(self, src: str) -> None:
        """Record a simulated ``rpm -i``: mark the file's package installed.

        Reads the ``.rpm``'s name/version from ``rpm_files`` (the seam
        ``RpmPackage`` reads for a file path) and records them in
        ``rpm_packages`` plus ``packages``, so a repeat ``dnf.rpm`` is a no-op
        and ``DnfPackages`` also sees the package. An unknown ``.rpm`` path has
        no metadata to install, so nothing changes.
        """
        info = self.rpm_files.get(src)
        if info is None:
            return
        name = info["name"]
        self.rpm_packages[name] = info["version"]
        self.packages.add(name)

    def import_key(self, path: str) -> None:
        """Record a simulated ``rpm --import`` of the spooled key at ``path``.

        ``dnf.key`` first uploads the key stream to a ``/tmp`` spool path (the
        paired ``DnfKeyUploadCommand`` runs through ``put_file``, storing the
        bytes in ``remote_files``), then imports and removes it. This mirrors
        the import-and-remove: the spool bytes are popped out of
        ``remote_files`` and recorded in ``imported_keys``, so a test can assert
        both that the key was imported and that the spool file was cleaned up.
        An absent spool path imports nothing.
        """
        data = self.remote_files.pop(path, None)
        if data is not None:
            self.imported_keys.add(data)

    def enable_module(self, module: str, stream: str) -> None:
        """Record a simulated ``dnf module enable``: set the enabled stream.

        A module is either enabled on one stream or disabled, never both, so
        enabling also clears any disabled marker -- keeping ``enabled_modules``
        and ``disabled_modules`` mutually consistent for the ``DnfEnabledModules``
        and ``DnfDisabledModules`` facts. A repeat enable of the same stream is
        then a no-op, and a switch re-points the stream.
        """
        self.enabled_modules[module] = stream
        if module in self.disabled_modules:
            self.disabled_modules.remove(module)

    def disable_module(self, module: str) -> None:
        """Record a simulated ``dnf module disable``: mark the module disabled.

        The mirror of ``enable_module``: clears any enabled stream and keeps
        ``disabled_modules`` sorted, matching what ``DnfDisabledModules`` reports
        (its ``parse`` returns sorted, de-duplicated names), so a repeat disable
        is a no-op.
        """
        self.enabled_modules.pop(module, None)
        if module not in self.disabled_modules:
            self.disabled_modules.append(module)
        self.disabled_modules.sort()

    def make_link(self, src: str, target: str) -> None:
        self.links[src] = target

    def remove_link(self, src: str) -> None:
        self.links.pop(src, None)

    def create_file(self, src: str) -> None:
        self.existing_files.add(src)

    def remove_file(self, src: str) -> None:
        self.existing_files.discard(src)

    def write_repo_file(self, path: str, content: str) -> None:
        """Record a simulated ``dnf.repo`` write: store the content bytes.

        Mirrors ``put_file``: the content lands in ``remote_files`` -- the same
        store ``DnfRepoFile`` reads back (and ``RemoteFileSha1`` hashes) -- so a
        repeat ``dnf.repo`` with identical arguments is a no-op, since the fresh
        per-step fact cache sees these bytes and the content matches.
        """
        self.remote_files[path] = content.encode("utf-8")

    def remove_repo_file(self, path: str) -> None:
        """Record a simulated ``dnf.repo`` removal (``present=False``)."""
        self.remote_files.pop(path, None)

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
