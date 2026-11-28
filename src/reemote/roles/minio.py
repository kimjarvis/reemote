# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The MinIO role (migrated from ``wezhai/minio``).
#
# Installs and runs MinIO on Ubuntu hosts from the bundled release tarball
# (``minio`` + ``mc``): the ``minio`` group and user, the five managed
# directories, the unpacked binaries, the rendered ``/etc/default/minio`` and
# ``/etc/systemd/system/minio.service``, the started unit with its restart
# handler and console-port wait, and the three opt-in features the role ships
# behind ``--tags never``: bucket creation, admin policy/user provisioning, and
# the ``mc mirror`` bucket-sync units. :func:`minio` is the whole task list, and
# :class:`MinioVars` is ``defaults/main.yml``, bound from the inventory entry's
# ``minio`` role mapping by :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role ships no OS dispatch at
# all (its ``vars/main.yml`` is empty), so its defaults are the frozen constants
# below and an off-target host is the family guard's early return.
#
# The stream carries the install waves plus one wave per ``mc`` command (design
# doc section 5.7); the wave count is host-dependent because every feature is
# vars-gated:
#
# 0. :func:`install` (group, user, directories, package spool + unarchive) and
#    :func:`configure` (the two rendered files, ``Start minio service``) --
#    closed by ``checkpoint("install done")``, the role's ``notify``.
# 1 :func:`handler` -- ``Restart minio service (handler)`` and
#    ``Waiting for minio console port`` -- only where the checkpoint found a
#    notifying change or the start itself changed; then :func:`objects`,
#    :func:`accounts` and :func:`sync_services`, whose ``act(Shell, ...)``
#    barriers each close their own wave.
#
# Deviations from the Ansible role, all deliberate:
#
# * **D1** the family guard is the body's opening ``if``; a non-Ubuntu host
#   emits no requests, no barriers and no log lines at all.
# * **D2** the role's ``tags: never`` opt-ins become vars gates: ``buckets``,
#   ``users`` and ``sync`` default to empty, so a plain deploy is exactly the
#   role's default ``install`` + ``start`` path. The ``uninstall``/``stop``/
#   ``restart`` tag branches are scoped out.
# * **D3** ``play_hosts``/``hostvars[host].ansible_host`` in the env template
#   become the per-host ``node_address`` variable, enumerated over
#   :attr:`~reemote.rolevars.VarsSet.values` in sorted order (the play order has
#   no analogue); the host's own address falls back to its ``connection`` data.
# * **D4** ``run_once`` becomes the per-host ``primary`` variable: bucket ops
#   run only on the primary in cluster mode (and per host standalone, as the
#   role branches), user and sync ops always only on the primary.
# * **D5** the bundled 44 MB ``files/minio.tar.gz`` cannot ship in a library:
#   ``package_url`` fetches it control-side through :func:`~reemote.modules
#   .files.unarchive`, and ``package_path`` reads the controller file and
#   spools it with :func:`~reemote.modules.files.install` before the unarchive
#   (the spool at ``/tmp/minio.tar.gz`` persists; replacing the version means
#   removing ``<install_dir>/minio`` so the ``creates`` gate re-extracts).
#   That spool-then-extract pair only previews because of issue #73: an
#   operation planning a source this deploy stages reports a plan line instead
#   of raising on the host that does not have it yet.
# * **D6** ``become_user: minio`` on the ``mc`` shells has no per-request
#   analogue: every command embeds ``sudo -u minio -H`` inside one elevated
#   ``act(Shell, sudo=True)`` (the composer/nodejs precedent).
# * **D7** ``wait_for``'s ``delay: 5`` is dropped -- :func:`~reemote.modules
#   .server.wait` polls from once, with the role's ``timeout: 60`` kept.
# * **D8** the chained handlers (restart notifies the wait) collapse into one
#   handler wave: the restart request, then the wait request, sequential.
# * **D9** the ``mc`` shells re-run on every pass -- an ``act`` result is data,
#   exactly like the role's non-idempotent ``shell`` tasks, whose
#   ``failed_when`` tolerance of "already in effect" is reproduced.
# * **D10** ``Create ansible remote_tmp dir`` (``/home/minio/.ansible/tmp``) is
#   dropped: it is Ansible's own plumbing, not MinIO state.
# * **D11** the user's hardcoded ``home: /home/minio`` becomes
#   ``/home/<sys_user>``, so renaming the service user stays coherent.
# * **D12** the templates' default ``mode: 0644`` is carried explicitly on the
#   two :func:`~reemote.modules.files.install` requests.
#
# :func:`minio` returns :class:`MinioOutputs` on every path, including the
# family guard's early return (design doc sections 9 and 17.2).
# """

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import Host
from ..modules import files, server, systemd
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint

