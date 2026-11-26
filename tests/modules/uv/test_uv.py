# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the uv module: the ``Packages`` fact.

Covers ``reemote.modules.uv.uv`` -- ``Packages`` (the port of pyinfra's
``uv.UvPipPackages``) -- over the real-transport path (a
``CannedShellHost`` returning ``uv pip list --format json`` output, reusing
pyinfra's own fact fixtures verbatim: the empty array, the one-package and
three-package JSON lines) and through the ``SimulatedHost`` ``uv_packages``
seam, plus the total-parse rule (unparsable output, non-list documents and
rows without string ``name``/``version`` values contribute nothing), the
last-seen collapse of a repeated name, the sort guarantee, the verbatim
name spelling, the ``gather_fact`` fan-out and the exports.

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

from reemote.drivers import gather_fact
from reemote.hosts import Host
from reemote.modules import uv
from reemote.modules.uv.uv import PackageInfo, Packages
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell``, recording the shell it was
    given so the command parity checks can assert on it."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


def _entry(name: str, version: str) -> PackageInfo:
    """One ``PackageInfo`` entry, spelled as the fact returns it."""
    return {"name": name, "version": version}


# --- Fact: command parity ------------------------------------------------------


def test_uv_packages_command():
    """The shell is pyinfra's command verbatim; there is no parameter to
    vary it."""
    assert Packages.COMMAND == "uv pip list --format json"


# --- Fact: real-transport parsing (pyinfra's fixtures verbatim) ------------------

# pyinfra's ``tests/facts/uv.UvPipPackages/one_package.json`` output, in
# pyinfra's documented dict-of-sets shape ``{"pydash": ["3.48"]}`` -- here
# projected into the reemote entry shape.
ONE_PACKAGE_OUTPUT = '[{"name": "pydash", "version": "3.48"}]'

# pyinfra's ``three_packages.json`` output: one compact JSON line naming
# three packages out of alphabetical order.
THREE_PACKAGES_OUTPUT = (
    '[{"name": "pydash", "version": "3.48"}, '
    '{"name": "pluggy", "version": "1.60"}, '
    '{"name": "pip", "version": "25.3"}]'
)


async def test_uv_packages_parses_one_package():
    """The single-package fixture line becomes one entry, and the gathered
    shell is the fact's own command."""
    host = CannedShellHost(ONE_PACKAGE_OUTPUT)
    assert await Packages.process(host) == [_entry("pydash", "3.48")]
    assert host.last_shell == "uv pip list --format json"


async def test_uv_packages_parses_three_packages_sorted():
    """The three-package fixture line comes back as sorted ``PackageInfo``
    entries however uv printed them -- pyinfra's ``itemgetter`` sort, in
    list form."""
    assert await Packages.process(CannedShellHost(THREE_PACKAGES_OUTPUT)) == [
        _entry("pip", "25.3"),
        _entry("pluggy", "1.60"),
        _entry("pydash", "3.48"),
    ]


async def test_uv_packages_empty_array_reads_as_no_packages():
    """pyinfra's ``no_packages`` fixture: the bare ``[]`` document -- an
    environment with nothing installed -- reads as ``[]``, the counterpart
    of pyinfra's ``default = dict``."""
    assert await Packages.process(CannedShellHost("[]")) == []
    assert await Packages.process(CannedShellHost("")) == []


async def test_uv_packages_keeps_names_verbatim():
    """Unlike the pip fact there is no PEP 503 normalization: uv prints the
    canonical name of each distribution, so the dotted and capitalized
    spellings survive exactly as printed."""
    output = (
        '[{"name": "zope.interface", "version": "7.2"}, '
        '{"name": "PyYAML", "version": "6.0.2"}]'
    )
    assert await Packages.process(CannedShellHost(output)) == [
        _entry("PyYAML", "6.0.2"),
        _entry("zope.interface", "7.2"),
    ]


async def test_uv_packages_ignores_leading_noise():
    """The document is read from the first ``[`` onwards, so stray stdout
    noise ahead of the JSON array (which uv itself does not normally
    produce, but a wrapper shell might) is discarded."""
    output = 'Using Python 3.12 environment\n' + ONE_PACKAGE_OUTPUT
    assert Packages.parse(output.splitlines()) == [_entry("pydash", "3.48")]


# --- Fact: the total parse --------------------------------------------------------


async def test_uv_packages_unparsable_output_reads_as_empty():
    """Where pyinfra's ``process_json`` warns on a failed load, reemote
    reads ``[]``: broken JSON, a non-list document and a document with no
    array start all contribute nothing."""
    assert Packages.parse(['[{"name": "cut']) == []
    assert Packages.parse(['{"name": "pydash", "version": "3.48"}']) == []
    assert Packages.parse(["no json here"]) == []


async def test_uv_packages_skips_rows_without_string_fields():
    """A non-mapping row, a row missing ``name`` or ``version``, and a row
    whose fields are not strings (uv always prints strings, but the parse
    does not crash on anything else) all contribute nothing; valid rows
    alongside them still land."""
    output = (
        '["a string", {"name": "noversion"}, {"version": "noname"}, '
        '{"name": "numver", "version": 3}, '
        '{"name": "pluggy", "version": "1.60"}]'
    )
    assert Packages.parse([output]) == [_entry("pluggy", "1.60")]


async def test_uv_packages_collapses_a_repeated_name():
    """A repeated name collapses into the entry seen last, keeping the
    result unique per name -- the set semantics of pyinfra's dict values,
    narrowed to one version per name exactly as the fact returns it."""
    output = (
        '[{"name": "pydash", "version": "3.47"}, '
        '{"name": "pydash", "version": "3.48"}]'
    )
    assert Packages.parse([output]) == [_entry("pydash", "3.48")]


# --- Fact: SimulatedHost seam -----------------------------------------------------


async def test_uv_packages_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``uv_packages`` store, bypassing the
    shell; the default empty store reads as ``[]``, like the baseline's
    ``default = dict`` for a bare environment."""
    host = SimulatedHost(name="h", uv_packages={"pydash": "3.48"})
    assert await Packages.process(host) == [_entry("pydash", "3.48")]
    assert await Packages.process(SimulatedHost(name="h")) == []


async def test_uv_packages_seam_sorts_and_returns_copies():
    """Seam entries come out sorted by name, and mutating a returned entry
    never touches the simulated state."""
    host = SimulatedHost(name="h", uv_packages={"b": "2", "a": "1"})
    entries = await Packages.process(host)
    assert entries == [_entry("a", "1"), _entry("b", "2")]
    entries[0]["version"] = "tampered"
    assert await Packages.process(host) == [_entry("a", "1"), _entry("b", "2")]


async def test_gather_fact_uv_packages_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(name="web1", uv_packages={"pydash": "3.48"})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], Packages)
    assert answers == {
        "web1": [_entry("pydash", "3.48")],
        "web2": [],
    }


# --- Exports -----------------------------------------------------------------------


def test_uv_module_exports():
    """The package re-exports the fact and its entry type, so
    ``uv.Packages`` and ``uv.PackageInfo`` resolve; the top-level package
    makes ``from reemote import Packages`` work and keeps the ``uv``
    namespace importable."""
    assert uv.Packages is Packages
    assert uv.PackageInfo is PackageInfo
    assert uv.uv.Packages is Packages
    import reemote

    assert reemote.uv is uv
    for name in ("PackageInfo", "Packages", "uv"):
        assert name in reemote.__all__
        assert getattr(reemote, name) is not None
