# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.ini_option``: the section-scoped INI option edit.

``files.ini_option`` is Ansible ``ini_file`` parity in the spool-and-install
family: it reads the target's text with the ``FileContents`` fact, applies the
pure ``_ini_set_option`` helper (rewrite one active option line inside one
section, insert a missing option, append a missing section, create an absent
file), and -- only when the result differs -- delegates the write to
``files.install``. Every other section, option, comment and blank line is
preserved, so the RPM-shipped multi-section ``epel.repo`` keeps its
``[epel-debuginfo]``/``[epel-source]`` sections while ``[epel] enabled`` flips.

Covers the helper's preservation/fixpoint laws (the issue's acceptance list),
the operation's delegate-to-install behaviour (create, rewrite, no-op,
``mode``/``user``/``group``/``validate`` forwarding, ``**kwargs`` absorption),
the fact read over the real transport, and end-to-end ``run``/plan/log
behaviour including the second-request no-op and the two-privilege-level log.
"""

import shlex
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import command_shell, format_log, format_plan, run
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import (
    FileContents,
    InstallCommand,
    InstallSpoolCommand,
    InstallValidateCommand,
    _ini_set_option,
    _install_spool_path,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "family": "Ubuntu",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

DEST = "/etc/yum.repos.d/epel.repo"
SPOOL = _install_spool_path(DEST)
SPOOL_DIR = SPOOL.rsplit("/", 1)[0]

# The RPM-shipped three-section repo file the issue's absence proof cites:
# every section carries its own ``enabled=0``, so a line-scoped edit would
# corrupt the debuginfo/source sections' intent.
EPEL_REPO = """[epel]
name=Extra Packages for Enterprise Linux
metalink=https://mirrors.example/metalink?repo=epel
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL
countme=1

[epel-debuginfo]
name=Extra Packages for Enterprise Linux - Debug
metalink=https://mirrors.example/metalink?repo=epel-debug
enabled=0
gpgcheck=1

[epel-source]
name=Extra Packages for Enterprise Linux - Source
metalink=https://mirrors.example/metalink?repo=epel-source
enabled=0
gpgcheck=1
"""

EPEL_ENABLED = EPEL_REPO.replace("enabled=0\ngpgcheck=1\ngpgkey", "enabled=1\ngpgcheck=1\ngpgkey", 1)


def set_enabled(current: str | None) -> str:
    """The helper bound to the issue's exact task: ``[epel] enabled=1``."""
    return _ini_set_option(current, "epel", "enabled", "1", True, True)


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout


# --- The pure helper: preservation and fixpoint laws --------------------------


def test_helper_rewrites_only_the_one_line_in_the_one_section():
    """The issue's core acceptance: setting ``[epel] enabled`` changes only
    that line; the other sections' same-named options are untouched."""
    out = set_enabled(EPEL_REPO)
    assert out == EPEL_ENABLED
    assert out.count("enabled=0") == 2  # debuginfo + source keep their lines
    assert out.count("enabled=1") == 1


def test_helper_preserves_comments_blank_lines_and_order():
    """Comments, blank lines and option order survive verbatim."""
    current = (
        "# managed by the RPM\n"
        "[epel]\n"
        "name=Extra Packages\n"
        "; a comment inside the section\n"
        "enabled=0\n"
        "\n"
        "gpgcheck=1\n"
        "\n"
        "[epel-source]\n"
        "enabled=0\n"
    )
    out = set_enabled(current)
    assert out == current.replace("enabled=0\n\ngpgcheck", "enabled=1\n\ngpgcheck", 1)


def test_helper_is_a_fixpoint():
    """A second pass over the output returns it verbatim (``changed=False``
    on the second request)."""
    once = set_enabled(EPEL_REPO)
    assert set_enabled(once) == once


def test_helper_inserts_a_missing_option_before_the_section_gap():
    """A missing option lands after the section's last non-blank line; the
    blank line separating the next section stays in place."""
    current = "[epel]\nname=Extra Packages\n\n[epel-source]\nenabled=0\n"
    out = set_enabled(current)
    assert out == "[epel]\nname=Extra Packages\nenabled=1\n\n[epel-source]\nenabled=0\n"
    assert set_enabled(out) == out


def test_helper_appends_a_missing_section():
    """A missing section is appended blank-line-separated with the option as
    its first line; an already-blank tail gains no second separator."""
    out = set_enabled("[other]\ny=2\n")
    assert out == "[other]\ny=2\n\n[epel]\nenabled=1\n"
    assert set_enabled(out) == out
    assert set_enabled("[other]\ny=2\n\n") == "[other]\ny=2\n\n[epel]\nenabled=1\n"


def test_helper_creates_an_absent_or_empty_file():
    """``None`` (absent) and ``""`` (empty) both produce a fresh one-section
    file -- Ansible's ``create: yes`` default."""
    assert set_enabled(None) == "[epel]\nenabled=1\n"
    assert set_enabled("") == "[epel]\nenabled=1\n"


