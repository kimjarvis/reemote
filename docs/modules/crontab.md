# crontab module

<!---

The `crontab` namespace is the canonical home for scheduled cron entries —
reading and managing one entry in a user's crontab table. It migrates
pyinfra's `crontab.crontab` operation and its `crontab.Crontab` fact (the
structured read, as `CrontabEntries`).

The `Crontab` fact dumps the invoking user's whole table
(`crontab -l 2>/dev/null || true`, the empty string when they have none) as
raw text, mirroring `sysctl.SysctlFile`'s raw-content model: the operation
parses the text itself, so environment lines, comments and unrelated entries
round-trip untouched and an unpreservable reformat is impossible. The
`CrontabEntries` companion answers pyinfra's structured question instead —
one `CrontabEntry` per table line, the shape of pyinfra's `CrontabFile
.to_json()` — reading through the same command and seam via the raw-text
fact, so it issues no second shell of its own and a simulated install reads
back structured immediately.

The single `crontab` operation adds, updates or removes **one** scheduled
entry, keyed either by its command or by a `cron_name` marker comment
(`# reemote-name=<name>`, reemote's own marker, not pyinfra's
`# pyinfra-name=`). Identity follows pyinfra's rule: without a `cron_name`
the command names the entry (so commands must be unique within a table); with
one, later changes to `command` overwrite the same named entry and two
identical commands can coexist under different names. The five schedule
fields (`minute`/`hour`/`day_of_month`/`month`/`day_of_week`) default to `*`,
accept an int, a cron string or a list (comma-joined), and are overridden
wholesale by `special_time` (`@reboot`, `@daily`, ...). `present=False`
removes the entry and its marker comment.

Unlike pyinfra — which dumps the table to a temp file and edits it in place
with `sed` (delete/replace) or `echo >>`, then reinstalls with `crontab` —
reemote follows the `sysctl` **spool-and-install** model: the operation
computes the new whole-table text control-side (a pure keyed edit that
preserves every unrelated line), spools it with an **unprivileged** scp into a
deterministic entry-derived `/tmp` directory, then reinstalls it with an
elevated `crontab <spool> && rm -rf <spool-dir>`. The idempotence gate is the
whole table read back through the fact, so an entry already in the desired
state yields nothing.

pyinfra's `interpolate_variables` and `user` options are deliberately dropped.
`interpolate_variables` controls a second shell pass over the command that
reemote never makes, since the table is computed in Python rather than
rendered into `sed`/`echo` scripts. Which table to edit follows the request's
privilege — the inventory run-as user or `sudo`, never an operation parameter:
`crontab` acts on the invoking identity, so the operation carries no `user`.
`crontab` writes the invoking user's own table, so `sudo` is only needed to
manage another user's crontab; the spool transfer itself is always
unprivileged.

--->

::: reemote.modules.crontab.crontab
    options:
      show_root_heading: false
      members:
        - Crontab
        - CrontabEntries
        - CrontabEntry
        - crontab