# --- role constants (defaults/main.yml, frozen to the Ubuntu target) ---

#: The systemd unit the role manages, and the handler's service name.
SERVICE = "minio"

#: ``package_name`` -- the tarball's spool destination when read controller-side.
PACKAGE_SPOOL = "/tmp/minio.tar.gz"

#: ``templates/minio_env.j2``'s destination (Ansible ``template`` default mode).
ENV_PATH = "/etc/default/minio"

#: ``templates/minio.service.j2``'s destination.
UNIT_PATH = "/etc/systemd/system/minio.service"

#: The directory ``Copy policy files`` writes each policy JSON into.
POLICY_SPOOL_DIR = "/tmp"

#: The ``mc`` alias the role's bucket/user shells address.
ALIAS = "ansible_minio"

#: ``wait_for``'s ``timeout: 60`` (its ``delay: 5`` is dropped, see D7).
CONSOLE_WAIT_TIMEOUT = 60

#: The unarchive ``creates`` gate: the server binary marks an extracted bundle.
SERVER_BINARY = "minio"

#: The role's bundled ``files/getput_policy.json``, verbatim -- the only
#: policy file the default ``policies`` anchor names.
GETPUT_POLICY_JSON = """{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:DeleteObject",
                "s3:GetBucketLocation",
                "s3:GetObject",
                "s3:ListBucket",
                "s3:PutObject"
            ],
            "Resource": [
                "arn:aws:s3:::*"
            ]
        }
    ]
}"""

#: The bundled policy file :func:`policy_content` resolves without a read.
BUNDLED_POLICY_FILE = "getput_policy.json"

#: ``templates/minio_env.j2``, verbatim except for the D3 loop source: the
#: role's ``{% for host in play_hosts %}...hostvars[host].ansible_host``
#: becomes ``{% for addr in minio_node_addresses %}`` -- the addresses the
#: body enumerated from the ``node_address`` variables.
ENV_TEMPLATE = """MINIO_ROOT_USER={{ minio_admin_user }}
MINIO_ROOT_PASSWORD={{ minio_admin_pass }}
MINIO_PROMETHEUS_AUTH_TYPE=public
{% if minio_cluster_mode %}
MINIO_OPTS="--config-dir {{ minio_config_dir }} --address ':{{ minio_service_port }}' --console-address ':{{ minio_console_port }}'{% for addr in minio_node_addresses %} http://{{ addr }}:{{ minio_service_port }}{{ minio_data_dir }}/{% endfor %}"
{% else %}
MINIO_OPTS="--config-dir {{ minio_config_dir }} --address ':{{ minio_service_port }}' --console-address ':{{ minio_console_port }}' {{ minio_data_dir }}/"
{% endif %}
"""

#: ``templates/minio.service.j2``, verbatim.
UNIT_TEMPLATE = """[Unit]
Description=MinIO
Documentation=https://min.io/docs/minio/linux/index.html
Wants=network-online.target
After=network-online.target
AssertFileIsExecutable={{ minio_install_dir }}/minio

[Service]
WorkingDirectory={{ minio_install_dir }}

User={{ minio_sys_user }}
Group={{ minio_sys_group }}
#ProtectProc=invisible

EnvironmentFile=-/etc/default/minio
#ExecStartPre=/bin/bash -c "if [ -z \\"${MINIO_VOLUMES}\\" ]; then echo \\"Variable MINIO_VOLUMES not set in /etc/default/minio\\"; exit 1; fi"
ExecStart={{ minio_install_dir }}/minio server $MINIO_OPTS

# This may improve systemctl setups where other services use `After=minio.server`
# Uncomment the line to enable the functionality
# Type=notify

# Let systemd restart this service always
Restart=always

# Specifies the maximum file descriptor number that can be opened by this process
LimitNOFILE=65536

# Specifies the maximum number of threads this process can create
TasksMax=infinity

# Disable timeout logic and wait until process is stopped
TimeoutStopSec=infinity
SendSIGKILL=no

[Install]
WantedBy=multi-user.target
"""

