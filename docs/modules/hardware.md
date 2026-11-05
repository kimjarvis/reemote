# hardware module

## hardware Facts

::: reemote5.modules.hardware.hardware
    options:
      show_root_heading: false
      members:
        - BlockDevices
        - CpuInfo
        - Cpus
        - DiskStats
        - DmiInfo
        - Ipv4Addrs
        - Ipv6Addrs
        - LsBlk
        - NetworkDevices
        - SmbiosInfo

<!---

`reemote5.modules.hardware.hardware` holds the hardware facts — `BlockDevices`,
the mounted block devices and their disk usage read from `df`, `CpuInfo`,
the CPU details reported by `lscpu`, `Cpus`, the number of online CPUs
reported by `getconf`, `DiskStats`, the per-device IO counters read from
`/proc/diskstats`, `DmiInfo`, the system identity, BIOS and chassis fields
exposed under `/sys/class/dmi/id/`, `Ipv4Addrs`/`Ipv6Addrs`, the IPv4/IPv6
addresses per network interface, `LsBlk`, the complete block device tree
reported by `lsblk -J`, `NetworkDevices`, the full network device
details, the last three read from `ip addr show` or `ifconfig -a`, and
`SmbiosInfo`, the full SMBIOS/DMI tables decoded by `dmidecode`.
`DiskStats`, `DmiInfo`, `LsBlk` and `SmbiosInfo` are reemote5 originals
with no pyinfra counterpart: `DiskStats` and `LsBlk` complement
`BlockDevices`, whose `df` listing only shows mounted filesystems, while
`DmiInfo` and `SmbiosInfo` report the machine's firmware identity —
manufacturer, product name, serial number, BIOS version and chassis
type — for asset tracking and conditional execution.
It is a fact-only namespace so far: no operations live here yet.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `BlockDevices` takes
no arguments: it runs `df` and returns a dict of device → `{blocks, used,
available, used_percent, mount}` dict, mirroring pyinfra's
`hardware.BlockDevices`. Every value stays a raw string — the block counts
and the percentage are not converted to ints, exactly like pyinfra. The
header row and any line that does not match the `df` row shape are skipped,
including rows whose device or mount point carries a character outside the
row regex's character classes (e.g. an NFS `host:/export` source), and so is
the `none` pseudo-filesystem; a repeated device keeps the last row. Empty
output (a failed read) reads as `{}`, pyinfra's `default = dict`. Because
`df` only lists mounted filesystems, unmounted disks, LVM volumes and raw
devices never appear here — the `LsBlk` sibling reports the complete block
device tree and `DiskStats` the per-device IO counters.

