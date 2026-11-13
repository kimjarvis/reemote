# Handlers

`reemote.handlers` packages the **two-deploy outcome split** — reemote's answer
to Ansible's `notify:`/handler mechanism — into two reusable helpers, so a role
migration stops copy-pasting a bespoke `notifying_hosts`/`build_restart_*` pair.

reemote has no first-class handler: a deploy body cannot observe its own earlier
outcomes (`ctx.changed` flows one way, operation → driver →
[`ApplyReport`][reemote.drivers.ApplyReport]), and branching on it inside one
deploy would make the prepare and apply phases yield different request
sequences, tripping [`DeterminismError`][reemote.drivers.DeterminismError]. The
sanctioned shape is to run the notifying tasks, read their report in the gap
*between* two complete prepare/apply cycles, freeze the hosts that changed, and
pass that frozen set into a second deploy as a static input. See
[Change notification (handlers)](guide.md#change-notification-handlers) for the
full rationale and a runnable
[example](examples/demonstrations/composition.md#restarting-a-service-only-when-a-notifying-operation-changed).

[`changed_hosts`][reemote.handlers.changed_hosts] is the *notification read*:
one pass over a report's log, freezing the hosts whose **named** entries
reported `changed` into a `frozenset[str]`. A `changed` entry is trustworthy —
the driver is fail-fast, so a non-zero command exit raises
[`CommandError`][reemote.hosts.CommandError] rather than logging a change — and
a skipped entry (a `when` guard failed) carries `changed=False` and never queues
the handler.

[`service_handler`][reemote.handlers.service_handler] is the *handler factory*:
it closes over that frozen set and returns a deploy-shaped callable
(`(host) -> generator of OperationRequest`) that yields one
[`systemd.service`][reemote.modules.systemd.systemd.service] request per service,
for the notified hosts only. Its `action` picks the verb — `"restarted"`,
`"reloaded"` or `"running"` — reproducing the matching Ansible `state:`. Because
its branch consults a snapshot taken *before* the deploy's prepare phase, both
phases resolve it identically and the determinism guard holds; an empty host set
yields an empty deploy — the converged no-op. It composes with the other deploy
helpers: pass it to [`run`][reemote.drivers.run],
[`compose`][reemote.core.compose] or [`sub_deploy`][reemote.core.sub_deploy].

[`restart_handler`][reemote.handlers.restart_handler] and
[`reload_handler`][reemote.handlers.reload_handler] are thin wrappers pinning
`action` to the two commonest idioms — restart-on-change and the
reload-on-config-change half of a role that also restarts on other changes.

```python
from reemote import changed_hosts, reload_handler, restart_handler, run

_, report, _ = await run(inventory, config_deploy)          # the notifying tasks
changed = changed_hosts(report, {"Write app config"})       # the notification read
restart = restart_handler(changed, "app", when=is_web)      # systemd.service(restarted=True)
reload = reload_handler(changed, "nginx", when=is_web)      # systemd.service(reloaded=True)
await run(inventory, restart)                               # act on changed hosts only
```

## Reference

::: reemote.handlers
    options:
      members:
        - changed_hosts
        - service_handler
        - restart_handler
        - reload_handler
