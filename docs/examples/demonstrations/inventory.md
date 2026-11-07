# Inventory demonstrations

Scripts under `examples/demonstrations/inventory/` — scenarios that cross
module boundaries to show how an inventory is sourced. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

## CentOS hosts from a JSON file

The `dnf_packages.py` deploy — install httpd, then remove its welcome
configuration — with the target hosts kept in `inventory_centos.json`
instead of in the source: read the file, validate it with
[`validate_inventory`][reemote5.hosts.validate_inventory], and hand the
plain list of dictionaries to `run`. The deploy is guarded by `is_centos`
(`host.group == "centos"`), composed once with `sub_deploy(..., when=...)`
so the inner operations carry no `when` of their own and a skipped host
never has its facts read.

::: json_inventory_centos
    options:
      members: []

## Alpine hosts from a JSON file

The same idea with `apk_packages.py`: install apache2, then remove its
default homepage, reading the hosts from `inventory_alpine.json`, with the
deploy guarded by `is_alpine` (`host.group == "alpine"`).

::: json_inventory_alpine
    options:
      members: []

## Ubuntu hosts from a JSON file

The same idea with `apt_packages.py`: install apache2, then remove its
default homepage, reading the hosts from `inventory_ubuntu.json`, with the
deploy guarded by `is_ubuntu` (`host.group == "ubuntu"`).

::: json_inventory_ubuntu
    options:
      members: []