`CpuInfo` also takes no arguments: it runs `LANG=C lscpu` and returns a dict
mapping each `Key: value` row's stripped key to its stripped value, mirroring
pyinfra's `hardware.CpuInfo`. A key containing `flag` (case-insensitively,
e.g. `Flags`) has its value split on whitespace into a list of tokens; every
other value stays a raw string. As in pyinfra the value is the segment after
the first colon, so a second colon in a value truncates it; lines without a
colon are skipped. Empty output (a failed read) reads as `None`, exactly like
pyinfra's `invalid` fixture, and a missing `lscpu` exits 127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError] rather than
`None` (pyinfra's `requires_command` gating is deliberately dropped).

`Cpus` also takes no arguments: it runs
`getconf NPROCESSORS_ONLN 2> /dev/null || getconf _NPROCESSORS_ONLN` (the
fallback covers platforms without the unprefixed name) and converts the
first output line with `int()`, mirroring pyinfra's `hardware.Cpus`.
Anything after the first line is ignored. A first line that is not an
integer reads as `None` — pyinfra's `invalid` fixture — as does empty output
(a failed read), which pyinfra answers with its `default()` fallback of
`None`.

`DiskStats` also takes no arguments: it runs `cat /proc/diskstats` and maps
each device name to the kernel's IO counters as ints — a reemote5 original
with no pyinfra counterpart, reading `/proc/diskstats` directly so no
sysstat package (`iostat`) is required on the target. The eleven classic
counters (`reads_completed`, `reads_merged`, `sectors_read`, `ms_reading`,
`writes_completed`, `writes_merged`, `sectors_written`, `ms_writing`,
`ios_in_progress`, `ms_doing_io`, `weighted_ms_doing_io`) are always
present; a 4.18+ kernel adds the four `discards_*`/`ms_discarding`
counters and a 5.5+ kernel the two `flush_requests`/`ms_flushing`
counters, each group included only when the row carries it. Sectors are
512-byte units, and the counters are cumulative since boot: a single
gather is a snapshot, and a rate needs two gathers subtracted over their
elapsed time. Every row is kept — including `loop`/`ram`/`dm-*` devices —
so the caller can filter; a repeated device keeps the last row. Short
rows, rows with a non-integer counter and columns beyond the flush group
(a future kernel) are skipped or ignored. Empty output (a failed read)
reads as `{}`.

`DmiInfo` also takes no arguments: it runs
`grep -H '' /sys/class/dmi/id/* 2> /dev/null || true` and maps each DMI
identity file's name to its raw value (`sys_vendor`, `product_name`,
`product_serial`, `bios_vendor`, `bios_version`, `bios_date`,
`chassis_type`, ...) — a reemote5 original with no pyinfra counterpart.
Reading sysfs needs no `dmidecode` binary and no root for most fields,
so this is the unprivileged sibling of `SmbiosInfo`; the root-only files
(`product_serial`, `product_uuid`, `board_serial`, `chassis_serial`) are
simply absent when the login user cannot read them. Values stay raw
strings: `chassis_type` is the numeric SMBIOS chassis code (e.g. `3`
desktop, `10` notebook, `23` rack mount — `SmbiosInfo` reports the
decoded label instead). The directory's `uevent`/`modalias` entries,
empty values and lines outside the `/sys/class/dmi/id/` prefix are
skipped. A machine without DMI (most containers, many ARM boards and
VMs) has no such directory: the failed read maps to `{}`, so an empty
dict means "no DMI identity available".

`Ipv4Addrs` also takes no arguments: it runs
`ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a`
and maps each interface carrying IPv4 addresses to the list of those
addresses — the primary address first, then any aliases in listing order —
mirroring pyinfra's `hardware.Ipv4Addrs`. Interfaces without an IPv4
address are not part of the dict, and the addresses are the raw strings
the command printed (no octet validation). pyinfra implements the fact as
a `ShortFactBase` projection over `hardware.NetworkDevices`; reemote5
collapses that indirection into this single class, keeping only the IPv4
address lists (the IPv6 addresses are the sibling `Ipv6Addrs` fact's
projection); the richer device dict — ether, mtu, state and each address's
mask/broadcast details — is the sibling `NetworkDevices` fact. Empty or
undecodable output reads as `{}`, pyinfra's `default = dict`.

`Ipv6Addrs` also takes no arguments: it runs the same
`ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a`
and maps each interface carrying IPv6 addresses to the list of those
addresses — the primary address first, then any aliases in listing order —
mirroring pyinfra's `hardware.Ipv6Addrs`. Interfaces without an IPv6
address are not part of the dict, and the addresses are the raw strings
the command printed (no address validation — pyinfra's documented example
even carries a doubled `::`). pyinfra implements the fact as a
`ShortFactBase` projection over `hardware.NetworkDevices` (a subclass of
its `Ipv4Addrs` swapping the projected family); reemote5 collapses that
indirection into this single sibling class sharing `Ipv4Addrs`' command
and projection helpers, keeping only the IPv6 address lists; the richer
device dict — ether, mtu, state and each address's mask details — is the
sibling `NetworkDevices` fact. Empty or undecodable output reads as `{}`,
pyinfra's `default = dict`.

