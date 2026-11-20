# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the crontab module: fact, descriptors and operation.

Covers ``reemote.modules.crontab.crontab``: the private helpers
(``_schedule_value``, ``_desired_fields``, ``_render``, ``_parse_line``,
``_fields_differ``, ``_find_entry`` and ``_edit_crontab``'s keyed whole-table
add/update/remove); the ``Crontab`` whole-table fact (command rendering,
``parse`` rejoin, the ``crontab_text`` seam read and the real-transport
fallback) and its structured ``CrontabEntries`` companion (pyinfra's
``crontab.Crontab`` fact fixtures -- simple/comments/env_variables, reused
verbatim -- the malformed-line skip, the seam read through ``Crontab`` and
the ``FactCache`` caching); the two typed descriptors (the non-shell spool transfer's
``__str__`` and the elevated install's ``sh -c`` shell); the ``crontab``
operation (idempotence against the seam, every pyinfra option branch reused
from the upstream operation tests -- add/add_named/add_special_time/
add_existing/edit/remove/change_*_special_time, the list schedule fields and
``**kwargs`` absorbing an unknown argument); the seam mutation through
``send_command``; the end-to-end plan/apply/dry/lockstep and determinism
guards; and the exports.
"""

import hashlib
from collections.abc import Generator

import pytest

from reemote.core import (
    FactCache,
    OpContext,
    OperationRequest,
    op,
)
from reemote.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import crontab
from reemote.modules.crontab.crontab import (
    Crontab,
    CrontabEntries,
    CrontabEntry,
    CrontabInstallCommand,
    CrontabUploadCommand,
    _desired_fields,
    _edit_crontab,
    _fields_differ,
    _find_entry,
    _parse_line,
    _render,
    _schedule_value,
    _spool_path,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "connection": {"key_path": "~/.ssh/id_ed25519"}},
    {"host": "web2", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]

# The deterministic spool the operation uploads to for a given managed entry;
# computed with the module's own helper so a test never hardcodes the digest.
_SPOOL = _spool_path("this_is_a_command", None)
_SPOOL_DIR = _SPOOL.rsplit("/", 1)[0]
_NAMED_SPOOL = _spool_path("this_is_a_command", "a-name")


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


async def materialize(host: Host, request: OperationRequest):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    return ctx, commands


# --- Helpers -------------------------------------------------------------------


def test_schedule_value_defaults_and_comma_join():
    # An unset field is "*"; a scalar keeps its spelling; a list comma-joins.
    assert _schedule_value(None) == "*"
    assert _schedule_value("") == "*"
    assert _schedule_value(5) == "5"
    assert _schedule_value("*/5") == "*/5"
    assert _schedule_value([1, 2, 3]) == "1,2,3"


def test_desired_fields_special_time_overrides_the_five_fields():
    fields = _desired_fields(0, 1, None, None, 0, "@reboot")
    assert fields == {
        "special_time": "@reboot",
        "minute": None,
        "hour": None,
        "day_of_month": None,
        "month": None,
        "day_of_week": None,
    }
    # Without a nickname every unset field is "*".
    fields = _desired_fields(0, 1, None, None, None, None)
    assert fields["special_time"] is None
    assert fields["minute"] == "0"
    assert fields["hour"] == "1"
    assert fields["day_of_month"] == "*"
    assert fields["month"] == "*"
    assert fields["day_of_week"] == "*"


def test_render_schedule_and_special_time_lines():
    fields = _desired_fields(0, 1, None, None, 0, None)
    assert _render(fields, "/bin/tar cf /tmp/etc.tar /etc") == (
        "0 1 * * 0 /bin/tar cf /tmp/etc.tar /etc"
    )
    fields = _desired_fields(None, None, None, None, None, "@reboot")
    assert _render(fields, "/usr/bin/mkdir /var/run/x") == (
        "@reboot /usr/bin/mkdir /var/run/x"
    )


def test_parse_line_skips_comments_blanks_and_env():
    assert _parse_line("") is None
    assert _parse_line("   ") is None
    assert _parse_line("# a comment") is None
    assert _parse_line("MAILTO=root") is None  # env, not a schedule entry
    assert _parse_line("* * * * *") is None  # five fields, no command
    parsed = _parse_line("*/5 * * * * /usr/bin/ping 127.0.0.1")
    assert parsed == {
        "special_time": None,
        "minute": "*/5",
        "hour": "*",
        "day_of_month": "*",
        "month": "*",
        "day_of_week": "*",
        "command": "/usr/bin/ping 127.0.0.1",
    }
    special = _parse_line("@reboot /usr/bin/mkdir /var/run/x")
    assert special["special_time"] == "@reboot"
    assert special["command"] == "/usr/bin/mkdir /var/run/x"


def test_fields_differ_compares_digits_as_ints():
    existing = _parse_line("05 1 * * 0 cmd")
    desired = _desired_fields(5, 1, None, None, 0, None)
    # A differently zero-padded but equivalent minute is not a change.
    assert not _fields_differ(existing, desired)
    desired = _desired_fields(6, 1, None, None, 0, None)
    assert _fields_differ(existing, desired)


def test_find_entry_by_command_and_by_name_marker():
    lines = [
        "# reemote-name=backup_etc",
        "0 1 * * 0 /bin/tar cf /tmp/etc.tar /etc",
        "* * * * * this_is_a_command",
    ]
    # Identity by command (no cron_name).
    found = _find_entry(lines, "this_is_a_command", None)
    assert found is not None
    idx, comment_idx, _parsed = found
    assert idx == 2
    assert comment_idx is None
    # Identity by marker comment (with cron_name).
    found = _find_entry(lines, "/bin/tar cf /tmp/etc.tar /etc", "backup_etc")
    assert found is not None
    idx, comment_idx, _parsed = found
    assert idx == 1
    assert comment_idx == 0
    # With a cron_name the marker comment -- not the command -- is the identity,
    # so the backup_etc entry is found regardless of the command asked for.
    found = _find_entry(lines, "this_is_a_command", "backup_etc")
    assert found is not None
    assert found[0] == 1
    assert found[1] == 0


def test_edit_crontab_adds_to_an_empty_table():
    # pyinfra ``add`` parity: an empty crontab gains the default-schedule line.
    text, changed, reason = _edit_crontab(
        "",
        command="this_is_a_command",
        present=True,
        cron_name=None,
        desired=_desired_fields(None, None, None, None, None, None),
    )
    assert changed
    assert text == "* * * * * this_is_a_command\n"
    assert reason == "crontab entry this_is_a_command absent, will add"


def test_edit_crontab_add_is_idempotent():
    # pyinfra ``add_existing`` parity: an entry already in state is a no-op.
    existing = "* * * * * this_is_a_command\n"
    text, changed, reason = _edit_crontab(
        existing,
        command="this_is_a_command",
        present=True,
        cron_name=None,
        desired=_desired_fields(None, None, None, None, None, None),
    )
    assert not changed
    assert text == existing
    assert reason == "crontab entry this_is_a_command already up to date"


def test_edit_crontab_adds_a_named_entry_with_a_blank_line():
    # pyinfra ``add_named`` parity: the new entry gains a preceding blank line,
    # a reemote marker comment, and the schedule line -- unrelated lines kept.
    existing = "* * * * * apt update\n"
    text, changed, _reason = _edit_crontab(
        existing,
        command="this_is_a_command",
        present=True,
        cron_name="a-name",
        desired=_desired_fields(None, None, None, None, None, None),
    )
    assert changed
    assert text == (
        "* * * * * apt update\n"
        "\n"
        "# reemote-name=a-name\n"
        "* * * * * this_is_a_command\n"
    )


def test_edit_crontab_adds_a_special_time_entry():
    # pyinfra ``add_special_time`` parity: the nickname replaces the fields.
    text, changed, _reason = _edit_crontab(
        "",
        command="this_is_a_command",
        present=True,
        cron_name=None,
        desired=_desired_fields(None, None, None, None, None, "@reboot"),
    )
    assert changed
    assert text == "@reboot this_is_a_command\n"


def test_edit_crontab_updates_a_differing_schedule():
    # pyinfra ``edit`` parity: the line is replaced in place, others preserved.
    existing = "0 1 * * 0 apt update\n* * * * * this_is_a_command\n"
    text, changed, reason = _edit_crontab(
        existing,
        command="this_is_a_command",
        present=True,
        cron_name=None,
        desired=_desired_fields("*/5", None, None, None, None, None),
    )
    assert changed
    assert reason == "crontab entry this_is_a_command schedule differs, will update"
    assert text == "0 1 * * 0 apt update\n*/5 * * * * this_is_a_command\n"


def test_edit_crontab_change_to_and_from_special_time():
    # pyinfra ``change_to_special_time`` / ``change_from_special_time`` parity:
    # a schedule entry becomes a nickname line and vice-versa.
    text, changed, _ = _edit_crontab(
        "* * * * * this_is_a_command\n",
        command="this_is_a_command",
        present=True,
        cron_name=None,
        desired=_desired_fields(None, None, None, None, None, "@reboot"),
    )
    assert changed
    assert text == "@reboot this_is_a_command\n"
    text, changed, _ = _edit_crontab(
        "@reboot this_is_a_command\n",
        command="this_is_a_command",
        present=True,
        cron_name=None,
        desired=_desired_fields(0, None, None, None, None, None),
    )
    assert changed
    assert text == "0 * * * * this_is_a_command\n"


def test_edit_crontab_removes_the_entry_and_its_marker():
    # pyinfra ``remove`` parity: the entry line and its name comment go.
    existing = (
        "* * * * * apt update\n"
        "\n"
        "# reemote-name=a-name\n"
        "* * * * * this_is_a_command\n"
    )
    text, changed, reason = _edit_crontab(
        existing,
        command="this_is_a_command",
        present=False,
        cron_name="a-name",
        desired=_desired_fields(None, None, None, None, None, None),
    )
    assert changed
    assert reason == "crontab entry this_is_a_command present, will remove"
    assert text == "* * * * * apt update\n\n"


def test_edit_crontab_remove_is_idempotent():
    text, changed, reason = _edit_crontab(
        "* * * * * apt update\n",
        command="this_is_a_command",
        present=False,
        cron_name=None,
        desired=_desired_fields(None, None, None, None, None, None),
    )
    assert not changed
    assert text == "* * * * * apt update\n"
    assert reason == "crontab entry this_is_a_command already absent"


# --- Crontab fact --------------------------------------------------------------


def test_crontab_command_lists_the_user_table():
    assert Crontab.command() == "crontab -l 2>/dev/null || true"


def test_crontab_parse_rejoins_with_one_trailing_newline():
    assert Crontab.parse([]) == ""
    assert Crontab.parse(["* * * * * cmd"]) == "* * * * * cmd\n"
    assert Crontab.parse(["a", "b"]) == "a\nb\n"


async def test_crontab_reads_the_simulated_seam():
    host = SimulatedHost(name="h", crontab_text="*/5 * * * * /usr/bin/ping\n")
    assert await Crontab.process(host) == "*/5 * * * * /usr/bin/ping\n"
    # A user with no crontab reads as the empty string, not a fallback shell.
    assert await Crontab.process(SimulatedHost(name="h")) == ""


async def test_crontab_falls_back_to_the_shell_without_the_seam():
    host = CannedShellHost("* * * * * this_is_a_command\n")
    assert await Crontab.process(host) == "* * * * * this_is_a_command\n"
    assert host.shells == ["crontab -l 2>/dev/null || true"]


# --- CrontabEntries fact ---------------------------------------------------------


def test_crontab_entries_parses_pyinfra_simple_fixture():
    # pyinfra's ``tests/facts/crontab.Crontab/simple.json`` output, verbatim
    # -- the first line is tab-separated -- against its fixture fact, with
    # the total-entry form's empty fields added (reemote spells an
    # inapplicable field ``""``/``[]`` rather than omitting the key).
    output = (
        "*\t*\t*\t*\t*\tapt update\n"
        "0 0 * * * apt install -fy\n"
        "@daily echo daily"
    )
    assert CrontabEntries.parse(output) == [
        {
            "command": "apt update",
            "env": "",
            "minute": "*",
            "hour": "*",
            "day_of_month": "*",
            "month": "*",
            "day_of_week": "*",
            "special_time": "",
            "comments": [],
        },
        {
            "command": "apt install -fy",
            "env": "",
            "minute": 0,
            "hour": 0,
            "day_of_month": "*",
            "month": "*",
            "day_of_week": "*",
            "special_time": "",
            "comments": [],
        },
        {
            "command": "echo daily",
            "env": "",
            "minute": "",
            "hour": "",
            "day_of_month": "",
            "month": "",
            "day_of_week": "",
            "special_time": "@daily",
            "comments": [],
        },
    ]


def test_crontab_entries_parses_pyinfra_comments_fixture():
    # ``tests/facts/crontab.Crontab/comments.json``: the comment lines
    # immediately preceding an entry ride in its ``comments``.
    output = (
        "* * * * * apt update\n"
        "# this is a comment\n"
        "# another line in the comment, wut!\n"
        "0 0 * * * apt install -fy"
    )
    entries = CrontabEntries.parse(output)
    assert entries[0]["comments"] == []
    assert entries[1]["comments"] == [
        "# this is a comment",
        "# another line in the comment, wut!",
    ]


def test_crontab_entries_parses_pyinfra_env_variables_fixtures():
    # ``env_variables.json`` (issue #979's crash fix) and
    # ``env_variables_spaced_and_lowercase.json``, verbatim: env lines become
    # entries carrying the whole line, not schedule entries.
    entries = CrontabEntries.parse(
        "MAILTO=me@example.com\n"
        "CRON_TZ=Europe/Amsterdam\n"
        "@reboot echo rebooted\n"
        "0 0 * * * apt update"
    )
    assert [e["env"] for e in entries] == [
        "MAILTO=me@example.com",
        "CRON_TZ=Europe/Amsterdam",
        "",
        "",
    ]
    assert entries[2]["special_time"] == "@reboot"
    assert entries[2]["command"] == "echo rebooted"
    assert entries[3]["command"] == "apt update"
    entries = CrontabEntries.parse(
        "MAILTO = me@example.com\n"
        "cron_tz = Europe/Amsterdam\n"
        "SHELL=/bin/bash\n"
        "0 * * * * echo hi"
    )
    # Spaces around ``=`` and a lowercase name still read as env lines, the
    # line kept verbatim -- and the spaced fields never reach the schedule
    # branch to be misparsed.
    assert [e["env"] for e in entries] == [
        "MAILTO = me@example.com",
        "cron_tz = Europe/Amsterdam",
        "SHELL=/bin/bash",
        "",
    ]
    assert entries[3]["minute"] == 0
    assert entries[3]["command"] == "echo hi"


def test_crontab_entries_int_normalization():
    # Digits become ints (pyinfra's try_int), cron operator strings do not.
    entry = CrontabEntries.parse("05 */5 1 JAN fri cmd")[0]
    assert entry["minute"] == 5
    assert entry["hour"] == "*/5"
    assert entry["day_of_month"] == 1
    assert entry["month"] == "JAN"
    assert entry["day_of_week"] == "fri"


def test_crontab_entries_skips_malformed_lines():
    # A short line has no command -- pyinfra's unpack would raise, reemote
    # skips it -- and a bare ``@nickname`` without a command is no entry
    # either. A trailing comment with no following entry is dropped.
    assert CrontabEntries.parse("* * * *\n@daily\n# trailing\n") == []


def test_crontab_entries_empty_table_reads_as_empty_list():
    assert CrontabEntries.parse("") == []


async def test_crontab_entries_reads_the_seam_through_the_raw_fact():
    # No second seam: the whole-table ``crontab_text`` store serves both
    # facts, so a simulated install reads back structured immediately --
    # and the shell is never reached (a bare Host would raise on connect).
    host = SimulatedHost(
        name="h",
        crontab_text="# reemote-name=backup_etc\n0 1 * * 0 /bin/tar cf /tmp/etc.tar /etc\n",
    )
    assert await CrontabEntries.process(host) == [
        {
            "command": "/bin/tar cf /tmp/etc.tar /etc",
            "env": "",
            "minute": 0,
            "hour": 1,
            "day_of_month": "*",
            "month": "*",
            "day_of_week": 0,
            "special_time": "",
            "comments": ["# reemote-name=backup_etc"],
        }
    ]
    # The default seam (no crontab) reads as [], not a fallback shell.
    assert await CrontabEntries.process(SimulatedHost(name="h")) == []


async def test_crontab_entries_falls_back_to_the_shell_without_the_seam():
    # The delegation reuses the raw fact's guarded command verbatim.
    host = CannedShellHost("* * * * * this_is_a_command\n")
    assert await CrontabEntries.process(host) == [
        {
            "command": "this_is_a_command",
            "env": "",
            "minute": "*",
            "hour": "*",
            "day_of_month": "*",
            "month": "*",
            "day_of_week": "*",
            "special_time": "",
            "comments": [],
        }
    ]
    assert host.shells == ["crontab -l 2>/dev/null || true"]


async def test_crontab_entries_is_cached_by_the_fact_cache():
    host = SimulatedHost(name="h", crontab_text="* * * * * this_is_a_command\n")
    facts = FactCache(host, phase="test")
    first = await facts.get(CrontabEntries)
    host.crontab_text = "* * * * * this_is_a_command\n* * * * * /usr/bin/ping\n"
    # The second read is the cached first result, not a re-gather; each read
    # re-parses fresh dicts, so mutating one entry never leaks into a later
    # parse of the same seam text.
    assert await facts.get(CrontabEntries) is first
    first[0]["command"] = "mutated"
    assert await CrontabEntries.process(host) == [
        {
            "command": "this_is_a_command",
            "env": "",
            "minute": "*",
            "hour": "*",
            "day_of_month": "*",
            "month": "*",
            "day_of_week": "*",
            "special_time": "",
            "comments": [],
        },
        {
            "command": "/usr/bin/ping",
            "env": "",
            "minute": "*",
            "hour": "*",
            "day_of_month": "*",
            "month": "*",
            "day_of_week": "*",
            "special_time": "",
            "comments": [],
        },
    ]


# --- Descriptors ---------------------------------------------------------------


def test_crontab_upload_command_is_a_non_shell_transfer():
    cmd = CrontabUploadCommand(content="* * * * * cmd\n", dest=_SPOOL)
    assert not hasattr(cmd, "shell")
    assert str(cmd) == f"scp <crontab> -> {_SPOOL}"
    assert command_shell(cmd) == str(cmd)
    # The content is a plain hashable str, stable across phases.
    assert hash(cmd) == hash(CrontabUploadCommand(content="* * * * * cmd\n", dest=_SPOOL))


def test_crontab_install_command_wraps_the_chain_in_sh():
    cmd = CrontabInstallCommand(spool=_SPOOL)
    assert cmd.shell() == f"sh -c 'crontab {_SPOOL} && rm -rf {_SPOOL_DIR}'"
    # sudo elevates the WHOLE chain, spool cleanup included.
    assert command_shell(cmd, sudo=True) == f"sudo {cmd.shell()}"


def test_crontab_install_command_quotes_a_pathological_spool():
    cmd = CrontabInstallCommand(spool="/tmp/my spool/crontab")
    assert cmd.shell() == (
        "sh -c 'crontab '\"'\"'/tmp/my spool/crontab'\"'\"' && "
        "rm -rf '\"'\"'/tmp/my spool'\"'\"''"
    )


def test_spool_path_is_entry_derived_and_lives_in_a_subdir():
    # Same entry -> same spool across both phases; a cron_name keys it apart,
    # so two identical commands under different names never clobber one upload.
    assert _spool_path("this_is_a_command", None) == _SPOOL
    assert _spool_path("this_is_a_command", None) == _SPOOL
    assert _spool_path("this_is_a_command", "a-name") == _NAMED_SPOOL
    assert _NAMED_SPOOL != _SPOOL
    digest = hashlib.sha1(b"\0this_is_a_command").hexdigest()[:16]
    assert _SPOOL == f"/tmp/reemote-crontab-{digest}/crontab"


# --- crontab operation ---------------------------------------------------------


async def test_operation_add_yields_spool_then_install():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(crontab.crontab, command="this_is_a_command")
    )
    assert ctx.changed
    assert ctx.reason == "crontab entry this_is_a_command absent, will add"
    assert commands == [
        CrontabUploadCommand(content="* * * * * this_is_a_command\n", dest=_SPOOL),
        CrontabInstallCommand(spool=_SPOOL),
    ]


async def test_operation_no_change_when_entry_in_state():
    host = SimulatedHost(name="h", crontab_text="* * * * * this_is_a_command\n")
    ctx, commands = await materialize(
        host, op(crontab.crontab, command="this_is_a_command")
    )
    assert not ctx.changed
    assert ctx.reason == "crontab entry this_is_a_command already up to date"
    assert commands == []


async def test_operation_named_example_from_pyinfra_docs():
    # The documented weekly-backup example: cron_name + day_of_week/hour/minute.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(
            crontab.crontab,
            command="/bin/tar cf /tmp/etc_bup.tar /etc",
            cron_name="backup_etc",
            day_of_week=0,
            hour=1,
            minute=0,
        ),
    )
    assert ctx.changed
    spool = _spool_path("/bin/tar cf /tmp/etc_bup.tar /etc", "backup_etc")
    assert commands == [
        CrontabUploadCommand(
            content=(
                "# reemote-name=backup_etc\n"
                "0 1 * * 0 /bin/tar cf /tmp/etc_bup.tar /etc\n"
            ),
            dest=spool,
        ),
        CrontabInstallCommand(spool=spool),
    ]
    # A repeat with the entry now installed is a no-op.
    host.crontab_text = commands[0].content
    ctx, commands = await materialize(
        host,
        op(
            crontab.crontab,
            command="/bin/tar cf /tmp/etc_bup.tar /etc",
            cron_name="backup_etc",
            day_of_week=0,
            hour=1,
            minute=0,
        ),
    )
    assert not ctx.changed
    assert commands == []


async def test_operation_special_time_example_from_pyinfra_docs():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(
            crontab.crontab,
            command="/usr/bin/mkdir /var/run/my_directory",
            special_time="@reboot",
        ),
    )
    assert ctx.changed
    assert commands[0].content == "@reboot /usr/bin/mkdir /var/run/my_directory\n"


async def test_operation_update_replaces_the_line():
    host = SimulatedHost(name="h", crontab_text="* * * * * /usr/bin/ping 127.0.0.1\n")
    ctx, commands = await materialize(
        host, op(crontab.crontab, command="/usr/bin/ping 127.0.0.1", minute="*/5")
    )
    assert ctx.changed
    assert ctx.reason.endswith("schedule differs, will update")
    assert commands[0].content == "*/5 * * * * /usr/bin/ping 127.0.0.1\n"


async def test_operation_list_schedule_field_is_comma_joined():
    # op() rejects a list argument (unhashable), so the multi-value spelling
    # reaches the generator directly -- the list joins with commas.
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    commands = [
        cmd
        async for cmd in crontab.crontab(ctx, command="cmd", hour=[8, 12, 18])
    ]
    assert commands[0].content == "* 8,12,18 * * * cmd\n"


async def test_operation_remove_present_false():
    host = SimulatedHost(name="h", crontab_text="* * * * * this_is_a_command\n")
    ctx, commands = await materialize(
        host, op(crontab.crontab, command="this_is_a_command", present=False)
    )
    assert ctx.changed
    assert ctx.reason == "crontab entry this_is_a_command present, will remove"
    assert commands[0].content == ""


async def test_operation_absorbs_unknown_kwargs():
    # ``**kwargs`` swallows a stray pyinfra global argument (op() already
    # strips name/sudo/su); the decision is unaffected.
    host = SimulatedHost(name="h")
    ctx, _commands = await materialize(
        host, op(crontab.crontab, command="this_is_a_command", extra_global="ignored")
    )
    assert ctx.changed


# --- Simulated seam ------------------------------------------------------------


async def test_send_command_mutates_the_crontab_text_seam():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(crontab.crontab, command="this_is_a_command")
    )
    for cmd in commands:
        await send_command(host, cmd)
    assert host.crontab_text == "* * * * * this_is_a_command\n"
    assert _SPOOL not in host.remote_files
    # A repeat now reads the installed table back through the fact -> no-op.
    ctx, commands = await materialize(
        host, op(crontab.crontab, command="this_is_a_command")
    )
    assert not ctx.changed
    assert commands == []


# --- End-to-end: plan / apply / dry / lockstep / determinism -------------------


def schedule_backup(host: Host) -> Generator[OperationRequest]:
    """The example deploy: install a weekly /etc backup cron, elevated."""
    yield op(
        crontab.crontab,
        name="Backup /etc weekly",
        command="/bin/tar cf /tmp/etc_bup.tar /etc",
        cron_name="backup_etc",
        day_of_week=0,
        hour=1,
        minute=0,
        sudo=True,
    )


def empty_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts with no crontab yet."""
    return SimulatedHost.from_inventory(entry)


async def test_runs_across_hosts_in_lockstep():
    spool = _spool_path("/bin/tar cf /tmp/etc_bup.tar /etc", "backup_etc")
    plans, report, hosts = await run(
        INVENTORY, schedule_backup, host_factory=empty_factory
    )
    assert [p.changed for p in plans["web1"]] == [True]
    assert [p.changed for p in plans["web2"]] == [True]
    assert "Backup /etc weekly" in format_plan(plans)
    assert report is not None
    assert [e.name for e in report.log] == ["Backup /etc weekly"] * 2
    log = format_log(report)
    # The transfer is unprivileged (no sudo); the install elevates.
    assert log.count(f"scp <crontab> -> {spool}") == 2
    assert log.count(f"$ sudo sh -c 'crontab {spool} && rm -rf ") == 2
    for host in hosts:
        assert host.crontab_text == (
            "# reemote-name=backup_etc\n"
            "0 1 * * 0 /bin/tar cf /tmp/etc_bup.tar /etc\n"
        )
        assert spool not in host.remote_files
    # A fresh prepare against the now-converged hosts plans no change.
    for host in hosts:
        _runtimes, plans2 = await run_prepare([host], schedule_backup)
        assert [p.changed for p in plans2[host.name]] == [False]


async def test_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, schedule_backup, host_factory=empty_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.crontab_text == ""
        assert host.remote_files == {}


def branching_on_live_state(host: Host) -> Generator[OperationRequest]:
    """An illegal deploy: the second request branches on state the first writes."""
    yield op(crontab.crontab, name="First", command="this_is_a_command", sudo=True)
    if "this_is_a_command" in host.crontab_text:
        yield op(crontab.crontab, name="Second", command="/usr/bin/ping", sudo=True)


async def test_branching_on_live_state_raises_determinism_error():
    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], branching_on_live_state)
    assert [p.request.name for p in plans["h"]] == ["First"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- Exports -------------------------------------------------------------------


def test_crontab_exports():
    import inspect

    import reemote

    # The fact and descriptors are exported from the namespace and the top level.
    for symbol in (
        Crontab,
        CrontabEntries,
        CrontabUploadCommand,
        CrontabInstallCommand,
    ):
        assert getattr(crontab, symbol.__name__) is symbol
        assert getattr(reemote, symbol.__name__) is symbol
    # The entry shape rides along on the namespace (its docs home), like the
    # other modules' entry TypedDicts.
    assert crontab.CrontabEntry is CrontabEntry
    # The operation is reached through the namespace; the ``crontab`` name in
    # the top-level ``__all__`` is the namespace package, not the operation.
    assert inspect.isasyncgenfunction(crontab.crontab)
    assert "crontab" in crontab.__all__
    assert "crontab" in reemote.__all__
    assert reemote.crontab is crontab