#: ``templates/sync.service.j2``, verbatim except that the role's ``sync``/
#: ``bucket_item`` loop vars arrive as one render's flat context.
SYNC_TEMPLATE = """[Unit]
Description=Miniomc
Documentation=https://docs.minio.io/

[Service]
User={{ minio_sys_user }}
Group={{ minio_sys_group }}
ExecStart={{ minio_install_dir }}/mc mirror --limit-upload {{ sync_upload_limit }} --remove --overwrite --watch minio{{ sync_from_label }}/{{ bucket_local }} minio{{ sync_to_label }}/{{ bucket_remote }}
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


# --- variables ---------------------------------------------------------------


class BucketSpec(BaseModel):
    """One bucket: an item of ``bucket_list``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: ``expire_days`` -- the ``mc ilm rule add`` object-expiration rule.
    expire_days: int | None = None

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        """An empty bucket name is a typo, not "no bucket"."""
        if not value:
            raise ValueError("a bucket needs a name")
        return value


class PolicySpec(BaseModel):
    """One admin policy: an item of ``policies`` (the YAML anchor's two keys).

    :attr:`content` is the JSON document itself; left unset, :attr:`file`
    resolves bundled-first (the role's ``files/getput_policy.json``) and then
    as a controller-side path, read at plan time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    file: str = BUNDLED_POLICY_FILE
    content: str | None = None

    @field_validator("name", "file")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        """Neither the policy name nor its file may be empty."""
        if not value:
            raise ValueError("a policy needs a name and a file")
        return value


class MinioUserSpec(BaseModel):
    """One admin user: an item of ``users``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    username: str
    password: str
    #: ``user.policies | default(policies)`` -- the role's per-user set_fact.
    policies: tuple[PolicySpec, ...] | None = None

    @field_validator("username", "password")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        """An empty username or password is a typo."""
        if not value:
            raise ValueError("a minio user needs a username and a password")
        return value


class SyncSite(BaseModel):
    """One side of a bucket-sync pair (the role's ``sync.from``/``sync.to``).

    The remote half needs ``host``/``port``/``access_key``/``secret_key``; the
    local half only its ``area_label`` -- the local endpoint is always this
    host's own address, so the unused fields simply stay unset there.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    area_label: str
    host: str | None = None
    port: int = 9000
    access_key: str | None = None
    secret_key: str | None = None


class BucketMapping(BaseModel):
    """One ``sync.bucket_mapping`` entry: local bucket -> remote bucket."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    local: str
    remote: str


