# Inventory demonstrations

Scripts under `examples/demonstrations/inventory/` — scenarios that cross
module boundaries to show how an inventory is sourced. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

## Inventory from a JSON file

The same deploy as the base example, with the target hosts kept in
`inventory.json` instead of in the source: read the file, validate it with
[`validate_inventory`][reemote5.hosts.validate_inventory], and hand the
plain list of dictionaries to `run`.

::: json_inventory
    options:
      members: []
