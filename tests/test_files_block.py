# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the ``files.block`` migration: a managed block as a files.edit transform.

the baseline's ``files.block`` is not ported as a bespoke operation -- the developer
chose the transform route: the ``block_transform`` factory in
``examples/modules/files/files_block.py`` builds a pure, idempotent
``files.edit`` transform with the baseline's option set (content, present,
line/before/after positioning, escape_regex_characters, marker/begin/end),
and the generic primitive supplies the fact read, the content-equality
idempotence and the ``PutCommand`` write. These tests import that factory
straight from the example module, so the shipped demonstration code is what
is verified.

Covers the factory's controller-side validation (the baseline's
``OperationValueError`` messages as ``ValueError``), every transform branch
against the baseline's own operation-test assertions (append/prepend, before/after
a regex line, literal escaping, replace-in-place, fixpoint no-op, removal),
the documented None pass-through for removal of an absent file, the
``files.edit`` materialization (changed/reason/``PutCommand``), end-to-end
plan/apply behaviour through ``SimulatedHost`` including the second-edit
no-op, and the ``DeterminismError`` a factory call inside the deploy
generator would trip.
"""

import importlib.util
from collections.abc import Generator
from pathlib import Path

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import (
    DeterminismError,
    format_log,
    format_plan,
    run,
)
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import PutCommand, read_source_bytes
from tests.simulated_host import SimulatedHost

# Import the example module by path: the factory under test lives in
# examples/ (demo content never enters src/), and the example is not a
# package on sys.path.
_example_path = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "modules"
    / "files"
    / "files_block.py"
)
_spec = importlib.util.spec_from_file_location("files_block_example", _example_path)
files_block = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(files_block)
block_transform = files_block.block_transform

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

REMOTE_PATH = "/tmp/reemote-block.txt"

BEGIN = "# BEGIN REEMOTE BLOCK"
END = "# END REEMOTE BLOCK"


def block_text(*lines: str) -> str:
    """The marked block exactly as the transform writes it (trailing newline)."""
    return "\n".join([BEGIN, *lines, END]) + "\n"


# Stable module-level transforms for the end-to-end deploys (the same callable
# objects in both phases -- the determinism guard compares them by value).
ENSURE = block_transform(content="please add this", line="before this", before=True)
REMOVE = block_transform(present=False)


# --- Factory validation (the baseline OperationValueError parity) ------------------


def test_present_requires_content():
    with pytest.raises(ValueError, match="'content' must be supplied"):
        block_transform()
    with pytest.raises(ValueError, match="'content' must be supplied"):
        block_transform(content="")


def test_line_requires_exactly_one_of_before_after():
    with pytest.raises(ValueError, match="only one of 'before' or 'after'"):
        block_transform(content="x", line="anchor")
    with pytest.raises(ValueError, match="only one of 'before' or 'after'"):
        block_transform(content="x", line="anchor", before=True, after=True)


def test_before_after_without_line_must_be_equal():
    with pytest.raises(ValueError, match="'line' must be supplied"):
        block_transform(content="x", before=True)


# --- The transform: present branches ------------------------------------------


def test_absent_file_creates_the_block():
    transform = block_transform(content="please add this")
    assert transform(None) == block_text("please add this")


def test_appends_to_existing_content_by_default():
    transform = block_transform(content="please add this")
    assert transform("seed line\n") == "seed line\n" + block_text("please add this")


def test_prepends_when_before_and_after_are_true():
    transform = block_transform(content="please add this", before=True, after=True)
    assert transform("seed line\n") == block_text("please add this") + "seed line\n"


def test_inserts_before_the_matching_line():
    """The baseline add_no_existing_block_line_provided parity: the block lands
    before the first line matching the whole-line-adjusted regex."""
    transform = block_transform(content="please add this", line="before this", before=True)
    assert transform("before this\nafter that\n") == (
        block_text("please add this") + "before this\nafter that\n"
    )


def test_inserts_after_the_matching_line():
    transform = block_transform(content="please add this", line="before this", after=True)
    assert transform("before this\nafter that\n") == (
        "before this\n" + block_text("please add this") + "after that\n"
    )


def test_appends_when_the_line_regex_does_not_match():
    """The baseline's awk END rule: no match means the block is appended."""
    transform = block_transform(content="please add this", line="absent anchor", before=True)
    assert transform("seed line\n") == "seed line\n" + block_text("please add this")