class SyncSpec(BaseModel):
    """One mirror service group: an item of ``sync_service``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    upload_limit: str = "10MiB"
    source: SyncSite
    target: SyncSite
    bucket_mapping: tuple[BucketMapping, ...] = ()

    @field_validator("target")
    @classmethod
    def _target_complete(cls, value: SyncSite) -> SyncSite:
        """The remote site is the one the unit actually dials."""
        if not (value.area_label and value.host and value.access_key and value.secret_key):
            raise ValueError(
                "a sync target needs area_label, host, access_key and secret_key"
            )
        return value


class MinioVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``minio`` role mapping. The four derived directories
    (``minio_install_dir`` &co) are ``None`` → recomputed from
    :attr:`basedir`, so overriding the basedir moves the whole tree as it does
    in the role. Sequences are ``tuple``, never ``list``: the model is frozen
    and hashable, and a value that reaches :func:`~reemote.core.op` must hash.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``cluster_mode``: the env template's endpoint loop (D3) and the
    #: bucket-op placement (D4).
    cluster_mode: bool = True
    #: This host's address as peers should reach it (D3); ``None`` falls back
    #: to the inventory ``connection`` address.
    node_address: str | None = None
    #: D4's ``run_once`` stand-in: this host is the play's first host.
    primary: bool = True
    sys_user: str = "minio"
    sys_group: str = "minio"
    admin_user: str = "minioadmin"
    admin_pass: str = "minioadmin"
    service_port: int = 9000
    console_port: int = 9001
    basedir: str = "/data/minio"
    #: ``null`` falls back to ``<basedir>/bin`` (the role's derived default).
    install_dir: str | None = None
    config_dir: str | None = None
    data_dir: str | None = None
    log_dir: str | None = None
    backup_dir: str = "/opt/minio"
    #: D5's two package sources; exactly one must be set.
    package_url: str | None = None
    package_path: str | None = None
    #: The ``policies`` YAML anchor's default.
    policies: tuple[PolicySpec, ...] = (PolicySpec(name="getput"),)
    #: ``users`` -- empty by default: D2's ``tags: never`` gate.
    users: tuple[MinioUserSpec, ...] = ()
    #: ``bucket_list`` -- empty by default: D2's ``tags: never`` gate.
    buckets: tuple[BucketSpec, ...] = ()
    #: ``sync_service`` -- empty by default: D2's ``tags: never`` gate.
    sync: tuple[SyncSpec, ...] = ()


@dataclass(frozen=True)
class MinioOutputs:
    """What :func:`minio` publishes to a composing role.

    ``service`` is the unit name, ``install_dir`` the binary directory,
    ``endpoint`` the ``mc`` alias URL for this host. A host the family guard
    skipped carries empty strings.
    """

    service: str
    install_dir: str
    endpoint: str


@dataclass(frozen=True)
class SyncUnit:
    """One rendered bucket-sync unit: its service name and file content."""

    name: str
    content: str


@dataclass(frozen=True)
class ResolvedPolicy:
    """One policy with its JSON in hand: name, spool file name, content."""

    name: str
    file: str
    content: str


@dataclass(frozen=True)
class MinioPlan:
    """One host's resolved install/configure targets, built by
    :func:`build_plan` after the variables resolve -- the directories, the
    rendered files, the package source and the sync units are all decided
    before the first request, so every sub-deploy is a pure function of it.
    """

    sys_user: str
    sys_group: str
    admin_user: str
    admin_pass: str
    service_port: int
    console_port: int
    cluster_mode: bool
    install_dir: str
    config_dir: str
    data_dir: str
    log_dir: str
    backup_dir: str
    mc: str
    alias_url: str
    env_content: str
    unit_content: str
    #: The D5 package routes: a URL for ``files.unarchive`` to fetch
    #: control-side, or the controller-read bytes to spool.
    package_url: str | None
    package_content: bytes | None
    sync_units: tuple[SyncUnit, ...]
    policies: tuple[ResolvedPolicy, ...]


# --- pure helpers ------------------------------------------------------------


def resolve_dirs(hv: MinioVars) -> tuple[str, str, str, str, str]:
    """The role's four derived directories plus the fixed backup dir.

    ``install_dir``/``config_dir``/``data_dir``/``log_dir`` default to
    ``<basedir>/{bin,cfg,data,log}`` exactly as ``defaults/main.yml`` derives
    them; ``backup_dir`` has no derived default there.
    """
    return (
        hv.install_dir or f"{hv.basedir}/bin",
        hv.config_dir or f"{hv.basedir}/cfg",
        hv.data_dir or f"{hv.basedir}/data",
        hv.log_dir or f"{hv.basedir}/log",
        hv.backup_dir,
    )


def own_address(v: VarsSet[MinioVars], hv: MinioVars, host: Host) -> str:
    """This host's MinIO address: ``node_address``, else its connection data.

    The role read ``hostvars[inventory_hostname].ansible_host``; reemote's
    ``Host`` carries no ``.address`` property, so the fallback walks the
    ``connection`` mapping the way the connector resolves it.
    """
    if hv.node_address:
        return hv.node_address
    connection = host.connection or {}
    return (
        connection.get("address")
        or connection.get("host")
        or connection.get("hostname")
        or host.name
    )


def node_addresses(v: VarsSet[MinioVars], hv: MinioVars, host: Host) -> tuple[str, ...]:
    """The cluster endpoint addresses (D3): every ``node_address`` that is set.

    Enumerated over the whole :class:`~reemote.rolevars.VarsSet` -- the
    ``play_hosts`` analogue -- in sorted order, because a per-host body has no
    play order to preserve. A host that set no ``node_address`` contributes
    its own connection address instead of nothing, so a single-node "cluster"
    still renders one endpoint like the role's loop over a one-host play.
    """
    addrs = {model.node_address for model in v.values.values() if model.node_address}
    if not addrs:
        return (own_address(v, hv, host),)
    return tuple(sorted(addrs))


def render_env(hv: MinioVars, config_dir: str, data_dir: str, endpoints: tuple[str, ...]) -> str:
    """``templates/minio_env.j2``, pre-rendered control-side (D3 source)."""
    from ._render import jinja2_environment

    return jinja2_environment().from_string(ENV_TEMPLATE).render(
        minio_admin_user=hv.admin_user,
        minio_admin_pass=hv.admin_pass,
        minio_cluster_mode=hv.cluster_mode,
        minio_config_dir=config_dir,
        minio_service_port=hv.service_port,
        minio_console_port=hv.console_port,
        minio_data_dir=data_dir,
        minio_node_addresses=list(endpoints),
    )


def render_unit(hv: MinioVars, install_dir: str) -> str:
    """``templates/minio.service.j2``, pre-rendered control-side."""
    from ._render import jinja2_environment

    return jinja2_environment().from_string(UNIT_TEMPLATE).render(
        minio_install_dir=install_dir,
        minio_sys_user=hv.sys_user,
        minio_sys_group=hv.sys_group,
    )


def render_sync_units(hv: MinioVars, install_dir: str) -> tuple[SyncUnit, ...]:
    """``templates/sync.service.j2`` once per ``sync`` x ``bucket_mapping``.

    The unit is named exactly as the role names it and its destination:
    ``minio_<from>_<local>_to_<to>_<remote>.service``.
    """
    from ._render import jinja2_environment

    environment = jinja2_environment()
    template = environment.from_string(SYNC_TEMPLATE)
    units: list[SyncUnit] = []
    for sync in hv.sync:
        for mapping in sync.bucket_mapping:
            name = (
                f"minio_{sync.source.area_label}_{mapping.local}"
                f"_to_{sync.target.area_label}_{mapping.remote}.service"
            )
            content = template.render(
                minio_install_dir=install_dir,
                minio_sys_user=hv.sys_user,
                minio_sys_group=hv.sys_group,
                sync_upload_limit=sync.upload_limit,
                sync_from_label=sync.source.area_label,
                sync_to_label=sync.target.area_label,
                bucket_local=mapping.local,
                bucket_remote=mapping.remote,
            )
            units.append(SyncUnit(name=name, content=content))
    return tuple(units)


def policy_content(pol: PolicySpec) -> str:
    """Resolve one policy's JSON: literal, bundled constant, controller read.

    A ``file`` that is not the bundled ``getput_policy.json`` is a
    controller-side path read here -- the same non-barrier control read
    ``apache`` does for a custom template (the role's ``copy`` looked it up in
    the role's own ``files/``, which a library role does not have).
    """
    if pol.content is not None:
        return pol.content
    if pol.file == BUNDLED_POLICY_FILE:
        return GETPUT_POLICY_JSON
    return Path(pol.file).read_text(encoding="utf-8")


def resolved_policies(hv: MinioVars) -> tuple[ResolvedPolicy, ...]:
    """Every policy any user needs, deduplicated in first-seen order.

    Reproduces the role's per-user ``set_fact``
    (``user.policies | default(policies)``); the role's ``loop: users`` re-copy
    and re-created the same policy per user, which an ``mc admin policy
    create`` of identical content makes idempotent -- so the copies and
    creates are deduplicated once, harmlessly (a deviation this docstring
    owns, not a behaviour change).
    """
    seen: dict[tuple[str, str], ResolvedPolicy] = {}
    for user in hv.users:
        for pol in user.policies if user.policies is not None else hv.policies:
            key = (pol.name, pol.file)
            if key not in seen:
                seen[key] = ResolvedPolicy(
                    name=pol.name, file=pol.file, content=policy_content(pol)
                )
    return tuple(seen.values())


def as_minio(plan: MinioPlan, command: str) -> str:
    """D6: one ``mc`` invocation as the service user inside an elevated shell."""
    return f"sudo -u {plan.sys_user} -H {command}"


def alias_set_script(plan: MinioPlan) -> str:
    """``Add minio config`` / ``Add minio config only first host.``."""
    return as_minio(
        plan,
        f"{plan.mc} alias set {ALIAS} {plan.alias_url} {plan.admin_user} {plan.admin_pass}",
    )


def bucket_script(plan: MinioPlan, bucket: BucketSpec) -> str:
    """One ``Create buckets`` loop item, verbatim incl. the ``| true`` gate."""
    lines = [as_minio(plan, f"{plan.mc} mb {ALIAS}/{bucket.name} | true")]
    if bucket.expire_days is not None:
        lines.append(
            as_minio(
                plan,
                f"{plan.mc} ilm rule add --expire-days {bucket.expire_days}"
                f" {ALIAS}/{bucket.name}",
            )
        )
    return "\n".join(lines)


def policy_create_script(plan: MinioPlan, pol: ResolvedPolicy) -> str:
    """One ``Create policies`` loop item."""
    return as_minio(
        plan,
        f"{plan.mc} admin policy create {ALIAS} {pol.name}"
        f" {POLICY_SPOOL_DIR}/{pol.file}",
    )


def user_script(plan: MinioPlan, user: MinioUserSpec, names: tuple[str, ...]) -> str:
    """One ``Create user`` task: the add plus every policy attach (D9)."""
    lines = [
        as_minio(plan, f"{plan.mc} admin user add {ALIAS} {user.username} {user.password}")
    ]
    lines.extend(
        as_minio(plan, f"{plan.mc} admin policy attach {ALIAS} {name} --user {user.username}")
        for name in names
    )
    return "\n".join(lines)


def sync_config_script(plan: MinioPlan, sync: SyncSpec) -> str:
    """``Add synchronize config``: the local and remote ``config host add``."""
    local = as_minio(
        plan,
        f"{plan.mc} config host add minio{sync.source.area_label} {plan.alias_url}"
        f" {plan.admin_user} {plan.admin_pass}",
    )
    remote = as_minio(
        plan,
        f"{plan.mc} config host add minio{sync.target.area_label}"
        f" http://{sync.target.host}:{sync.target.port}"
        f" {sync.target.access_key} {sync.target.secret_key}",
    )
    return f"{local}\n{remote}"


def _fail(result: dict, label: str, host_name: str) -> None:
    """Raise the example-style error for a failed ``act`` shell (D9)."""
    raise ValueError(
        f"{host_name}: {label} failed (exit {result['exit_status']}): "
        f"{(result['stdout'] + result['stderr']).strip()}"
    )


def build_plan(v: VarsSet[MinioVars], hv: MinioVars, host: Host) -> MinioPlan:
    """Freeze every read the install/configure stages need, control-side.

    The directory derivations, both renders, the sync units, the policy JSONs
    and -- D5 -- the package source all resolve here, so the deploy below
    branches on no host state at all.

    Raises:
        ValueError: Neither ``package_url`` nor ``package_path`` is set (the
            bundled tarball cannot ship in the library, D5). Raised before the
            first request, so nothing is half-applied.
        OSError: ``package_path`` (or a custom policy ``file``) names a
            controller file that cannot be read.
    """
    if not hv.package_url and not hv.package_path:
        raise ValueError(
            f"{host.name}: minio needs a package source -- set package_url "
            "(fetched control-side) or package_path (read controller-side)"
        )
    install_dir, config_dir, data_dir, log_dir, backup_dir = resolve_dirs(hv)
    endpoints = node_addresses(v, hv, host) if hv.cluster_mode else ()
    address = own_address(v, hv, host)
    package_content = (
        Path(hv.package_path).read_bytes() if hv.package_path else None
    )
    return MinioPlan(
        sys_user=hv.sys_user,
        sys_group=hv.sys_group,
        admin_user=hv.admin_user,
        admin_pass=hv.admin_pass,
        service_port=hv.service_port,
        console_port=hv.console_port,
        cluster_mode=hv.cluster_mode,
        install_dir=install_dir,
        config_dir=config_dir,
        data_dir=data_dir,
        log_dir=log_dir,
        backup_dir=backup_dir,
        mc=f"{install_dir}/mc",
        alias_url=f"http://{address}:{hv.service_port}",
        env_content=render_env(hv, config_dir, data_dir, endpoints),
        unit_content=render_unit(hv, install_dir),
        package_url=hv.package_url,
        package_content=package_content,
        sync_units=render_sync_units(hv, install_dir),
        policies=resolved_policies(hv),
    )


#: The configure requests whose change queues ``Restart minio service``.
NOTIFYING = ("Add environment file", "Add systemd file")

#: The ``Start minio service`` task's own notify target (the wait handler).
START_NAME = "Start minio service"


# --- sub-deploys -------------------------------------------------------------


def install(plan: MinioPlan) -> Deploy:
    """``tasks/install.yml``: group, user, the five directories, the package.

    ``Create ansible remote_tmp dir`` is not here (D10). The D5 package route
    is two requests -- the sha1-idempotent spool install, then the ``creates``-
    gated unarchive -- or, for a ``package_url``, just the unarchive.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            server.group,
            name="Create group if not exists",
            group=plan.sys_group,
            sudo=True,
        )
        yield op(
            server.user,
            name="Create user if not exists",
            user=plan.sys_user,
            group=plan.sys_group,
            home=f"/home/{plan.sys_user}",
            create_home=True,
            sudo=True,
        )
        for directory in (
            plan.install_dir,
            plan.config_dir,
            plan.data_dir,
            plan.log_dir,
            plan.backup_dir,
        ):
            yield op(
                files.directory,
                name=f"Create dirs. {directory}",
                path=directory,
                user=plan.sys_user,
                group=plan.sys_group,
                mode="0755",
                sudo=True,
            )
        if plan.package_content is not None:
            yield op(
                files.install,
                name=f"Spool {PACKAGE_SPOOL.rsplit('/', 1)[-1]}",
                content=plan.package_content,
                dest=PACKAGE_SPOOL,
                mode="0644",
                sudo=True,
            )
            package = PACKAGE_SPOOL
        else:
            package = plan.package_url
        yield op(
            files.unarchive,
            name="Unpack minio.tar.gz",
            src=package,
            dest=plan.install_dir,
            creates=f"{plan.install_dir}/{SERVER_BINARY}",
            user=plan.sys_user,
            group=plan.sys_group,
            mode="0755",
            sudo=True,
        )

    return deploy


