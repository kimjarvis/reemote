# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.template``: render a control-side Jinja2 stream, install it.

``files.template`` is ``files.install`` with a render step in front: it reads a
Jinja2 template from a seekable IO-like *stream* on the control machine (never
a path -- the ``files.put`` contract), expands it against a ``variables``
mapping with Jinja2, and delegates the write to ``files.install``. The reuse is
the point: the ``FileContents`` content-only idempotence, the octal-digit
``mode`` validation, the dest-derived ``/tmp`` spool naming, the optional
``user``/``group`` chown and the ``InstallSpoolCommand``/``InstallCommand``
pair all stay ``install``'s, so a template converges exactly like the
equivalent literal content.

Covers the Jinja2 rendering (variable substitution, control flow, the
``StrictUndefined`` and trailing-newline behaviour), the stream contract
(text and binary sources, rewound between reads), the accepted ``variables``
spellings (a mapping for a direct call, a tuple of pairs at an ``op()`` site),
the delegation of ``mode``/``user``/``group`` and the mode validation, the
content-equality idempotence, and end-to-end ``run``/plan/log behaviour
including per-host rendering and the second-run no-op.
"""

import io
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import format_log, format_plan, run
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import (
    InstallCommand,
    InstallSpoolCommand,
    _install_spool_path,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

TWO_HOST_INVENTORY = [
    {"host": "web1", "connection": {"key_path": "~/.ssh/id_ed25519"}},
    {"host": "web2", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]

DEST = "/etc/reemote-template-demo.conf"
SPOOL = _install_spool_path(DEST)

# The template text and the variables it renders with. A plain str at module
# level; the *stream* wrapping it is what an op() site must keep identity-stable.
TEMPLATE_TEXT = "# hi {{ name }}\nport = {{ port }}\n"
VARIABLES = (("name", "nginx"), ("port", 80))
RENDERED = "# hi nginx\nport = 80\n"

# Single shared streams for the end-to-end deploys: OperationRequest compares a
# stream by identity, so the same object must ride both phases (and every
# host). read_source_bytes rewinds it before each read, so sharing is safe.
TEMPLATE_STREAM = io.StringIO(TEMPLATE_TEXT)
PER_HOST_TEXT = "host = {{ hostname }}\n"
PER_HOST_STREAM = io.StringIO(PER_HOST_TEXT)


async def render_content(template: str, variables=VARIABLES, dest=DEST) -> str:
    """Render ``template`` through ``files.template`` and return the content.

    Drives the operation against an empty ``SimulatedHost`` and pulls the
    rendered text out of the yielded ``InstallSpoolCommand`` -- the descriptor
    that carries exactly the string ``files.install`` would spool. A fresh
    stream per call is fine here: a direct operation call has no determinism
    guard (that lives at the ``op()``/driver boundary).
    """
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx, io.StringIO(template), dest, variables=variables
        )
    ]
    return commands[0].content


# --- Rendering ----------------------------------------------------------------


async def test_template_substitutes_variables():
    assert await render_content(TEMPLATE_TEXT) == RENDERED


async def test_template_renders_jinja_control_flow():
    """Full Jinja2 parity: loops and conditionals, not just ``{{ var }}``."""
    template = "{% for s in servers %}server {{ s }}\n{% endfor %}"
    assert await render_content(template, variables=(("servers", ("a", "b")),)) == (
        "server a\nserver b\n"
    )


async def test_template_keeps_the_trailing_newline():
    """keep_trailing_newline: a template's final newline survives the render,
    so the content-only idempotence comparison sees exactly what was written."""
    assert await render_content("line\n") == "line\n"


async def test_template_without_a_trailing_newline_keeps_none():
    assert await render_content("line") == "line"


async def test_template_renders_with_an_empty_context():
    """A template referencing no variable renders with variables omitted."""
    assert await render_content("static = true\n", variables=None) == "static = true\n"


# --- The stream contract ------------------------------------------------------


async def test_template_accepts_a_binary_stream():
    """An io.BytesIO source decodes as UTF-8, exactly as read_source_bytes
    normalizes it for files.put."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx, io.BytesIO(TEMPLATE_TEXT.encode()), DEST, variables=VARIABLES
        )
    ]
    assert commands[0].content == RENDERED


async def test_template_rewinds_the_stream_between_reads():
    """The same stream object renders identically twice -- it is rewound to
    position 0 before each read, so one module-level stream serves the
    prepare and apply phases (and every host)."""
    stream = io.StringIO(TEMPLATE_TEXT)
    host = SimulatedHost(name="h")

    first = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    a = [c async for c in files.template(first, stream, DEST, variables=VARIABLES)]
    second = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    b = [c async for c in files.template(second, stream, DEST, variables=VARIABLES)]

    assert a[0].content == b[0].content == RENDERED


# --- The variables spellings --------------------------------------------------


async def test_template_accepts_a_mapping_for_a_direct_call():
    """A direct call (no op()) may pass a plain dict; the operation copies it
    into the render context."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx,
            io.StringIO(TEMPLATE_TEXT),
            DEST,
            variables={"name": "nginx", "port": 80},
        )
    ]
    assert commands[0].content == RENDERED


# --- Rendering errors surface before any host is contacted --------------------


async def test_template_strict_undefined_raises_value_error():
    """A variable the template references but the context omits raises
    ValueError (chained from the Jinja2 StrictUndefined error), naming dest."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="failed to render template"):
        [
            cmd
            async for cmd in files.template(
                ctx, io.StringIO("{{ missing }}"), DEST, variables=VARIABLES
            )
        ]