def test_escape_regex_characters_matches_the_line_literally():
    """The baseline add_no_existing_block_line_provided_escape_regex parity: the
    '*' in the line is escaped, so only the literal text matches."""
    transform = block_transform(
        content="please add this",
        line="before this *",
        before=True,
        escape_regex_characters=True,
    )
    # "before this XXX" would match the unescaped regex (this * = this followed
    # by anything); with escaping only the literal "before this *" line does.
    assert transform("before this XXX\nbefore this *\n") == (
        "before this XXX\n" + block_text("please add this") + "before this *\n"
    )


def test_replaces_an_existing_block_in_place():
    """The baseline add_existing_block_different_content parity: only the marked
    region changes, every other line is preserved."""
    transform = block_transform(content="should be this", line="before this", before=True)
    current = "head\n" + block_text("previously was something else") + "tail\n"
    assert transform(current) == "head\n" + block_text("should be this") + "tail\n"


def test_existing_block_with_same_content_is_a_fixpoint():
    """The baseline add_existing_block_same_content parity ('content already
    present'): the text is returned byte-identical, even without a trailing
    newline, so files.edit's content-equality check reports no change."""
    transform = block_transform(content="the one true string", line="before this", before=True)
    current = block_text("the one true string").rstrip("\n")
    assert transform(current) == current


def test_multiline_string_and_list_content_agree():
    from_str = block_transform(content="line one\nline two")
    from_list = block_transform(content=["line one", "line two"])
    assert from_str("seed\n") == from_list("seed\n") == (
        "seed\n" + block_text("line one", "line two")
    )


def test_custom_marker_begin_and_end():
    transform = block_transform(
        content="<p>Warning</p>",
        marker="<!-- {mark} BLOCK -->",
        begin="OPEN",
        end="CLOSE",
    )
    assert transform(None) == (
        "<!-- OPEN BLOCK -->\n<p>Warning</p>\n<!-- CLOSE BLOCK -->\n"
    )
    # A second custom-marker block is found and replaced, not duplicated.
    current = transform(None)
    assert transform(current) == current


def test_shell_metacharacters_are_written_literally():
    """The baseline's shell-metachar fixtures: content never reaches a shell in a
    pure-Python transform, so $(...), backticks and quotes stay verbatim."""
    content = 'eval $(thef -a) `id` "quoted" \\backslash'
    transform = block_transform(content=content)
    assert transform(None) == block_text(content)


# --- The transform: removal branches ------------------------------------------


def test_removes_the_marked_region_including_markers():
    """The baseline remove_existing_block parity: the awk range delete removes
    every line from the begin marker through the end marker."""
    current = "keep\n" + block_text("existing content") + "tail\n"
    assert REMOVE(current) == "keep\ntail\n"


def test_removal_without_markers_leaves_the_text_untouched():
    """The baseline remove_no_existing_block parity ('no remove required: markers
    not found'): the identical text means files.edit yields nothing."""
    assert REMOVE("no markers here\n") == "no markers here\n"


def test_removal_of_an_absent_file_passes_none_through():
    """The documented contract stretch: None -> None keeps files.edit's
    content-equality check a no-op, so no empty file is created (the baseline's
    'no remove required: file did not exist')."""
    assert REMOVE(None) is None


def test_removal_ignores_content_and_line():
    """The baseline warns and ignores content when removing; the factory accepts
    the combination and removes the marked region regardless."""
    transform = block_transform(content="ignored", present=False)
    current = "keep\n" + block_text("existing content")
    assert transform(current) == "keep\n"


def test_every_branch_is_a_fixpoint():
    """transform(transform(x)) == transform(x) -- the property files.edit's
    content-equality idempotence relies on, across every branch."""
    cases = [
        (block_transform(content="x"), None),
        (block_transform(content="x"), "seed\n"),
        (block_transform(content="x", before=True, after=True), "seed\n"),
        (block_transform(content="x", line="anchor", before=True), "anchor\n"),
        (block_transform(content="x", line="anchor", after=True), "anchor\n"),
        (block_transform(content="new"), block_text("old")),
        (REMOVE, "keep\n" + block_text("old") + "tail\n"),
        (REMOVE, "no markers\n"),
        (REMOVE, None),
    ]
    for transform, seed in cases:
        once = transform(seed)
        assert transform(once) == once