def configure(plan: MinioPlan) -> Deploy:
    """The two templates (main.yml's ``install`` include tail) and the start.

    Both installs notify (their names are in :data:`NOTIFYING`); ``Start
    minio service`` carries the role's second notify channel -- here, a name
    the body reads out of the same checkpoint report (D8).
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.install,
            name="Add environment file",
            content=plan.env_content,
            dest=ENV_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        yield op(
            files.install,
            name="Add systemd file",
            content=plan.unit_content,
            dest=UNIT_PATH,
            mode="0644",
            user="root",
            group="root",
            sudo=True,
        )
        yield op(
            systemd.service,
            name=START_NAME,
            service=SERVICE,
            running=True,
            daemon_reload=True,
            sudo=True,
        )

    return deploy


def handler(plan: MinioPlan, notified: frozenset[str], started: frozenset[str]) -> Deploy:
    """``handlers/main.yml`` as one wave (D8): restart, then the port wait.

    The restart is the chained handler's first link and notifies the wait;
    the role's plain ``Start minio service`` task notifies the wait directly.
    A host in neither set yields nothing -- the converged no-op. The wait
    always reports changed (a poll is not a state gate), which is why it
    rides *inside* this notified-only wave rather than the configure one.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        restart = host.name in notified
        if restart:
            yield from service_handler(
                notified,
                (SERVICE,),
                action="restarted",
                daemon_reload=True,
                name="Restart minio service (handler)",
            )(host)
        if restart or host.name in started:
            yield op(
                server.wait,
                name=f"Waiting for minio console port {plan.console_port}",
                port=plan.console_port,
                timeout=CONSOLE_WAIT_TIMEOUT,
                sudo=True,
            )

    return deploy


