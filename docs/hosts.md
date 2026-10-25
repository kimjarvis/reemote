# Hosts and the inventory

Hosts and their SSH information are specified as a simple *inventory*: a list
of dictionaries, each describing one host (name, `connection`, `session`,
optionally `sudo` and any extra keys). The inventory is the parameter the
library functions accept; [`Host.from_inventory`][reemote5.hosts.Host.from_inventory]
materializes each entry into a [`Host`][reemote5.hosts.Host] whose
`connection`/`session` data reaches both operations
([`send_command`][reemote5.drivers.send_command]) and facts (each `process`).

`SimulatedHost` is part of the *test framework*, not of the inventory or the
library: it lives in `tests/simulated_host.py` and is the in-memory stand-in
holding the state facts read (`packages`, `links`) and the mutations commands
apply (`execute_command`), which is what makes the "fresh fact cache per
lockstep step" property observable. The library never imports or constructs
it; tests and examples inject it via `run`'s `host_factory` argument and it
implements the seams [`send_command`][reemote5.drivers.send_command] and the
facts fall back to while no real SSH transport exists.

## Reference

::: reemote5.hosts
    options:
      members:
        - Host