async def test_template_malformed_syntax_raises_value_error():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="failed to render template"):
        [
            cmd
            async for cmd in files.template(
                ctx, io.StringIO("{% if %}"), DEST, variables=VARIABLES
            )
        ]


# --- Delegation: the operation shape is files.install's -----------------------


async def test_template_yields_the_install_machinery():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx, io.StringIO(TEMPLATE_TEXT), DEST, variables=VARIABLES
        )
    ]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} is absent, will install"
    spool_cmd, install_cmd = commands
    assert isinstance(spool_cmd, InstallSpoolCommand)
    assert spool_cmd.content == RENDERED
    assert spool_cmd.dest == SPOOL
    assert isinstance(install_cmd, InstallCommand)
    assert install_cmd.spool == SPOOL
    assert install_cmd.dest == DEST
    assert install_cmd.mode == 644


async def test_template_is_a_no_op_when_the_rendered_content_matches():
    host = SimulatedHost(name="h", remote_files={DEST: RENDERED.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx, io.StringIO(TEMPLATE_TEXT), DEST, variables=VARIABLES
        )
    ]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({DEST})"
    assert commands == []


async def test_template_rewrites_when_the_rendered_content_differs():
    host = SimulatedHost(name="h", remote_files={DEST: b"stale\n"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx, io.StringIO(TEMPLATE_TEXT), DEST, variables=VARIABLES
        )
    ]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} differs, will install"
    assert len(commands) == 2


async def test_template_mode_and_owner_reach_the_install_command():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx,
            io.StringIO(TEMPLATE_TEXT),
            DEST,
            variables=VARIABLES,
            mode="0640",
            user="dd-agent",
            group="dd-agent",
        )
    ]
    _, install_cmd = commands
    assert install_cmd.mode == 640
    assert install_cmd.user == "dd-agent"
    assert install_cmd.group == "dd-agent"
    assert f"chown dd-agent:dd-agent {DEST}" in install_cmd.shell()


@pytest.mark.parametrize("mode", ["u+x", "888", "", "0o644"])
async def test_template_rejects_non_octal_digit_modes(mode):
    """The mode validation is install's, reached through the delegation."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Invalid file mode"):
        [
            cmd
            async for cmd in files.template(
                ctx, io.StringIO(TEMPLATE_TEXT), DEST, variables=VARIABLES, mode=mode
            )
        ]


async def test_template_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.template(
            ctx,
            io.StringIO(TEMPLATE_TEXT),
            DEST,
            variables=VARIABLES,
            some_global_arg=True,
        )
    ]
    assert len(commands) == 2


# --- End to end through the driver --------------------------------------------


def template_deploy():
    """One template install, then a second install of the same render at the
    same path. The stream is a module-level constant -- identity-stable across
    phases -- and the variables are a hashable tuple of pairs."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.template,
            name="Install rendered config",
            template=TEMPLATE_STREAM,
            dest=DEST,
            variables=VARIABLES,
            sudo=True,
        )
        yield op(
            files.template,
            name="Re-install rendered config",
            template=TEMPLATE_STREAM,
            dest=DEST,
            variables=VARIABLES,
            sudo=True,
        )

    return deploy


async def test_template_end_to_end_plan_log_and_second_no_op():
    plans, report, hosts = await run(
        INVENTORY, template_deploy(), host_factory=SimulatedHost.from_inventory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the file was absent for both
    assert f"content {DEST} is absent, will install" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Install rendered config"].changed
    assert not entries["Re-install rendered config"].changed
    assert entries["Re-install rendered config"].reason == (
        f"content already up to date ({DEST})"
    )

    # The rendered content landed at dest and the spool was cleaned up.
    assert hosts[0].remote_files == {DEST: RENDERED.encode()}

    log = format_log(report)
    assert f"$ scp <files install> -> {SPOOL}" in log
    assert "$ sudo sh -c 'mkdir -p /etc && cp" in log


async def test_template_dry_run_sends_nothing():
    plans, report, hosts = await run(
        INVENTORY,
        template_deploy(),
        host_factory=SimulatedHost.from_inventory,
        dry=True,
    )
    assert report is None
    assert plans["web1"][0].changed
    assert hosts[0].remote_files == {}


async def test_template_renders_per_host_end_to_end():
    """The shared template stream renders a different file per host because
    the variables carry host.name -- the whole reason to template. The stream
    stays a module-level constant so the determinism guard sees one identity
    across the prepare and apply phases."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.template,
            name="Install per-host config",
            template=PER_HOST_STREAM,
            dest=DEST,
            variables=(("hostname", host.name),),
            sudo=True,
        )

    _plans, report, hosts = await run(
        TWO_HOST_INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )
    assert report is not None
    by_name = {host.name: host for host in hosts}
    assert by_name["web1"].remote_files[DEST] == b"host = web1\n"
    assert by_name["web2"].remote_files[DEST] == b"host = web2\n"