def objects(plan: MinioPlan, hv: MinioVars) -> Deploy:
    """``tasks/bucket.yml``: the alias, then one wave per bucket (D4, D9).

    Skipped wholesale when ``buckets`` is empty (D2) or -- as the role
    branches -- when a *cluster* play is not the first host; standalone runs
    it per host exactly like the role's second pair of tasks.
    """
    if not hv.buckets:
        return _noop
    if hv.cluster_mode and not hv.primary:
        return _noop

    def deploy(host: Host) -> Generator[StageItem, Any]:
        result = yield act(
            Shell,
            command=alias_set_script(plan),
            name="Add minio config",
            sudo=True,
        )
        if result["exit_status"] != 0:
            _fail(result, "mc alias set", host.name)
        for bucket in hv.buckets:
            result = yield act(
                Shell,
                command=bucket_script(plan, bucket),
                name=f"Create bucket {bucket.name}",
                sudo=True,
            )
            if result["exit_status"] != 0:
                _fail(result, f"mc mb {bucket.name}", host.name)

    return deploy


def accounts(plan: MinioPlan, hv: MinioVars) -> Deploy:
    """``tasks/user.yml``: copy/create the policies, then add each user.

    The role ran the whole file ``run_once`` per user (D4: primary only) and
    looped it over ``users``; the policy copies and creates are deduplicated
    across users (see :func:`resolved_policies`).
    """
    if not hv.users or not hv.primary:
        return _noop

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for pol in plan.policies:
            yield op(
                files.install,
                name=f"Copy policy file {pol.file}",
                content=pol.content,
                dest=f"{POLICY_SPOOL_DIR}/{pol.file}",
                mode="0644",
                user=plan.sys_user,
                group=plan.sys_group,
                sudo=True,
            )
        for pol in plan.policies:
            result = yield act(
                Shell,
                command=policy_create_script(plan, pol),
                name=f"Create policy {pol.name}",
                sudo=True,
            )
            if result["exit_status"] != 0:
                _fail(result, f"mc admin policy create {pol.name}", host.name)
        for user in hv.users:
            names = tuple(
                pol.name
                for pol in (user.policies if user.policies is not None else hv.policies)
            )
            result = yield act(
                Shell,
                command=user_script(plan, user, names),
                name=f"Create user {user.username}",
                sudo=True,
            )
            # The role's failed_when: the whole task succeeds when the only
            # complaint is a policy attach that is already in effect (D9).
            output = result["stdout"] + result["stderr"]
            if result["exit_status"] != 0 and "already in effect" not in output:
                _fail(result, f"mc admin user add {user.username}", host.name)

    return deploy


