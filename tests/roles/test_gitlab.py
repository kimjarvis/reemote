# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``gitlab`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, gitlab(v), host_factory=...)``. The control-side
vendor-script (``script.deb.sh``/``script.rpm.sh``) fetch is stubbed at
``files._fetch_url_bytes`` (autouse fixture), so nothing touches the network,
and the three shells the role acts through -- the vendor script, ``gitlab-ctl
reconfigure`` and the ``openssl req`` one-liner -- are canned ``shell_results``
records keyed by their exact ``sudo`` rendering (#16).

The inventory is the two target families plus the guard's proof: ``ubuntu``
(the apt path, ``gnupg2`` merged, the folded cache refresh), ``web1`` (the
RedHat/dnf path: plain ``dnf.packages`` installs, ``script.rpm.sh``, no
``gnupg2``) and ``db1`` (Alpine, which the role must never touch).

The load-bearing assertion is the migration's shape claim: the stream is
**fact-gated, so its wave count is host-dependent**. A fresh host emits the
full chain -- install probe (fact), repo script (act), bootstrap probe (fact),
first-run reconfigure (act), certificate probe (fact), ``openssl`` (act),
``checkpoint("configure done")``, then the ``restart gitlab`` command-handler
(act) -- while a converged host collapses to three facts and one checkpoint,
no acts and no handler wave at all. The dry preview parks on the *first*
barrier -- the ``/usr/bin/gitlab-ctl`` probe, whose answer decides every wave
after it -- so unlike nfs it plans only wave 0's dependency install.

**A between-pass bridge is required**: unlike nfs's fully self-maintaining
seams, the role's three ``FileExists`` barriers read paths no simulation
mutator creates (``apt`` records only package rows, an ``act`` shell only what
``shell_results`` answers), so ``bridge()`` adds the gitlab-ctl binary, the
bootstrapped marker and the certificate to ``existing_files`` -- which is
exactly the state the real first pass leaves on disk. Everything else
converges on its own: the apt stamp, the SSL directory row, and the config
file's content and root:root ``0600`` stat row.
"""

import shlex

import pytest
from pydantic import ValidationError

from reemote import CommandError, run_waves
from reemote.modules.files import files as files_module
from reemote.roles import GitlabVars, gitlab
from reemote.roles.gitlab import (
    BOOTSTRAPPED_PATH,
    CONFIG_PATH,
    GITLAB_CTL_PATH,
    GNUPG2_PACKAGE,
    NOTIFYING,
    RECONFIGURE_COMMAND,
    REPO_SCRIPT_SPOOL,
    REPO_SCRIPT_URLS,
    SSL_DIR,
    TARGET_FAMILIES,
    GitlabExtraOption,
    GitlabPlan,
    extra_settings_context,
    gitlab_plan,
    openssl_command,
    package_pin,
    render_gitlab_rb,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import ActionRequest, CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

#: The exact shells the ``sudo=True`` acts render (no privilege section in the
#: inventory, so a bare ``sudo`` prefix) -- the ``shell_results`` seam keys.
REPO_SHELL = f"sudo bash {REPO_SCRIPT_SPOOL}"
RECONFIGURE_SHELL = f"sudo {RECONFIGURE_COMMAND}"

#: The vendor script body: valid UTF-8, so ``files.download`` takes the text
#: install path exactly as it does for the real ``script.deb.sh``/``.rpm.sh``.
FAKE_SCRIPT = b"#!/bin/bash\n# canned gitlab repository installer\nexit 0\n"

#: The handler's barrier name, kept from ``handlers/main.yml``.
HANDLER_NAME = "restart gitlab"

#: The always-acted shells one loop seeds per host; the ``openssl`` seam key
#: is derived from the resolved plan, so ``seed`` adds it through
#: :func:`seed_openssl` instead of a literal here.
ACTION_SHELLS = (REPO_SHELL, RECONFIGURE_SHELL)

#: The three request names that queue the handler (D6).
CONFIGURE_NAMES = {
    "Copy GitLab configuration file",
    "Set GitLab configuration file mode and owner",
}


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side script fetch with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list
    of URLs the stub was called with, so a test can assert the fetch happened
    (and that a skipped host fetched nothing).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_SCRIPT

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(
    redhat_overrides: dict | None = None, **overrides
) -> list[dict]:
    """A three-host inventory: both target families and one off-target host.

    ``ubuntu`` runs the apt path, ``web1`` the RedHat/dnf path, and ``db1``
    (Alpine) proves the family guard. The role variables ride in each target
    entry's ``roles`` mapping under ``"gitlab"``, bound by ``resolve_vars``:
    ``overrides`` apply to the Ubuntu host, ``redhat_overrides`` to the
    RedHat one. With no overrides the upstream ``defaults/main.yml`` values
    apply on both: ``domain: gitlab`` derives the external URL, both
    certificate paths and the subject, ``version`` is empty (the candidate
    installs), and the self-signed certificate is created.
    """
    entry = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host", "apt_package_manager"],
        "roles": {
            "gitlab": {
            },
        },
    }
    entry["roles"]["gitlab"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
            "tags": ["redhat_host", "dnf_package_manager"],
            "roles": {"gitlab": dict(redhat_overrides or {})},
        },
        {
            "host": "db1",
            "family": "Alpine",
            "connection": {"address": "192.0.2.12", "username": "admin"},
            "group": "alpine",
        },
    ]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock minimal machine with no GitLab anywhere.

    Applied to both target families: the apt-side stores start empty (they
    answer nothing on the dnf host, whose ``packages`` store is its own), no
    config file or SSL directory exists, and all three action shells are
    canned as successes -- keyed by their exact ``sudo`` rendering (#16).
    The shell texts are family-independent: the spool path, the reconfigure
    command and (on the shared default domain) the openssl one-liner.
    """
    host.packages = {}
    host.apt_packages = {}
    host.apt_sources = []
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    host.services = {}
    host.enabled_services = set()
    for shell in ACTION_SHELLS:
        host.shell_results[shell] = {"exit_status": 0, "stdout": "", "stderr": ""}
    seed_openssl(host, gitlab_plan(GitlabVars(), "Ubuntu"))


def seed_openssl(host: SimulatedHost, plan: GitlabPlan, *, ok: bool = True) -> None:
    """Can the ``creates=``-gated certificate command for one plan."""
    host.shell_results[f"sudo {plan.openssl_command}"] = {
        "exit_status": 0 if ok else 1,
        "stdout": "",
        "stderr": "" if ok else "req: Error on request\n",
    }


def bridge(host: SimulatedHost, plan: GitlabPlan) -> None:
    """Simulate what the real first pass leaves behind for the fact barriers.

    The omnibus package owns ``/usr/bin/gitlab-ctl``, a successful bootstrap
    leaves ``/var/opt/gitlab/bootstrapped``, and ``openssl`` wrote the
    certificate -- none of which any simulation mutator records, so the second
    pass reads them through this explicit ``existing_files`` bridge.
    """
    host.existing_files.update({GITLAB_CTL_PATH, BOOTSTRAPPED_PATH, plan.ssl_certificate})


def make_hosts(inventory: list[dict]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeding targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") in TARGET_FAMILIES:
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict], hosts: dict[str, SimulatedHost], *, dry: bool = False
):
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(GitlabVars, "gitlab", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = gitlab(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report) -> dict[str, object]:
    """The ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def _kind(barrier) -> tuple:
    """Classify one barrier record for the shape assertion.

    All four barrier kinds this role uses are pinned -- ``fact`` probes,
    ``act`` shells, and the ``checkpoint`` notification read -- so a
    speculatively-added ``control`` (or a kind swap) breaks the shape test
    rather than hiding.
    """
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.name)
    if isinstance(barrier, ActionRequest):
        return ("act", barrier.name)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    return ("other", type(barrier).__name__)


def _barrier_shape(report, host: str = "ubuntu"):
    """One host's ordered ``(wave, kind)`` barrier list for a pass."""
    records = [r for r in report.records if r.host == host]
    return [(r.wave, _kind(r.barrier)) for r in records if r.barrier is not None]


def _request_waves(report, host: str = "ubuntu") -> set[int]:
    """The set of wave numbers that carried at least one request for ``host``."""
    return {
        r.wave for r in report.records if r.host == host and r.log is not None
    }


# --- the stream shape: host-dependent waves -----------------------------------


async def test_fresh_host_runs_the_whole_chain(canned_fetch):
    """The default fresh host emits all eight barriers in Ansible's order.

    Facts and acts interleave exactly as the task list reads: the install
    probe gates the repo pair, the bootstrap probe gates the tolerated first
    reconfigure, the certificate probe gates ``openssl``, and the checkpoint
    selects the handler host -- each barrier closing its own wave.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("fact", "Check if GitLab is already installed")),
        (1, ("act", "Install GitLab repository")),
        (2, ("fact", "Check if GitLab is already configured")),
        (3, ("act", "Reconfigure GitLab (first run)")),
        (4, ("fact", "Check if the self-signed certificate exists")),
        (5, ("act", "Create self-signed certificate")),
        (6, ("checkpoint", "configure done")),
        (7, ("act", HANDLER_NAME)),
    ]
    # Waves 3 and 5 carry only their act -- no request ever joins them.
    assert _request_waves(report) == {0, 1, 2, 4, 6}
    # Both target families run the fresh chain, so both config writes notify.
    assert "configure done → 2 hosts notified" in report.tree()
    assert f"barrier: {HANDLER_NAME} →" in report.tree()


async def test_converged_host_collapses_to_facts_and_a_checkpoint(canned_fetch):
    """The migration's idempotence claim: three quiet facts, one empty checkpoint.

    A host the first pass fully provisioned (bridge included) still runs every
    probe -- the barrier model has no request-side caching across passes -- but
    skips the repo block, the first reconfigure and ``openssl`` entirely, and
    the checkpoint notifies nobody, so no handler wave appears at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    bridge(host, gitlab_plan(GitlabVars(), "Ubuntu"))
    bridge(hosts["web1"], gitlab_plan(GitlabVars(), "RedHat"))
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert _barrier_shape(second) == [
        (0, ("fact", "Check if GitLab is already installed")),
        (1, ("fact", "Check if GitLab is already configured")),
        (2, ("fact", "Check if the self-signed certificate exists")),
        (3, ("checkpoint", "configure done")),
    ]
    assert _request_waves(second) == {0, 2, 3}
    assert "configure done → 0 hosts notified" in second.tree()
    # The four first-pass shells (script, first reconfigure, openssl, handler)
    # are the whole shell history: the rerun acts zero times.
    assert host.ran_shells == [
        REPO_SHELL,
        RECONFIGURE_SHELL,
        f"sudo {gitlab_plan(GitlabVars(), "Ubuntu").openssl_command}",
        RECONFIGURE_SHELL,
    ]


async def test_preinstalled_host_skips_repo_and_package_tasks(canned_fetch):
    """Task 3's ``when: not gitlab_installed.stat.exists`` as a fact gate.

    A host where GitLab is installed and bootstrapped but the certificate is
    missing never fetches the vendor script (the stub records the URLs) and
    never installs the package; the certificate act and the handler still run.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    plan = gitlab_plan(GitlabVars(), "Ubuntu")
    host.existing_files.update({GITLAB_CTL_PATH, BOOTSTRAPPED_PATH})

    report = await run_role(inventory, hosts)

    # The Ubuntu host is installed already, so only the RedHat target fetches
    # its vendor script -- the fact gate is per host, not per inventory.
    assert canned_fetch == [REPO_SCRIPT_URLS["RedHat"].format(edition="gitlab-ce")]
    assert REPO_SHELL not in host.ran_shells
    assert "Install GitLab" not in ubuntu_names(report)
    assert _barrier_shape(report) == [
        (0, ("fact", "Check if GitLab is already installed")),
        (1, ("fact", "Check if GitLab is already configured")),
        (2, ("fact", "Check if the self-signed certificate exists")),
        (3, ("act", "Create self-signed certificate")),
        (4, ("checkpoint", "configure done")),
        (5, ("act", HANDLER_NAME)),
    ]
    assert host.ran_shells == [f"sudo {plan.openssl_command}", RECONFIGURE_SHELL]


async def test_dry_preview_parks_at_the_install_probe(canned_fetch):
    """``dry=True`` never runs a barrier -- and the first barrier is a *fact*.

    Unlike nfs (whose first barrier is the checkpoint after every request),
    the gitlab body gates its middle waves on the ``/usr/bin/gitlab-ctl``
    probe, so the dry preview plans only the dependency install and parks:
    nothing is applied, no log line exists, and the tree names the probe as
    unknown until run.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.directory_stats == {}
    assert host.ran_shells == []
    assert canned_fetch == []
    assert changed_entries(report) == []
    assert _barrier_shape(report) == [
        (0, ("fact", "Check if GitLab is already installed"))
    ]
    assert _request_waves(report) == set()
    assert report.merged().log == []
    assert "Check if GitLab is already installed → unknown until run" in report.tree()


# --- the first pass really deploys the role ------------------------------------


async def test_first_pass_installs_repo_package_and_config(canned_fetch):
    """Every store the migration claims, on one fresh host in one pass.

    The dependencies plus ``gnupg2`` install (D4's merged task), the script
    downloads control-side and runs elevated, the *unpinned* edition installs,
    the ``0700`` SSL directory and the certificate appear, and the rendered
    ``gitlab.rb`` lands root:root ``0600`` -- changing, so the handler's
    ``gitlab-ctl reconfigure`` runs exactly once on top of the first run.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    plan = gitlab_plan(GitlabVars(), "Ubuntu")
    report = await run_role(inventory, hosts)

    # Dependencies merged with gnupg2, deduplicated; cache refreshed.
    assert "openssh-server" in host.apt_packages
    assert "postfix" in host.apt_packages
    assert GNUPG2_PACKAGE in host.apt_packages
    assert host.apt_update_stamp is not None
    # The edition installs unpinned on the default path (candidate).
    assert host.apt_packages["gitlab-ce"] == [""]

    # The vendor script: fetched control-side, spooled, installed verbatim.
    # Both targets fetch their own family's script.
    assert sorted(canned_fetch) == sorted(
        url.format(edition="gitlab-ce") for url in REPO_SCRIPT_URLS.values()
    )
    assert host.remote_files[REPO_SCRIPT_SPOOL] == FAKE_SCRIPT

    # The SSL directory, the omnibus way: 0700 root:root.
    assert host.directory_stats[SSL_DIR] == {
        "user": "root",
        "group": "root",
        "mode": 700,
    }

    # The rendered config: the derived defaults, root:root 0600.
    data = plan.config_content.encode()
    assert host.remote_files[CONFIG_PATH] == data
    assert host.file_stats[CONFIG_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 600,
        "size": len(data),
    }

    # ``files.install`` wrote content *and* mode, so the companion no-ops;
    # the config change queued the command handler.
    log = ubuntu_log(report)
    assert log["Copy GitLab configuration file"].changed
    assert log["Set GitLab configuration file mode and owner"].changed is False
    assert host.ran_shells == [
        REPO_SHELL,
        RECONFIGURE_SHELL,
        f"sudo {plan.openssl_command}",
        RECONFIGURE_SHELL,
    ]


async def test_version_pin_installs_the_pinned_package(canned_fetch):
    """Tasks 8-10's ``set_fact``: ``gitlab-ce=17.5.1-ce.0`` on the apt seam.

    The Debian ``=`` separator (D2) reaches ``apt-get install`` verbatim, and
    the simulation records exactly that version, so a rerun (bridged) finds
    the pin satisfied.
    """
    inventory = make_inventory(version="17.5.1-ce.0")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    assert host.apt_packages["gitlab-ce"] == ["17.5.1-ce.0"]


async def test_redhat_host_runs_the_dnf_path(canned_fetch):
    """The RedHat half of the family dispatch: dnf stores, rpm script, no gnupg2.

    ``web1`` runs the same eight-barrier chain as ``ubuntu``, but both package
    requests go through ``dnf.packages`` (no folded apt cache refresh -- the
    upstream role refreshes nothing on RedHat), the vendor script is
    ``script.rpm.sh`` and the Debian-only ``gnupg2`` never enters the
    dependency list. The pin grammar is the shared ``=`` spelling: the dnf
    store records the ``.el8`` version exactly as given.
    """
    inventory = make_inventory(redhat_overrides={"version": "17.11.7-ce.0.el8"})
    hosts = make_hosts(inventory)
    host = hosts["web1"]
    plan = gitlab_plan(GitlabVars(version="17.11.7-ce.0.el8"), "RedHat")
    report = await run_role(inventory, hosts)

    assert REPO_SCRIPT_URLS["RedHat"].format(edition="gitlab-ce") in canned_fetch
    assert host.packages["openssh-server"] == [""]
    assert GNUPG2_PACKAGE not in host.packages
    assert host.packages["gitlab-ce"] == ["17.11.7-ce.0.el8"]
    # No apt movement on the dnf host: the cache refresh stays on the Ubuntu half.
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    # The family-independent stages are identical: same spool, shells, config.
    assert host.remote_files[REPO_SCRIPT_SPOOL] == FAKE_SCRIPT
    assert host.ran_shells == [
        REPO_SHELL,
        RECONFIGURE_SHELL,
        f"sudo {plan.openssl_command}",
        RECONFIGURE_SHELL,
    ]
    log = {entry.name: entry for entry in report.merged().log if entry.host == "web1"}
    assert log["Copy GitLab configuration file"].changed
    assert host.remote_files[CONFIG_PATH] == plan.config_content.encode()
    assert _barrier_shape(report, "web1") == _barrier_shape(report, "ubuntu")


async def test_self_signed_certificate_skipped_when_disabled(canned_fetch):
    """The ``gitlab_create_self_signed_cert`` gate: no directory, no probe, no act.

    With the flag off, tasks 11-12 drop out of the stream entirely -- the
    certificate fact disappears from the barrier shape -- while the derived
    ``nginx['ssl_certificate']`` lines still render (upstream behaviour: the
    template carries the paths regardless).
    """
    inventory = make_inventory(create_self_signed_cert=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("fact", "Check if GitLab is already installed")),
        (1, ("act", "Install GitLab repository")),
        (2, ("fact", "Check if GitLab is already configured")),
        (3, ("act", "Reconfigure GitLab (first run)")),
        (4, ("checkpoint", "configure done")),
        (5, ("act", HANDLER_NAME)),
    ]
    assert SSL_DIR not in host.directory_stats
    assert not any(shell.startswith("sudo openssl") for shell in host.ran_shells)
    config = host.remote_files[CONFIG_PATH].decode()
    assert "nginx['ssl_certificate'] = \"/etc/gitlab/ssl/gitlab.crt\"" in config


async def test_non_target_host_is_never_touched(canned_fetch):
    """Alpine has no upstream branch, so the family guard never contacts it.

    The RedHat entry is now a target (it shares the fresh chain with the
    Ubuntu host); the Alpine entry is the guard's proof: no wave, no log line,
    no barrier, no store movement.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["db1"]

    # The family guard returns before the first yield, so the off-target host is
    # in no wave, emits no log line, touches no barrier and its stores never move.
    assert not any(entry.host == "db1" for entry in report.merged().log)
    assert not any(r.host == "db1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.packages == {}
    assert skipped.apt_packages == {}
    assert skipped.ran_shells == []
    # The RedHat target, by contrast, runs the same eight-barrier chain.
    assert _barrier_shape(report, "web1") == _barrier_shape(report, "ubuntu")


# --- the notification paths -----------------------------------------------------


async def test_content_drift_rewrites_and_notifies(canned_fetch):
    """Stale ``gitlab.rb``: the install request rewrites and queues the reconfigure."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    plan = gitlab_plan(GitlabVars(), "Ubuntu")
    await run_role(inventory, hosts)
    bridge(host, plan)
    bridge(hosts["web1"], gitlab_plan(GitlabVars(), "RedHat"))

    host.remote_files[CONFIG_PATH] = b"external_url \"https://hand.edited/\"\n"

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy GitLab configuration file"].changed
    assert host.remote_files[CONFIG_PATH] == plan.config_content.encode()
    # The first pass ran the script + two reconfigures + openssl; this one only
    # the handler's.
    assert host.ran_shells.count(RECONFIGURE_SHELL) == 3

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_metadata_drift_converges_and_notifies(canned_fetch):
    """Right content, wrong mode and owner: the companion converges, then notifies.

    ``files.install`` is content-idempotent, so the install request stays quiet;
    the paired ``files.file`` re-applies ``0600 root:root`` -- and its name
    notifies, because Ansible's template reports a change on owner/group/mode
    drift too (D6). The read-back of the root-owned file is served by the
    elevated tier (issue #69), so only the metadata command is emitted.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    plan = gitlab_plan(GitlabVars(), "Ubuntu")
    data = plan.config_content.encode()
    # Pre-state: the exact intended content, drifted ownership and mode.
    host.remote_files[CONFIG_PATH] = data
    host.existing_files.add(CONFIG_PATH)
    host.file_stats[CONFIG_PATH] = {
        "user": "gitlab-www",
        "group": "gitlab-www",
        "mode": 644,
        "size": len(data),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy GitLab configuration file"].changed is False
    assert log["Set GitLab configuration file mode and owner"].changed
    assert host.file_stats[CONFIG_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 600,
        "size": len(data),
    }
    # The metadata-only drift still queued the command handler.
    assert host.ran_shells == [
        REPO_SHELL,
        RECONFIGURE_SHELL,
        f"sudo {plan.openssl_command}",
        RECONFIGURE_SHELL,
    ]


def test_only_the_configuration_requests_notify():
    """The role's one ``notify:`` sits on the template task; the companion joins it.

    Nothing else in the stream -- not the dependency install, not the vendor
    repo, not the SSL directory -- may queue ``restart gitlab``.
    """
    assert NOTIFYING == frozenset(CONFIGURE_NAMES)


# --- the act failures ------------------------------------------------------------


async def test_repo_script_failure_raises(canned_fetch):
    """Task 7's non-zero exit is data; the body turns it into the typed error."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].shell_results[REPO_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "apt-get: repository not found\n",
    }

    with pytest.raises(CommandError, match=REPO_SCRIPT_SPOOL):
        await run_role(inventory, hosts)


async def test_first_run_reconfigure_failure_is_tolerated(canned_fetch):
    """Task 10's ``failed_when: false`` (D7): the unchecked act never raises.

    The canned failure hits *both* reconfigure runs (same seam key) and the
    default handler tolerates its exit too (D8), so the whole pass completes.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.shell_results[RECONFIGURE_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "gitlab-ctl: run was not successful\n",
    }

    report = await run_role(inventory, hosts)

    assert host.ran_shells.count(RECONFIGURE_SHELL) == 2
    assert ubuntu_log(report)["Copy GitLab configuration file"].changed


async def test_openssl_failure_raises(canned_fetch):
    """Task 12's default ``failed_when: true`` -- as the body's own raise."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    plan = gitlab_plan(GitlabVars(), "Ubuntu")
    hosts["ubuntu"].shell_results[f"sudo {plan.openssl_command}"] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "req: Error on request\n",
    }

    with pytest.raises(CommandError, match="openssl req"):
        await run_role(inventory, hosts)


async def test_handler_failure_raises_only_with_the_flag(canned_fetch):
    """D8: ``restart_handler_failed_when=True`` makes the handler's exit fatal.

    Upstream's ``failed_when`` string coerces to ``False`` (the default test
    above proves the tolerated path); the bool field opts into the raise.
    """
    inventory = make_inventory(restart_handler_failed_when=True)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.shell_results[RECONFIGURE_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "gitlab-ctl: run was not successful\n",
    }

    with pytest.raises(CommandError, match=RECONFIGURE_COMMAND):
        await run_role(inventory, hosts)

    # The whole fresh chain ran before the raise: script, first-run act (same
    # seam key, unchecked -- D7), openssl, and the fatal handler reconfigure.
    plan = gitlab_plan(GitlabVars(), "Ubuntu")
    assert host.ran_shells == [
        REPO_SHELL,
        RECONFIGURE_SHELL,
        f"sudo {plan.openssl_command}",
        RECONFIGURE_SHELL,
    ]


# --- pure helpers -----------------------------------------------------------------


def test_package_pin_default_and_pinned():
    assert package_pin(GitlabVars()) == ("gitlab-ce",)
    assert package_pin(GitlabVars(version="17.5.1-ce.0")) == ("gitlab-ce=17.5.1-ce.0",)
    # The edition feeds both the pin name and the repo URL.
    pin = package_pin(GitlabVars(edition="gitlab-ee", version="15.0.0-ee.0"))
    assert pin == ("gitlab-ee=15.0.0-ee.0",)


def test_openssl_command_quotes_the_variable_parts():
    """The subject carries spaces and the paths come from ``gitlab_domain``."""
    command = openssl_command(
        "/C=US/ST=Missouri/L=Saint Louis/O=IT/CN=two words",
        "/etc/gitlab/ssl/two words.crt",
        "/etc/gitlab/ssl/two words.key",
    )
    assert command.startswith("openssl req -new -nodes -x509 ")
    assert "-subj '/C=US/ST=Missouri/L=Saint Louis/O=IT/CN=two words'" in command
    assert "-keyout '/etc/gitlab/ssl/two words.key'" in command
    assert "-out '/etc/gitlab/ssl/two words.crt'" in command
    assert command.endswith("-extensions v3_ca")
    # Every interpolated piece survives ``shlex`` parsing intact.
    parts = shlex.split(command)
    assert parts[parts.index("-subj") + 1] == (
        "/C=US/ST=Missouri/L=Saint Louis/O=IT/CN=two words"
    )


def test_extra_settings_context_drops_unset_type():
    """``model_dump(exclude_none=True)`` keeps ``kv.type is defined`` honest."""
    context = extra_settings_context(
        (
            {
                "gitlab_rails": (
                    GitlabExtraOption(key="artifact_enabled", value="true", type="plain"),
                    GitlabExtraOption(key="contact_email", value="git@example.com"),
                )
            },
        )
    )
    assert context == [
        {
            "gitlab_rails": [
                {"key": "artifact_enabled", "value": "true", "type": "plain"},
                {"key": "contact_email", "value": "git@example.com"},
            ]
        }
    ]


def test_gitlab_plan_derives_the_interpolated_defaults():
    """The four ``defaults/main.yml`` interpolations live in :func:`gitlab_plan`."""
    plan = gitlab_plan(GitlabVars(domain="gitlab.example.com"), "Ubuntu")
    assert plan.external_url == "https://gitlab.example.com/"
    assert plan.ssl_certificate == "/etc/gitlab/ssl/gitlab.example.com.crt"
    assert plan.ssl_certificate_key == "/etc/gitlab/ssl/gitlab.example.com.key"
    assert plan.cert_subj.endswith("/CN=gitlab.example.com")
    assert plan.script_url == (
        "https://packages.gitlab.com/install/repositories/gitlab/gitlab-ce/script.deb.sh"
    )
    # D4: the dependencies plus gnupg2, deduplicated, order preserved.
    assert plan.dependencies == (
        "openssh-server",
        "postfix",
        "curl",
        "openssl",
        "tzdata",
        GNUPG2_PACKAGE,
    )
    assert plan.dependencies == tuple(dict.fromkeys(plan.dependencies))
    # The RedHat plan differs only in the family-derived pieces: the rpm
    # script URL, and no Debian-only gnupg2.
    rh = gitlab_plan(GitlabVars(domain="gitlab.example.com"), "RedHat")
    assert rh.family == "RedHat"
    assert rh.script_url == (
        "https://packages.gitlab.com/install/repositories/gitlab/"
        "gitlab-ce/script.rpm.sh"
    )
    assert rh.dependencies == (
        "openssh-server",
        "postfix",
        "curl",
        "openssl",
        "tzdata",
    )
    assert rh.package_pin == plan.package_pin


def test_gitlab_plan_explicit_overrides_beat_the_derivation():
    """Upstream's variables are independent: one path set, the other derived."""
    plan = gitlab_plan(
        GitlabVars(domain="gitlab", ssl_certificate="/etc/ssl/custom.crt"), "Ubuntu"
    )
    assert plan.ssl_certificate == "/etc/ssl/custom.crt"
    assert plan.ssl_certificate_key == "/etc/gitlab/ssl/gitlab.key"
    assert plan.external_url == "https://gitlab/"


def test_render_defaults_omit_every_gated_block():
    """The untouched template gates stay absent, exactly as undefined upstream.

    D12: the two ``is defined`` knobs and ``gitlab_extra_settings`` never enter
    the context, so their blocks disappear; every ``| lower`` renders ``false``.
    """
    hv = GitlabVars()
    plan = gitlab_plan(hv, "Ubuntu")
    config = render_gitlab_rb(
        hv, plan.external_url, plan.ssl_certificate, plan.ssl_certificate_key
    )
    assert 'external_url "https://gitlab/"' in config
    assert "gitlab_rails['time_zone'] = \"UTC\"" in config
    assert "gitlab_rails['gitlab_email_enabled'] = false" in config
    assert "nginx['redirect_http_to_https'] = true" in config
    assert "letsencrypt['enable'] = false" in config
    assert "gitlab_rails['ldap_enabled'] = false" in config
    assert "gitlab_rails['smtp_enable'] = false" in config
    assert "registry['enable'] = false" in config
    assert "nginx['listen_port']" not in config
    assert "nginx['listen_https']" not in config
    assert "# Extra configuration" not in config
    assert 'git_data_dirs({"default" => {"path" => "/var/opt/gitlab/git-data"} })' in config


def test_render_overrides_emit_every_block():
    """Every optional template block, switched on at once and checked line-wise."""
    hv = GitlabVars(
        domain="gitlab",
        email_enabled=True,
        email_from="git@gitlab.example.com",
        ldap_enabled=True,
        smtp_enable=True,
        registry_enable=True,
        letsencrypt_enable=True,
        letsencrypt_contact_emails=("admin@gitlab.example.com", "ops@gitlab.example.com"),
        nginx_listen_port=8080,
        nginx_listen_https=False,
        nginx_ssl_verify_client="on",
        nginx_ssl_client_certificate="/etc/gitlab/ssl/ca.crt",
        extra_settings=(
            {
                "gitlab_rails": (
                    GitlabExtraOption(
                        key="artifact_enabled", value="true", type="plain"
                    ),
                    GitlabExtraOption(key="time_zone", value="UTC"),
                    GitlabExtraOption(key="authorized_keys", value=["a", "b"]),
                )
            },
        ),
    )
    plan = gitlab_plan(hv, "Ubuntu")
    config = render_gitlab_rb(
        hv, plan.external_url, plan.ssl_certificate, plan.ssl_certificate_key
    )

    assert "gitlab_rails['gitlab_email_from'] = \"git@gitlab.example.com\"" in config
    assert "gitlab_rails['ldap_enabled'] = true" in config
    assert "gitlab_rails['ldap_port'] = 389" in config
    assert "gitlab_rails['smtp_enable'] = true" in config
    assert "registry['enable'] = true" in config
    assert (
        "letsencrypt['contact_emails'] = "
        '[\"admin@gitlab.example.com\", \"ops@gitlab.example.com\"]' in config
    )
    assert "nginx['listen_port'] = \"8080\"" in config
    assert "nginx['listen_https'] = false" in config
    assert "nginx['ssl_verify_client'] = \"on\"" in config
    # The extra-settings triple loop: plain, quoted and non-string renderings.
    assert "gitlab_rails['artifact_enabled'] = true" in config
    assert "gitlab_rails['time_zone'] = 'UTC'" in config
    assert "gitlab_rails['authorized_keys'] = ['a', 'b']" in config


def test_custom_config_template_is_read_controller_side(tmp_path):
    """D11: a non-bundled ``config_template`` is a path on the *controller*."""
    template = tmp_path / "custom.rb.j2"
    template.write_text('external_url "{{ gitlab_external_url }}"\n', encoding="utf-8")
    hv = GitlabVars(domain="corp", config_template=str(template))
    plan = gitlab_plan(hv, "Ubuntu")
    assert plan.config_content == 'external_url "https://corp/"\n'


# --- variables ---------------------------------------------------------------------


def test_model_is_frozen_and_hashable():
    hv = GitlabVars()
    with pytest.raises(ValidationError):
        hv.domain = "other"  # type: ignore[misc]
    assert hash(hv) == hash(GitlabVars())


@pytest.mark.parametrize(
    "overrides",
    [
        {"domain": ""},
        {"edition": ""},
        {"extra_settings": ({"gitlab_rails": ({"value": "no key"},)},)},
    ],
)
def test_invalid_role_values_fail_fast(overrides):
    with pytest.raises(ValidationError):
        GitlabVars(**overrides)


async def test_inventory_role_variables_bind_and_typos_raise():
    inventory = make_inventory(
        domain="gitlab.example.com",
        version="17.5.1-ce.0",
        dependencies=["curl", "tzdata"],
        extra_settings=[{"gitlab_rails": [{"key": "time_zone", "value": "UTC"}]}],
    )
    v = resolve_vars(GitlabVars, "gitlab", inventory)
    host = SimulatedHost.from_inventory(inventory[0])
    hv = v.for_host(host)

    assert hv.domain == "gitlab.example.com"
    assert hv.version == "17.5.1-ce.0"
    # The list normalizes to a tuple of models; the section dict stays a dict.
    assert hv.dependencies == ("curl", "tzdata")
    assert isinstance(hv.extra_settings[0]["gitlab_rails"], tuple)
    assert hv.extra_settings[0]["gitlab_rails"][0].value == "UTC"
    # The dicts only exist on the control side (the render consumes them), so
    # no unhashable value ever reaches an ``op()`` argument -- proven below by
    # running the whole deploy with exactly these variables.

    # A typo in the inventory mapping is a RoleVarsError before any host contact.
    with pytest.raises(RoleVarsError):
        resolve_vars(GitlabVars, "gitlab", make_inventory(domian="typo"))


async def test_download_validate_certs_is_carried_but_inert(canned_fetch):
    """D10: the flag changes nothing -- the fetch is control-side, not per-request."""
    inventory = make_inventory(download_validate_certs=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert sorted(canned_fetch) == sorted(
        url.format(edition="gitlab-ce") for url in REPO_SCRIPT_URLS.values()
    )
    assert _barrier_shape(report)[:2] == [
        (0, ("fact", "Check if GitLab is already installed")),
        (1, ("act", "Install GitLab repository")),
    ]


async def test_extra_settings_from_inventory_reach_the_config_file(canned_fetch):
    """The whole channel: JSON nested lists -> models -> template -> host."""
    inventory = make_inventory(
        extra_settings=[
            {"gitlab_shell": [{"key": "git_users_path", "value": "gitlab-users"}]}
        ]
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    config = host.remote_files[CONFIG_PATH].decode()
    assert "gitlab_shell['git_users_path'] = 'gitlab-users'" in config
