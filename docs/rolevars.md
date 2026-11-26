# Role variables

`reemote.rolevars` turns an inventory into one resolved, typed variable model
per host. A role's static inputs are a frozen `pydantic` model the role module
defines — one field per knob, each with a default, all under the model's **bare**
field names. [`resolve_vars`][reemote.rolevars.resolve_vars] binds an inventory
entry's role mapping to that model and returns a
[`VarsSet`][reemote.rolevars.VarsSet]; a role factory
closes over the set and reads its own host's model with
[`VarsSet.for_host`][reemote.rolevars.VarsSet.for_host]. The models themselves
belong to each role, not to this module — it is only the framework that resolves
them.

```python
from pydantic import BaseModel, ConfigDict

class ApacheVars(BaseModel):
    model_config = ConfigDict(frozen=True)
    listen_port: int = 80
    server_name: str = "localhost"
    modules: tuple[str, ...] = ()

vars = resolve_vars(ApacheVars, "apache", inventory)

def apache(v: VarsSet[ApacheVars]):
    def deploy(host):
        conf = v.for_host(host)          # this host's resolved ApacheVars
        yield op(files.file, name="Write apache config", path=f"/etc/apache2/{conf.server_name}.conf")
    return deploy
```

## Precedence

For every inventory entry, [`resolve_vars`][reemote.rolevars.resolve_vars]
layers three sources, lowest to highest:

```text
model field defaults  <  the entry's roles[role] mapping  <  overrides
```

Each entry is validated through
[`InventoryEntry`][reemote.hosts.InventoryEntry] first, which claims the
`roles` key — a mapping of role name to that role's variables — so the binding
channel is exactly `entry["roles"][role]`. The keys there are bare field names:
`listen_port: 8080` under the inventory entry's `apache` role mapping sets
`ApacheVars.listen_port`. Another role's variables live under its own role name
in the same dict, so one inventory can carry several roles' variables side by
side without collision — each mapping is its own namespace, and there is no
key-prefix discipline anywhere.

`overrides` are bare field names merged last, and apply to **every host
uniformly**: a role factory closes over one `VarsSet` and sees no single host, so
a value that must vary per host belongs in the entry's role mapping, not in
`overrides`.

## Errors

- A key inside the entry's role mapping that names no field of the model is a
  spelling mistake, not something to ignore, so binding raises
  [`RoleVarsError`][reemote.rolevars.RoleVarsError] naming the offending key and
  every valid field name of the role. It subclasses `ValueError`, like the other
  inventory-validation failures.
- A bad *value* — `listen_port: "eighty"` in the `apache` role mapping — raises
  pydantic's `ValidationError` naming the field, since the merged mapping is
  validated into the model.

## Hashability

The resolved model is **frozen**, so it stays hashable. A factory closes over its
[`VarsSet`][reemote.rolevars.VarsSet], and [`op`][reemote.core.op] rejects
unhashable request arguments with
[`UnhashableArgumentError`][reemote.core.UnhashableArgumentError], so a variable
that reaches an operation must be a `tuple` or a scalar — never a `list` or a
`dict`. Declare sequence fields as `tuple[...]` and pydantic coerces a list from
the inventory into an immutable, hashable value. `VarsSet.values` is a read-only
mapping, so a set shared across every host cannot be mutated by one of them.

## Reference

::: reemote.rolevars
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - VarsSet
        - resolve_vars
        - RoleVarsError
