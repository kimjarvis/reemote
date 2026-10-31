# Module examples: hardware

Scripts under `examples/modules/hardware/` — one per fact of the
[`hardware`](../../modules/hardware.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`hardware_block_devices.py` reading
`BlockDevices`, `hardware_cpu_info.py` reading `CpuInfo`,
`hardware_cpus.py` reading `Cpus`, `hardware_disk_stats.py` reading
`DiskStats`, `hardware_dmi_info.py` reading `DmiInfo`,
`hardware_ipv4_addrs.py` reading `Ipv4Addrs`,
`hardware_ipv6_addrs.py` reading `Ipv6Addrs`, `hardware_lsblk.py` reading
`LsBlk`, `hardware_network_devices.py` reading `NetworkDevices`, and
`hardware_smbios_info.py` reading `SmbiosInfo`, each
over `gather_fact`) are not rendered
here — they read a single fact, and the facts themselves are documented on the
[`hardware` module page](../../modules/hardware.md).