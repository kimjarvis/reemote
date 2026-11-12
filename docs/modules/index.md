# Modules

reemote's facts and operations are grouped into twenty self-contained
*module namespaces*, each shipped under `reemote.modules.<name>` and
imported into a deploy by that name — `from reemote import apt, files,
systemd`. A namespace keeps its own facts, operations, typed entry shapes
and command descriptors together, so `apt.packages` reads `apt.AptPackages`
and never crosses into another namespace.

Each per-module page renders the API surface with mkdocstrings. Class-style
names (`AptPackages`) are facts; lower-case functions (`packages`) are
operations; typed entry shapes describe returned data, not extra commands.

## Package managers

| Module | Purpose |
| --- | --- |
| [apk](apk.md) | Alpine `apk` — packages, holds, world, repositories, virtual groups, autoremove |
| [apt](apt.md) | Debian/Ubuntu `apt` — packages, sources, keys, PPAs, `.deb` files, holds, upgrades |
| [dnf](dnf.md) | Fedora/RHEL `dnf` — packages, modules, groups, repositories, GPG keys, `.rpm` files |
| [flatpak](flatpak.md) | Flatpak applications |
| [snap](snap.md) | Ubuntu `snap` packages |

## Files and version control

| Module | Purpose |
| --- | --- |
| [files](files.md) | Files, directories, links, copies/moves, block edits, put/get transfers, install-from-content, root-owned transform edits, extended attributes |
| [git](git.md) | Git working copies — clone/pull, config, worktrees |

## Services

| Module | Purpose |
| --- | --- |
| [openrc](openrc.md) | Alpine/Gentoo OpenRC services, runlevels, config |
| [systemd](systemd.md) | systemd units — services, timers, drop-ins, sysusers, tmpfiles, mask/preset |

## Firewalls

| Module | Purpose |
| --- | --- |
| [firewalld](firewalld.md) | firewalld zones, services, ports, rich rules, ipsets, masquerade, panic mode |
| [iptables](iptables.md) | iptables/ip6tables chains, rules, policies, save/restore; ipset sets |
| [ufw](ufw.md) | Ubuntu UFW — rules, state, logging, default policy, applications |

## System configuration

| Module | Purpose |
| --- | --- |
| [hardware](hardware.md) | CPU, DMI/SMBIOS, block devices, disk stats, network interfaces, IPv4/IPv6 addresses |
| [selinux](selinux.md) | SELinux mode, booleans, file contexts, logins, ports, users, modules, permissive domains |
| [server](server.md) | Users, groups, hostname, mounts, timezone, kernel modules, locale, `/etc/hosts`, authorized_keys, processes, ports; shell/script/wait operations |
| [sysctl](sysctl.md) | Kernel parameters — sysctl settings and drop-in `.conf` files |

## Security and keys

| Module | Purpose |
| --- | --- |
| [gpg](gpg.md) | GPG keys and keyrings — import/export, trust, generation, deletion, verification |

## Databases

| Module | Purpose |
| --- | --- |
| [mysql](mysql.md) | MySQL/MariaDB users, databases, tables, indexes, privileges, roles, replication, plugins, dump/load |
| [postgresql](postgresql.md) | PostgreSQL roles, databases, schemas, extensions, tables, indexes, privileges, publications/subscriptions, dump/load |

## Containers

| Module | Purpose |
| --- | --- |
| [lxd](lxd.md) | LXD instances, images, profiles, projects, networks, storage pools/volumes, snapshots, operations |