def test_helper_create_false_raises_on_an_absent_file():
    """``create=False`` mirrors ``ini_file``'s ``create: no``: an absent file
    is an error, not a creation."""
    with pytest.raises(ValueError, match="absent and create=False"):
        _ini_set_option(None, "epel", "enabled", "1", True, False)
    # An existing file is edited normally under create=False.
    assert _ini_set_option("[epel]\nenabled=0\n", "epel", "enabled", "1", True, False) == (
        "[epel]\nenabled=1\n"
    )


def test_helper_no_extra_spaces_controls_the_separator_style():
    """``no_extra_spaces=False`` renders ``option = value`` (Ansible's
    default); True renders ``option=value``. Both are fixpoints."""
    spaced = _ini_set_option("[epel]\nenabled=0\n", "epel", "enabled", "1", False, True)
    assert spaced == "[epel]\nenabled = 1\n"
    assert _ini_set_option(spaced, "epel", "enabled", "1", False, True) == spaced
    tight = _ini_set_option(spaced, "epel", "enabled", "1", True, True)
    assert tight == "[epel]\nenabled=1\n"


def test_helper_recognizes_colon_separators_and_skips_comments():
    """configparser's ``:`` separator is an active option line; a commented
    ``# enabled=...`` is not (the insert path adds the real option)."""
    assert set_enabled("[epel]\nenabled: 0\n") == "[epel]\nenabled=1\n"
    out = set_enabled("[epel]\n# enabled=0\nname=x\n")
    assert out == "[epel]\n# enabled=0\nname=x\nenabled=1\n"


def test_helper_preserves_a_missing_trailing_newline():
    """A file without a final newline keeps that shape when only a middle
    line is rewritten."""
    assert set_enabled("[epel]\nenabled=0") == "[epel]\nenabled=1"


def test_helper_edits_the_first_of_duplicate_options():
    """A section duplicating an option edits the first active occurrence;
    the rewrite is still a fixpoint."""
    current = "[epel]\nenabled=0\nenabled=0\n"
    out = set_enabled(current)
    assert out == "[epel]\nenabled=1\nenabled=0\n"
    assert set_enabled(out) == out


def test_helper_matches_section_headers_with_inline_comments():
    """``[epel] ; comment`` is the epel section header."""
    assert set_enabled("[epel] ; the main section\nenabled=0\n") == (
        "[epel] ; the main section\nenabled=1\n"
    )


@pytest.mark.parametrize(
    ("section", "option", "value"),
    [
        ("", "enabled", "1"),
        ("ep[el", "enabled", "1"),
        ("epel", "", "1"),
        ("epel", "en=abled", "1"),
        ("epel", "enabled", "1\n2"),
    ],
)
def test_helper_rejects_malformed_names_and_values(section, option, value):
    with pytest.raises(ValueError, match="Invalid INI"):
        _ini_set_option(None, section, option, value, True, True)


# --- The operation: edit, then delegate to install ----------------------------


async def test_ini_option_rewrites_when_the_option_differs():
    """A drifted option spools the section-scoped edit and installs it."""
    host = SimulatedHost(name="h", remote_files={DEST: EPEL_REPO.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", no_extra_spaces=True
        )
    ]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} differs, will install"
    spool_cmd, install_cmd = commands
    assert isinstance(spool_cmd, InstallSpoolCommand)
    assert spool_cmd.content == EPEL_ENABLED
    assert spool_cmd.dest == SPOOL
    assert isinstance(install_cmd, InstallCommand)
    assert install_cmd.spool == SPOOL
    assert install_cmd.dest == DEST
    assert install_cmd.mode == 644


async def test_ini_option_is_a_no_op_when_already_converged():
    """The issue's acceptance: a second identical request yields nothing."""
    host = SimulatedHost(name="h", remote_files={DEST: EPEL_ENABLED.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", no_extra_spaces=True
        )
    ]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({DEST})"
    assert commands == []


async def test_ini_option_creates_an_absent_file():
    """An absent file is created holding just the section and option."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", no_extra_spaces=True
        )
    ]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} is absent, will install"
    spool_cmd, _ = commands
    assert spool_cmd.content == "[epel]\nenabled=1\n"


async def test_ini_option_create_false_surfaces_the_helper_error():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="absent and create=False"):
        [
            cmd
            async for cmd in files.ini_option(
                ctx, DEST, "epel", "enabled", "1", create=False
            )
        ]


async def test_ini_option_forwards_mode_user_and_group_to_install():
    """The ownership/mode params reach the elevated install descriptor."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", mode="0640", user="www", group="www"
        )
    ]
    _, install_cmd = commands
    assert install_cmd.mode == 640
    assert install_cmd.user == "www"
    assert install_cmd.group == "www"
    assert f"chown www:www {DEST}" in install_cmd.shell()
    assert "chmod 640 " in install_cmd.shell()