# --- Through files.edit: materialization --------------------------------------


async def test_edit_yields_a_put_for_a_new_block():
    host = SimulatedHost(name="h", remote_files={REMOTE_PATH: b"original\n"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.edit(ctx, REMOTE_PATH, ENSURE)]

    assert ctx.changed
    assert ctx.reason == f"content {REMOTE_PATH} differs from the transform, will rewrite"
    (put,) = commands
    assert isinstance(put, PutCommand)
    assert put.dest == REMOTE_PATH
    # No "before this" line in the file: the block is appended at the end.
    assert read_source_bytes(put.src).decode() == (
        "original\n" + block_text("please add this")
    )


async def test_edit_is_a_no_op_at_the_block_fixpoint():
    host = SimulatedHost(
        name="h",
        remote_files={REMOTE_PATH: block_text("please add this").encode()},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.edit(ctx, REMOTE_PATH, ENSURE)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({REMOTE_PATH})"
    assert commands == []


async def test_edit_removal_of_an_absent_file_creates_nothing():
    """The None pass-through end to end at the operation level: no command,
    no change, and the simulated filesystem stays empty."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.edit(ctx, REMOTE_PATH, REMOVE)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({REMOTE_PATH})"
    assert commands == []
    assert host.remote_files == {}


# --- End to end through the driver ---------------------------------------------


def ensure_deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the same block twice: the second edit proves the fixpoint."""
    yield op(files.edit, name="Ensure block", path=REMOTE_PATH, transform=ENSURE)
    yield op(files.edit, name="Re-ensure block", path=REMOTE_PATH, transform=ENSURE)


def remove_deploy(host: Host) -> Generator[OperationRequest]:
    """Remove the same block twice: the second removal proves the fixpoint."""
    yield op(files.edit, name="Remove block", path=REMOTE_PATH, transform=REMOVE)
    yield op(files.edit, name="Re-remove block", path=REMOTE_PATH, transform=REMOVE)


async def test_ensure_twice_plan_and_apply_log():
    """Prepare materializes both edits against pre-deployment state (both
    changed); only the apply log -- fresh per-step FactCache -- shows the
    second edit reading back what the first wrote."""

    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.remote_files = {REMOTE_PATH: b"before this\n"}
        return host

    plans, report, hosts = await run(
        INVENTORY, ensure_deploy, host_factory=factory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends
    assert f"content {REMOTE_PATH} differs" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Ensure block"].changed
    assert not entries["Re-ensure block"].changed
    assert entries["Re-ensure block"].reason == (
        f"content already up to date ({REMOTE_PATH})"
    )

    # The block landed before the matching line, over the simulated scp seam.
    assert hosts[0].remote_files == {
        REMOTE_PATH: (block_text("please add this") + "before this\n").encode()
    }
    assert f"$ scp <stream> -> {REMOTE_PATH}" in format_log(report)


async def test_removal_run_end_to_end():
    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.remote_files = {
            REMOTE_PATH: ("keep\n" + block_text("please add this") + "tail\n").encode()
        }
        return host

    _plans, report, hosts = await run(
        INVENTORY, remove_deploy, host_factory=factory
    )
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Remove block"].changed
    assert not entries["Re-remove block"].changed
    assert hosts[0].remote_files == {REMOTE_PATH: b"keep\ntail\n"}


async def test_factory_call_inside_the_deploy_raises_determinism_error():
    """A transform built fresh in the deploy generator has a new identity each
    phase, so the apply-phase request diverges from the prepare plan -- the
    factory must be called once, outside the generator."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Ensure block",
            path=REMOTE_PATH,
            transform=block_transform(content="please add this"),
        )

    with pytest.raises(DeterminismError):
        await run(INVENTORY, deploy, host_factory=SimulatedHost.from_inventory)