# Module examples: crontab

Scripts under `examples/modules/crontab/` — one per operation of the
[`crontab`](../../modules/crontab.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering case for the raw-text `Crontab` fact (reading
the invoking user's whole table over `gather_fact`) is not rendered here — it
reads a single fact, and the fact itself is documented on the
[`crontab` module page](../../modules/crontab.md). The structured
`CrontabEntries` read is rendered below, since its example shows how the
parsed entries (schedule fields, `@nickname` special times, environment lines
and attached comments) come back per host.

The operation example reads the shared project-root `inventory.json`.
`crontab` writes the invoking user's *own* table, so the request needs no
`sudo` to manage the login user's schedule — flag it only to manage another
user's crontab through the inventory `privilege`. The command runs
unprivileged against the connected user's crontab database, so there is no
root-owned `/etc` write and no confined-sudo host-policy prerequisite: the
spool lands in world-writable `/tmp` and the install runs `crontab <spool>` as
that user.

## Reading the parsed crontab

The crontab fact-gathering example: `crontab_crontab_entries.py` reads each
host's whole table through
[`CrontabEntries`][reemote.modules.crontab.crontab.CrontabEntries] — the
structured mirror of pyinfra's [`crontab.Crontab`](https://docs.pyinfra.com/en/3.x/facts/crontab.html)
fact — with `gather_fact` over the real SSH transport, and prints one list of
[`CrontabEntry`][reemote.modules.crontab.crontab.CrontabEntry] dicts per
host: every schedule line with its five fields (digits int-normalized),
`@nickname` lines under `special_time`, `NAME=value` lines under `env`, and
the comment lines immediately preceding an entry in `comments`. The read is
unprivileged and needs no `crontab` success — a user with no table, or a
host without the binary, simply reads as `[]`.

::: crontab_crontab_entries
    options:
      members: []

## Ensuring one scheduled entry

The crontab operation: `crontab.crontab` migrates pyinfra's
[`crontab.crontab`](https://docs.pyinfra.com/en/3.x/operations/crontab.html),
adding, updating or removing one entry keyed by a `cron_name` marker comment
or by its command. The script uses the documented weekly `/etc` backup —
`cron_name="backup_etc"` with `minute=0, hour=1, day_of_week=0` — and yields
the *same* request twice to show idempotence across the two phases: prepare
plans both as changes when the entry is absent, while apply installs on the
first and reports the second as a no-op, because the fresh per-step fact cache
now reads the entry back through [`Crontab`][reemote.modules.crontab.crontab.Crontab].
The whole table is edited control-side and reinstalled through the spool-and-
install pattern (an unprivileged scp to `/tmp`, then an elevated `crontab`
install), so unrelated entries, comments and environment lines are preserved;
`present=False` removes the entry and `special_time` replaces the five schedule
fields with a cron nickname.

::: crontab_crontab
    options:
      members: []