def sync_services(plan: MinioPlan, hv: MinioVars) -> Deploy:
    """``tasks/sync.yml``: aliases, units, and one start per unit (D4).

    The role ran the whole include ``run_once`` regardless of cluster mode,
    so this is primary-only; the mirror daemons must not run twice.
    """
    if not hv.sync or not hv.primary:
        return _noop

    def deploy(host: Host) -> Generator[StageItem, Any]:
        for sync in hv.sync:
            result = yield act(
                Shell,
                command=sync_config_script(plan, sync),
                name="Add synchronize config",
                sudo=True,
            )
            if result["exit_status"] != 0:
                _fail(result, "mc config host add", host.name)
        for unit in plan.sync_units:
            yield op(
                files.install,
                name=f"Render systemd file {unit.name}",
                content=unit.content,
                dest=f"/etc/systemd/system/{unit.name}",
                mode="0644",
                user="root",
                group="root",
                sudo=True,
            )
            yield op(
                systemd.service,
                name=f"Start synchronize service {unit.name}",
                service=unit.name,
                running=True,
                daemon_reload=True,
                sudo=True,
            )

    return deploy


def _noop(host: Host) -> Generator[StageItem, Any]:
    """The empty deploy a vars gate returns."""
    return
    yield  # pragma: no cover -- makes this a generator function