@pytest.mark.parametrize("mode", ["u+x", "888", "", "0o644"])
async def test_ini_option_surfaces_install_mode_validation(mode):
    """A non-octal-digit mode raises install's ValueError through ini_option."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Invalid file mode"):
        [
            cmd
            async for cmd in files.ini_option(
                ctx, DEST, "epel", "enabled", "1", mode=mode
            )
        ]


async def test_ini_option_forwards_validate_to_install():
    """The ``validate`` param reaches install's atomic validate-then-install
    hook between the spool transfer and the elevated install."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", validate="test -s {spool}"
        )
    ]
    assert [type(c) for c in commands] == [
        InstallSpoolCommand,
        InstallValidateCommand,
        InstallCommand,
    ]
    assert commands[1].validate == "test -s {spool}"
    assert commands[1].spool == SPOOL
    assert commands[1].dest == DEST


async def test_ini_option_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", some_global_arg=True
        )
    ]
    assert len(commands) == 2


# --- The fact read over the real transport ------------------------------------


async def test_ini_option_reads_current_content_over_the_real_transport():
    """The edit input comes from FileContents' marker/cat form when no
    simulated seam is present."""
    # Converged: the option already holds the wanted value.
    host = CannedShellHost(f"{FileContents.MARKER}\n[epel]\nenabled=1\n")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", no_extra_spaces=True
        )
    ]
    assert not ctx.changed
    assert commands == []

    # Not converged: the option flips inside its section only.
    host = CannedShellHost(f"{FileContents.MARKER}\n{EPEL_REPO}")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.ini_option(
            ctx, DEST, "epel", "enabled", "1", no_extra_spaces=True
        )
    ]
    assert ctx.changed
    spool_cmd, _ = commands
    assert spool_cmd.content == EPEL_ENABLED


# --- Descriptor elevation asymmetry (inherited from install) ------------------


def test_spool_transfer_is_never_elevated_but_install_is():
    """The two-privilege-level signature: a sudo-flagged request leaves the scp
    transfer unprefixed and elevates only the install shell."""
    spool_cmd = InstallSpoolCommand(content=EPEL_ENABLED, dest=SPOOL)
    assert command_shell(spool_cmd, sudo=True) == str(spool_cmd)

    install_cmd = InstallCommand(spool=SPOOL, dest=DEST, mode=644)
    inner = (
        f"mkdir -p /etc/yum.repos.d && cp {SPOOL} {DEST} && "
        f"chmod 644 {DEST} && rm -rf {SPOOL_DIR}"
    )
    assert install_cmd.shell() == f"sh -c {shlex.quote(inner)}"
    assert command_shell(install_cmd, sudo=True) == f"sudo sh -c {shlex.quote(inner)}"


# --- End to end through the driver ---------------------------------------------


def ini_deploy():
    """The issue's repo-epel task twice: the second reads back what the first
    installed and is a guaranteed no-op."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.ini_option,
            name="Ensure EPEL repo is enabled",
            path=DEST,
            section="epel",
            option="enabled",
            value="1",
            no_extra_spaces=True,
            mode="0644",
            sudo=True,
        )
        yield op(
            files.ini_option,
            name="Re-ensure EPEL repo is enabled",
            path=DEST,
            section="epel",
            option="enabled",
            value="1",
            no_extra_spaces=True,
            mode="0644",
            sudo=True,
        )

    return deploy


async def test_ini_option_end_to_end_plan_log_and_second_no_op():
    """The prepare plan materializes both requests against pre-deployment
    state (both changed); only the apply log shows the second reading back
    what the first installed. The other sections survive the write, and the
    log carries the unprivileged scp line and the sudo sh -c install."""
    host = SimulatedHost(name="web1", remote_files={DEST: EPEL_REPO.encode()})
    plans, report, hosts = await run(
        INVENTORY, ini_deploy(), host_factory=lambda entry: host
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the option was 0 for both
    assert f"content {DEST} differs, will install" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Ensure EPEL repo is enabled"].changed
    assert not entries["Re-ensure EPEL repo is enabled"].changed
    assert entries["Re-ensure EPEL repo is enabled"].reason == (
        f"content already up to date ({DEST})"
    )

    # Only the [epel] enabled line changed; the spool was cleaned up.
    assert hosts[0].remote_files == {DEST: EPEL_ENABLED.encode()}

    log = format_log(report)
    assert f"$ scp <files install> -> {SPOOL}" in log
    assert "$ sudo sh -c 'mkdir -p /etc/yum.repos.d && cp" in log


async def test_ini_option_dry_run_sends_nothing():
    host = SimulatedHost(name="web1", remote_files={DEST: EPEL_REPO.encode()})
    plans, report, hosts = await run(
        INVENTORY, ini_deploy(), host_factory=lambda entry: host, dry=True
    )
    assert report is None
    assert plans["web1"][0].changed
    assert hosts[0].remote_files == {DEST: EPEL_REPO.encode()}


async def test_ini_option_mode_converges_on_the_write():
    """The issue's ``mode: "0644"`` half: the install applies the requested
    mode on the write (metadata-only drift stays ``files.file``'s job)."""
    host = SimulatedHost(name="web1", remote_files={DEST: EPEL_REPO.encode()})
    _plans, report, hosts = await run(
        INVENTORY, ini_deploy(), host_factory=lambda entry: host
    )
    assert report is not None
    assert hosts[0].file_stats[DEST]["mode"] == 644
    assert "chmod 644 " in format_log(report)