# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the issue #70 elevated re-probe of a *hidden* path.

A file inside a ``0770``/``02770`` service directory (``/etc/redis``, owned
``redis:redis``) is invisible to a login that is not in that group: POSIX gives a
directory its search permission through the execute bit, and without it no name
inside resolves at all. ``test -e``, ``cat`` and ``stat`` then fail with
``EACCES``, which the shell reports as a plain "no such file" -- so the
unprivileged tier of ``FileContents``, ``File``/``Directory``/``Link``/``Socket``
and ``FileExists`` answers "absent" for a path that is merely hidden, and an
operation gated on that answer replans its write on every pass (the redis role's
live churn).

The fix is a traversal gate in front of every absent answer: one extra
unprivileged compound walks the path's directories outward to the nearest
existing one, and if *that* one denies ``-x`` the absence was never observable, so
the same probe is re-run elevated -- only a negative elevated answer is trusted as
absence. These tests pin all of that offline:

* the gate command itself (nearest-first ancestor chain, the tilde rule, the
  bare-name no-op, and that it never rides the fact's own command, which stays
  byte-identical to the baseline's);
* the real-transport tiers, through a scripted host that answers each shell
  separately and records the elevated calls -- including the cost guarantees (a
  visible absent path never elevates, a present path never runs the gate at all);
* the ``SimulatedHost`` traversal seam (``login_can_traverse``/
  ``login_path_visible``), which models the denial from the seeded
  ``directory_stats`` mode so the fallback runs offline exactly as it does over
  SSH -- removing it would make these tests report the hidden file as absent.
"""

import shlex
from datetime import datetime

from reemote.hosts import Host
from reemote.modules.files.files import (
    _HIDDEN,
    _PATH_HIDDEN,
    Directory,
    File,
    FileContents,
    FileExists,
    Link,
    _hidden_parent_command,
)
from tests.simulated_host import SimulatedHost

#: The issue's own path: the redis config inside its ``02770`` directory.
CONF_DIR = "/etc/redis"
CONF_PATH = "/etc/redis/redis.conf"

#: The login user of the scripted/simulated hosts -- neither the owner nor a
#: member of ``redis:redis``, so it is exactly the party the issue describes.
LOGIN = "supervisor"

EPOCH = datetime(1970, 1, 1)

#: The content a hidden file holds, as the two tiers would report it.
SECRET = "requirepass s3cret\n"

#: A GNU ``stat -c`` row for :data:`CONF_PATH`, the shape ``File.parse`` matches.
CONF_STAT_ROW = (
    "user=redis group=redis mode=-rw-r----- atime=1594804767 "
    "mtime=1594804767 ctime=0 size=18 '/etc/redis/redis.conf'\n"
)

CONF_STAT = {
    "user": "redis",
    "group": "redis",
    "mode": 640,
    "atime": datetime(2020, 7, 15, 9, 19, 27),
    "mtime": datetime(2020, 7, 15, 9, 19, 27),
    "ctime": EPOCH,
    "size": 18,
}

#: The setgid ``02770`` row ``redis:redis`` -- the mode that hides the config.
CONF_DIR_STAT = {"user": "redis", "group": "redis", "mode": 2770}


class ScriptedShellHost(Host):
    """Answers each unprivileged shell from a script, recording both tiers.

    ``CannedShellHost`` returns one stdout for every command, which cannot
    express "the probe said absent *and* the gate said hidden": the gate is a
    second, distinct shell. Here the answer is looked up by the exact shell
    string, an unscripted shell fails the test rather than silently answering
    empty, and :meth:`run_shell_privileged` records the elevated calls so a test
    can pin both that the fallback fired and that it did not.
    """

    def __init__(self, answers: dict[str, str], privileged_stdout: str = ""):
        super().__init__(name="scripted")
        self._answers = answers
        self._privileged_stdout = privileged_stdout
        self.shells: list[str] = []
        self.privileged_calls: list[tuple[str, bool]] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        try:
            return self._answers[shell]
        except KeyError:  # pragma: no cover - a test-shape failure
            raise AssertionError(f"unscripted shell: {shell!r}") from None

    async def run_shell_privileged(
        self, shell: str, *, sudo: bool = False, su: bool = False, doas: bool = False
    ) -> str:
        self.privileged_calls.append((shell, sudo))
        return self._privileged_stdout


def scripted(
    *, probe_stdout: str, gate_hidden: bool, privileged_stdout: str = ""
) -> ScriptedShellHost:
    """A host whose fact read answers the probe, then the traversal gate.

    ``probe_stdout`` is what the fact's own command prints (``""`` for the
    issue's case: the guard short-circuits, so a hidden path prints nothing at
    all), ``gate_hidden`` whether the nearest existing directory refuses the
    login user traversal.
    """
    return ScriptedShellHost(
        {
            FileContents.command(CONF_PATH): probe_stdout,
            File.command(CONF_PATH): probe_stdout,
            FileExists.command(CONF_PATH): probe_stdout,
            _hidden_parent_command(CONF_PATH): (
                f"{_PATH_HIDDEN}\n" if gate_hidden else ""
            ),
        },
        privileged_stdout=privileged_stdout,
    )


# --- the traversal gate command ----------------------------------------------


def test_gate_command_walks_the_ancestors_nearest_first():
    """The chain asks the nearest directory first and stops at the first that
    exists: a traversable one makes everything below nameable, so a deeper miss
    is genuine absence."""
    command = _hidden_parent_command(CONF_PATH)
    assert command.startswith(f"if [ -e {CONF_DIR} ]; then")
    assert command.index(CONF_DIR) < command.index(" /etc ") < command.index(" / ")
    assert command.endswith("fi")
    # Every branch asks for the search bit and prints the marker without it.
    assert command.count("[ -x ") == command.count("[ -e ") == 3
    assert command.count(f'|| printf "{_PATH_HIDDEN}\\n";') == 3


def test_gate_command_needs_no_wrapper_and_exits_zero():
    """The gate is never elevated -- it asks what the *login* user can see -- so
    it needs no ``sh -c`` wrapper, and each branch ends in a successful
    ``[ -x ]``/``printf``, so a refusal still exits zero (``run_shell_privileged``
    would raise on the elevated tier, and an unprivileged non-zero would be
    reported as a failing command)."""
    command = _hidden_parent_command(CONF_PATH)
    assert not command.startswith("sh -c")
    assert "sudo" not in command


def test_gate_command_leaves_a_leading_tilde_unquoted():
    """The ``File`` family's tilde rule reaches the ancestors too: ``~`` and a
    ``~/`` prefix stay bare so the remote shell expands them, while a later
    segment is quoted."""
    command = _hidden_parent_command("~/My Documents/token")
    assert "[ -e '~" not in command
    assert "[ -e ~/'My Documents' ]" in command
    assert "[ -x ~/'My Documents' ]" in command
    assert "[ -e ~ ]" in command


def test_gate_command_of_a_bare_name_asks_nothing():
    """A relative name with no directory above it has no ancestor to blame, so
    the gate cannot judge it and answers "not hidden" without a probe."""
    assert _hidden_parent_command("Makefile") == "true"


def test_gate_marker_never_rides_the_facts_own_command():
    """The gate is a separate shell: the baseline's own commands stay verbatim,
    which is what keeps the pinned command parity intact."""
    for command in (
        File.command(CONF_PATH),
        FileContents.command(CONF_PATH),
        FileExists.command(CONF_PATH),
    ):
        assert _PATH_HIDDEN not in command


def test_file_contents_command_exits_zero():
    """Both tiers end in ``|| true``: the elevated probe of a genuinely missing
    file must report absence, not raise ``CommandError`` on the failing guard
    (the hash family's idiom)."""
    assert FileContents.command(CONF_PATH).endswith("|| true'")


# --- FileContents over the real transport ------------------------------------


async def test_file_contents_reprobes_a_hidden_path_elevated():
    """The issue's case: the unprivileged ``test -e`` is refused and prints
    nothing, the gate says the parent hides the path, so the read is re-run
    elevated and the file's real content comes back."""
    host = scripted(
        probe_stdout="",
        gate_hidden=True,
        privileged_stdout=f"{FileContents.MARKER}\n{SECRET}",
    )
    assert await FileContents.process(host, CONF_PATH) == SECRET
    assert host.privileged_calls == [(FileContents.command(CONF_PATH), True)]


async def test_file_contents_of_a_visible_absent_path_never_elevates():
    """A traversable nearest ancestor settles the question: the absence is
    genuine, and the ordinary first run of every ``files.install`` costs no
    ``sudo`` round-trip."""
    host = scripted(probe_stdout="", gate_hidden=False)
    assert await FileContents.process(host, CONF_PATH) is None
    assert host.privileged_calls == []
    assert host.shells == [
        FileContents.command(CONF_PATH),
        _hidden_parent_command(CONF_PATH),
    ]


async def test_file_contents_trusts_only_a_negative_elevated_answer():
    """Root says absent too -- the file really is missing -- and that is the
    answer the fact returns, after exactly one elevation."""
    host = scripted(probe_stdout="", gate_hidden=True, privileged_stdout="")
    assert await FileContents.process(host, CONF_PATH) is None
    assert len(host.privileged_calls) == 1


async def test_file_contents_of_a_present_path_never_runs_the_gate():
    """The gate fronts absence only: a file the login can read costs one shell,
    exactly as before the fix."""
    host = scripted(probe_stdout=f"{FileContents.MARKER}\n{SECRET}", gate_hidden=False)
    assert await FileContents.process(host, CONF_PATH) == SECRET
    assert host.shells == [FileContents.command(CONF_PATH)]
    assert host.privileged_calls == []


async def test_file_contents_never_leaks_the_unreadable_sentinel():
    """A run-as user who is not root can be refused the elevated read too. The
    marker is detected on whichever tier printed it, and the fact's contract
    stays content-or-None -- the sentinel never reaches a caller."""
    host = ScriptedShellHost(
        {
            FileContents.command(CONF_PATH): f"{FileContents.MARKER}\n"
            f"{FileContents.UNREADABLE}\n",
            _hidden_parent_command(CONF_PATH): "",
        },
        privileged_stdout=f"{FileContents.MARKER}\n{FileContents.UNREADABLE}\n",
    )
    assert await FileContents.process(host, CONF_PATH) is None
    assert host.privileged_calls == [(FileContents.command(CONF_PATH), True)]
    # A refusal on the present arm is answered by the elevated tier, not by the
    # gate -- the gate never runs once the marker proves the path exists.
    assert host.shells == [FileContents.command(CONF_PATH)]


# --- the existence facts over the real transport ------------------------------


async def test_file_reprobes_a_hidden_path_elevated():
    """``File`` gets the same treatment: the stat chain prints nothing behind an
    untraversable parent, so the row is re-read elevated -- wrapped in ``sh -c``,
    because ``sudo`` execs its argument and an unwrapped chain would elevate only
    the leading ``test``."""
    host = scripted(
        probe_stdout="", gate_hidden=True, privileged_stdout=CONF_STAT_ROW
    )
    assert await File.process(host, CONF_PATH) == CONF_STAT
    (shell, sudo), = host.privileged_calls
    assert sudo is True
    assert shell == f"sh -c {shlex.quote(File.command(CONF_PATH))}"


async def test_file_of_a_visible_absent_path_never_elevates():
    host = scripted(probe_stdout="", gate_hidden=False)
    assert await File.process(host, CONF_PATH) is None
    assert host.privileged_calls == []
    assert host.shells == [File.command(CONF_PATH), _hidden_parent_command(CONF_PATH)]


async def test_directory_and_link_inherit_the_reprobe():
    """The siblings share the two-tier read through ``File`` and differ only in
    which seam store they name, so the elevated tier wraps *their* command."""
    host = ScriptedShellHost(
        {
            Directory.command(CONF_DIR): "",
            _hidden_parent_command(CONF_DIR): f"{_PATH_HIDDEN}\n",
        },
        privileged_stdout="user=redis group=redis mode=drwxrws--- atime=0 "
        "mtime=0 ctime=0 size=4096 '/etc/redis'\n",
    )
    stats = await Directory.process(host, CONF_DIR)
    assert stats["mode"] == 2770
    assert host.privileged_calls == [
        (f"sh -c {shlex.quote(Directory.command(CONF_DIR))}", True)
    ]

    host = ScriptedShellHost(
        {
            Link.command("/etc/redis/current"): "",
            _hidden_parent_command("/etc/redis/current"): f"{_PATH_HIDDEN}\n",
        },
        privileged_stdout="user=redis group=redis mode=lrwxrwxrwx atime=0 "
        "mtime=0 ctime=0 size=8 '/etc/redis/current' -> 'redis.conf'\n",
    )
    info = await Link.process(host, "/etc/redis/current")
    assert info["link_target"] == "redis.conf"
    assert host.privileged_calls == [
        (f"sh -c {shlex.quote(Link.command('/etc/redis/current'))}", True)
    ]


async def test_file_exists_reprobes_a_hidden_path_elevated():
    """``FileExists`` is the cheapest of the three and the one every ``creates``
    gate reads: a hidden path is re-asked as root, whose traversal is
    unrestricted, so a gated create stops re-firing."""
    host = scripted(probe_stdout="", gate_hidden=True, privileged_stdout="yes\n")
    assert await FileExists.process(host, CONF_PATH) is True
    (shell, sudo), = host.privileged_calls
    assert sudo is True
    # The elevated probe masks its own exit status: ``run_shell_privileged``
    # turns a non-zero ``test -e`` into ``CommandError``.
    assert shell == f"sh -c {shlex.quote(f'{FileExists.command(CONF_PATH)} || true')}"


async def test_file_exists_of_a_visible_absent_path_never_elevates():
    host = scripted(probe_stdout="", gate_hidden=False)
    assert await FileExists.process(host, CONF_PATH) is False
    assert host.privileged_calls == []


# --- the SimulatedHost traversal seam ----------------------------------------


def hidden_host(**overrides) -> SimulatedHost:
    """A simulated host holding the redis config behind its ``02770`` directory.

    The content, the file row and the directory row are all seeded -- the state a
    converged first pass leaves -- while the login user is neither ``redis`` nor
    in the ``redis`` group, so its probe cannot reach the config at all.
    """
    host = SimulatedHost(name="h", connection={"username": LOGIN})
    host.remote_files[CONF_PATH] = SECRET.encode()
    host.existing_files.add(CONF_PATH)
    host.file_stats[CONF_PATH] = dict(CONF_STAT)
    host.directory_stats[CONF_DIR] = dict(CONF_DIR_STAT)
    host.directory_stats.setdefault(
        "/etc", {"user": "root", "group": "root", "mode": 755}
    )
    for key, value in overrides.items():
        setattr(host, key, value)
    return host


def test_login_path_visible_models_the_posix_search_bits():
    """Owner, group (primary plus supplementary) and other search bits in POSIX
    order; root bypasses the check; a path with no seeded ancestor directory is
    visible (the legacy content-only seams)."""
    host = SimulatedHost(name="h", connection={"username": LOGIN})
    assert host.login_path_visible(CONF_PATH)  # no directory rows: legacy seam

    host.directory_stats[CONF_DIR] = dict(CONF_DIR_STAT)
    assert not host.login_path_visible(CONF_PATH)  # other: 02770 has no other x

    host.os_users[LOGIN] = {"group": LOGIN, "groups": ["redis"]}
    assert host.login_path_visible(CONF_PATH)  # supplementary group membership

    host.os_users[LOGIN] = {"group": "redis", "groups": []}
    assert host.login_path_visible(CONF_PATH)  # primary group membership

    host.os_users = {}
    host.directory_stats[CONF_DIR] = {"user": LOGIN, "group": "root", "mode": 700}
    assert host.login_path_visible(CONF_PATH)  # the owner search bit

    host.directory_stats[CONF_DIR] = {"user": "redis", "group": "redis", "mode": 755}
    assert host.login_path_visible(CONF_PATH)  # the other search bit

    host.directory_stats[CONF_DIR] = {"user": "redis", "group": "redis", "mode": 770}
    assert not host.login_path_visible(CONF_PATH)  # group-only, kim is nobody

    root = SimulatedHost(name="r", connection={"username": "root"})
    root.directory_stats[CONF_DIR] = dict(CONF_DIR_STAT)
    assert root.login_path_visible(CONF_PATH)  # root bypasses the check
    assert root.login_can_traverse(CONF_DIR)


def test_login_can_traverse_reads_the_directory_store_first():
    """``login_can_traverse`` answers for a directory through
    ``directory_stats`` (the seam ``Directory`` reads and ``make_directory``
    writes), falling back to ``file_stats``; an unseeded directory allows."""
    host = SimulatedHost(name="h", connection={"username": LOGIN})
    assert host.login_can_traverse(CONF_DIR)

    host.directory_stats[CONF_DIR] = {"user": "redis", "group": "redis", "mode": 700}
    assert not host.login_can_traverse(CONF_DIR)

    host.file_stats["/srv/secrets"] = {"user": "root", "group": "root", "mode": 600}
    assert not host.login_can_traverse("/srv/secrets")  # the file store answers too


def test_login_path_visible_stops_at_the_nearest_seeded_directory():
    """The nearest existing ancestor decides, because reaching it needed only its
    own parent's search bit: an untraversable grandparent above a traversable
    parent does not hide the path from a login that can already reach the
    parent's own row."""
    host = SimulatedHost(name="h", connection={"username": LOGIN})
    host.directory_stats["/var/lib/redis"] = {
        "user": "redis",
        "group": "redis",
        "mode": 777,
    }
    host.directory_stats["/var/lib"] = {"user": "root", "group": "root", "mode": 755}
    assert host.login_path_visible("/var/lib/redis/dump.rdb")

    # With the nearest row refusing traversal, the outer rows never matter.
    host.directory_stats["/var/lib/redis"]["mode"] = 770
    assert not host.login_path_visible("/var/lib/redis/dump.rdb")


async def test_file_contents_seam_hides_content_behind_a_denied_directory():
    """The seam models the denial itself: a ``0644`` file (so the read bit is not
    what refuses) inside an untraversable directory returns the same absence the
    real probe prints, and ``process``' elevated tier reads the same store as
    root. Removing the fallback makes this the issue's bug, offline."""
    host = hidden_host()
    host.file_stats[CONF_PATH] = {"user": "redis", "group": "redis", "mode": 644}
    assert not host.login_path_visible(CONF_PATH)
    assert host.login_can_read(CONF_PATH)  # the mode is not what hides it
    assert await FileContents.process(host, CONF_PATH) == SECRET


async def test_file_contents_seam_returns_absent_when_root_agrees():
    """A path the store does not hold is absent for root too: the seam answers
    the elevated tier from the same state, so the fact reports None rather than
    inventing content."""
    host = hidden_host(remote_files={}, existing_files=set(), file_stats={})
    assert not host.login_path_visible(CONF_PATH)
    assert await FileContents.process(host, CONF_PATH) is None


async def test_file_fact_seam_hides_a_row_behind_a_denied_directory():
    """The stat facts consult the same model: the row is withheld from the
    unprivileged tier and comes back from the elevated one."""
    host = hidden_host()
    assert await File.process(host, CONF_PATH) == CONF_STAT


async def test_directory_fact_seam_hides_a_row_behind_a_denied_directory():
    host = hidden_host()
    host.directory_stats["/etc/private"] = dict(CONF_DIR_STAT)
    host.directory_stats["/etc/private/deeper"] = {
        "user": "root",
        "group": "root",
        "mode": 755,
    }
    assert not host.login_path_visible("/etc/private/deeper")
    assert await Directory.process(host, "/etc/private/deeper") == {
        "user": "root",
        "group": "root",
        "mode": 755,
    }


async def test_file_exists_seam_hides_a_marker_behind_a_denied_directory():
    """``FileExists`` is the ``creates`` gate's fact: a hidden path that exists
    must not read as missing, or every gated create re-fires on every pass."""
    host = hidden_host()
    assert await FileExists.process(host, CONF_PATH) is True


async def test_the_unprivileged_seam_tier_reports_hidden_rather_than_answer():
    """The seam model itself, tier by tier: behind an untraversable directory the
    first tier must refuse to answer -- the simulation knows the whole store
    while the real probe could not have reached this far -- and only the
    elevated tier, reading the same state as root, returns it. Without this the
    offline suite would hand back the right value for the wrong reason and the
    fallback would never run offline (the blind spot issue #70 reports)."""
    host = hidden_host()
    assert await File._read(host, CONF_PATH, privileged=False) is _HIDDEN
    assert await File._read(host, CONF_PATH, privileged=True) == CONF_STAT
    assert await FileExists._read(host, CONF_PATH, privileged=False) is _HIDDEN
    assert await FileExists._read(host, CONF_PATH, privileged=True) is True
    # ``FileContents`` has no hidden sentinel: its absence answer *is* None, and
    # ``process``' own gate check is what turns that into the elevation.
    assert await FileContents._read(host, CONF_PATH, privileged=False) is None
    assert await FileContents._read(host, CONF_PATH, privileged=True) == SECRET


async def test_the_unprivileged_seam_tier_answers_a_reachable_path():
    """The same tier, with the directory's search bit (and the file's own read
    bit) granted: the answer is immediate, with no sentinel and no elevation --
    invisibility is the only thing that pushes the first tier off."""
    host = hidden_host()
    readable = {"user": "redis", "group": "redis", "mode": 644}
    host.directory_stats[CONF_DIR] = {"user": "redis", "group": "redis", "mode": 755}
    host.file_stats[CONF_PATH] = dict(readable)
    assert await File._read(host, CONF_PATH, privileged=False) == readable
    assert await FileExists._read(host, CONF_PATH, privileged=False) is True
    assert await FileContents._read(host, CONF_PATH, privileged=False) == SECRET


async def test_seam_never_asks_the_simulated_shell():
    """Seam-first: a hidden path is settled by the model and the in-memory
    stores, so no fact read reaches the simulated transport at all -- the
    re-probe is invisible to ``ran_shells``."""
    host = hidden_host()
    await FileContents.process(host, CONF_PATH)
    await File.process(host, CONF_PATH)
    await FileExists.process(host, CONF_PATH)
    assert host.ran_shells == []


async def test_a_reachable_path_costs_no_extra_seam_work():
    """The other half of the cost guarantee offline: with a traversable parent
    and a readable file, one fact read answers the whole question."""
    host = hidden_host()
    world = {"user": "redis", "group": "redis", "mode": 755}
    readable = {"user": "redis", "group": "redis", "mode": 644}
    host.directory_stats[CONF_DIR] = dict(world)
    host.file_stats[CONF_PATH] = dict(readable)
    assert host.login_path_visible(CONF_PATH)
    assert await FileContents.process(host, CONF_PATH) == SECRET
    assert await File.process(host, CONF_PATH) == readable