# --- the role ----------------------------------------------------------------


def minio(v: VarsSet[MinioVars]) -> Deploy:
    """Install and run MinIO on Ubuntu hosts, with opt-in object provisioning.

    Args:
        v: The role's resolved variables, one :class:`MinioVars` per host
            (``resolve_vars(MinioVars, "minio", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves` that returns
        :class:`MinioOutputs` for every host it runs -- including a host
        outside the target family, whose outputs are empty.

    Raises:
        ValueError: No package source is configured (D5), or one of the
            ``mc`` shells exited non-zero outside the role's own tolerance.
        OSError: A ``package_path`` or custom policy file cannot be read on
            the controller.
    """

    def deploy(host: Host) -> Generator[StageItem, Any, MinioOutputs]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return MinioOutputs(service="", install_dir="", endpoint="")

        plan = build_plan(v, hv, host)

        # Wave 0: install + configure, closed by the role's own ``notify``.
        yield from install(plan)(host)
        yield from configure(plan)(host)
        wave_report = yield checkpoint("install done")
        notified = changed_hosts(wave_report, NOTIFYING)
        started = changed_hosts(wave_report, (START_NAME,))

        # Wave 1: the chained handler (D8). Each later act barrier closes its
        # own wave, so the mc provisioning runs strictly after the restart
        # and the console-port wait applied.
        yield from handler(plan, notified, started)(host)
        yield from objects(plan, hv)(host)
        yield from accounts(plan, hv)(host)
        yield from sync_services(plan, hv)(host)

        return MinioOutputs(
            service=SERVICE,
            install_dir=plan.install_dir,
            endpoint=plan.alias_url,
        )

    return deploy


__all__ = [
    "MinioOutputs",
    "MinioVars",
    "minio",
]