`LsBlk` also takes no arguments: it runs
`lsblk -J -b -o NAME,TYPE,SIZE,MOUNTPOINT` and returns the JSON document's
`blockdevices` array — a reemote5 original with no pyinfra counterpart,
reporting the complete block device tree including unmounted disks,
partitions, LVM volumes and raw devices that the `df`-based
`BlockDevices` sibling never sees. Each node carries `name`, `type`
(`disk`/`part`/`lvm`/...), `size` (a byte count as an int, from `-b`) and
`mountpoint` (`None` when unmounted; the singular `MOUNTPOINT` column
reports only the last mount when a device is mounted at several paths),
with stacked devices nested under their parent's `children` list so the
tree structure is preserved verbatim. Empty or undecodable output, a
non-dict JSON document and a missing/non-list `blockdevices` key all read
as `[]`; non-dict nodes are skipped at every level and a non-list
`children` value is dropped rather than raising. A missing `lsblk` exits
127 — reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`NetworkDevices` also takes no arguments: it runs the same
`ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a`
and maps each interface to its details dict — the `state`
(`UP`/`DOWN`/`UNKNOWN`), the optional `ether` MAC and `mtu`, and an
`ipv4`/`ipv6` block per address family carrying the primary `address`
plus, when present, `mask_bits`, `netmask` (IPv4 only), `broadcast`
(IPv4 only, possibly `None`) and any further addresses under
`additional_ips` — mirroring pyinfra's `hardware.NetworkDevices`. The
`Ipv4Addrs`/`Ipv6Addrs` siblings are the address-only projections of this
dict. Mask tokens are normalized by pyinfra's `mask` helper: a prefix
length expands to its dotted-quad netmask, a hex (`0xffffff00`) or
dotted-quad netmask counts its bits. In the text branch the state is a
token heuristic (`UP` or `DOWN` anywhere in the device section, else
`UNKNOWN`), device names keep any `@parent` suffix, and a later section
for the same name wins outright; the JSON branch reads `operstate` and
keeps an `ether` even for loopback. Addresses stay raw strings, without
validation. Empty or undecodable output reads as `{}`, pyinfra's
`default = dict`. Unreadable entries are skipped silently (pyinfra logs
them through its logger) and non-dict JSON devices or address entries
are skipped rather than raising — reemote5 hardens the parser.

`SmbiosInfo` also takes no arguments: it runs
`dmidecode -q 2> /dev/null || sudo -n dmidecode -q 2> /dev/null || true`
and maps each SMBIOS section's
verbatim title (`BIOS Information`, `System Information`,
`Chassis Information`, ...) to its field dict — a reemote5 original with
no pyinfra counterpart and the privileged sibling of `DmiInfo`:
`dmidecode` usually requires root (and the binary must be installed),
but in exchange it decodes the full tables — the chassis `Type` arrives
as a label (`Rack Mount Chassis`, `Notebook`, `Other`, ...) rather than
`DmiInfo`'s numeric code, and sections beyond the identity fields
(processor, memory, ports, ...) are included too. When the direct read
fails, the command retries through `sudo -n dmidecode -q` — a deliberate,
documented deviation from "facts never elevate": `-n` never prompts, so
the retry succeeds only for a root login or passwordless (`NOPASSWD`)
sudo, creates no askpass file and raises no `PrivilegeError`; the
inventory's `sudo_password` is deliberately **not** consulted (the
library's askpass elevation belongs to operations). The `-q` flag
suppresses the handle/SMBIOS-version preamble. A field whose value is a
list of deeper-indented items (e.g. `Characteristics`) collects them
into a list of strings; every other value stays a raw string, and a
colon-bearing field with no value and no items reads as `""`. A repeated
section title (`Memory Device` appears once per DIMM) keeps the last
occurrence — the identity, BIOS and chassis sections each appear once.
A failed read (missing `dmidecode`, no root or passwordless sudo, no
SMBIOS tables) is swallowed by the command and maps to `{}` — an empty
dict cannot distinguish "no tables" from "no access"; use `DmiInfo` for
an unprivileged read.


## Example

The read-only fact-gathering scripts `hardware_block_devices.py`,
`hardware_cpu_info.py`, `hardware_cpus.py`, `hardware_disk_stats.py`,
`hardware_dmi_info.py`, `hardware_ipv4_addrs.py`,
`hardware_ipv6_addrs.py`, `hardware_lsblk.py`,
`hardware_network_devices.py` and `hardware_smbios_info.py`
under
[`examples/modules/hardware/`](../examples/modules/hardware.md) gather
`BlockDevices`, `CpuInfo`, `Cpus`, `DiskStats`, `DmiInfo`, `Ipv4Addrs`,
`Ipv6Addrs`, `LsBlk`, `NetworkDevices` and `SmbiosInfo` respectively
across an inventory with
[`gather_fact`][reemote5.drivers.gather_fact] and print each host's result
as JSON.

--->