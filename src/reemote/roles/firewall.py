# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The firewall role (migrated from ``geerlingguy.firewall``).
#
# Installs and configures an iptables firewall on Ubuntu hosts: the ``iptables``
# package, the once-per-machine flush the role gated on ``creates=``, the
# rendered ``/etc/firewall.bash`` script and its ``0744`` ownership, the
# ``firewall.service`` systemd unit, the service's running/enabled state, and
# the role's single ``restart firewall`` handler. :func:`firewall` is the whole
# task list, and :class:`FirewallVars` is ``defaults/main.yml``, bound from the
# inventory entry's ``firewall`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# Target: hosts in the ``"Ubuntu"`` OS family. The role's RedHat/firewalld and
# Arch branches and its init-script fork are out of scope, so its
# ``ansible_facts`` dispatch collapses to apt, systemd and the two frozen
# templates below.
#
# The stream carries three waves on a converged host, four on a first run,
# five when the handler fires (design doc section 5.7 -- the wave count is
# host-dependent because two of the barriers are conditional):
#
# 0. :func:`install` -- the ``iptables`` package -- closed by the
#    ``fact(FileExists, path=/etc/firewall.bash)`` barrier, which is the role's
#    ``creates=`` gate and sees the file the *previous* converge left behind.
# 1. (first run only) the ``iptables -F`` flush, an :func:`~reemote.stages.act`
#    barrier: the role ran it exactly once per machine, before the script that
#    replaces the rule set existed.
# 2. :func:`configure` -- the rendered script, its mode companion, the unit
#    file, the service state -- closed by ``checkpoint("configure done")``.
# 3. :func:`handler` -- ``Restart firewall (handler)``, only on the hosts that
#    checkpoint selected.
#
# The three reads the role takes *between* its tasks -- the ``creates=`` stat,
# the flush outcome and the handler notification -- are barriers now, so the
# script is rendered control-side from the variables alone and written with the
# spool-and-install family, and no name set is unioned across the inventory:
# the per-host body *is* the plan.
#
# The role's mechanism is a bash script that rebuilds the whole rule set, and
# :func:`firewall` keeps it: the additional rules are raw shell strings the
# script splices in verbatim, so an ``iptables.rule``-per-port translation is
# not faithful (see D7). A composing role gets no outputs -- nothing in the
# corpus consumes any -- so the body returns ``None`` on every path (design doc
# section 18.2).
#
# Deviations from the Ansible role, all deliberate:
#
# * **D1** the host-family guard is the body's opening ``if``, not a ``when=``
#   on every request: a host outside the ``"Ubuntu"`` family returns before it
#   yields anything, so it emits no requests and no log entries at all. The
#   role's generic ``package:``/``service:`` modules collapse to apt and
#   systemd, which is what they resolve to on the target anyway.
# * **D2** the init-script fork (``when: ansible_facts.service_mgr !=
#   'systemd'``) is dropped: the target family ships systemd, so the unit-file
#   task is unconditional here. A multi-init target would select between the
#   two with ``fact(server.InitSystem)``.
# * **D3** the opt-in ``disable-other-firewalls.yml`` import is out of scope by
#   agreement with the operator: :attr:`FirewallVars.disable_firewalld` and
#   :attr:`FirewallVars.disable_ufw` are carried as variables and do nothing
#   here, so an existing inventory still binds (the apache role's
#   ``enablerepo`` precedent).
# * **D4** the flush task's ``creates=/etc/firewall.bash`` gate becomes an
#   in-stream ``fact(FileExists)`` barrier, and the flush itself an ``act``
#   barrier whose non-zero exit the body raises as :class:`CommandError` -- the
#   role ran the play with ``become:``, hence ``sudo=True``. As a command the
#   flush has no reemote operation, and the barrier model is exactly the place
#   for a read the next wave depends on.
# * **D5** ``daemon_reload=True`` is explicit on the service request and on the
#   handler: Ansible's ``systemd`` module reloads the daemon implicitly when
#   unit files changed, and reemote asks (issue #50). The flag is state-gated,
#   so a converged run issues no reload and the pass stays a no-op.
# * **D6** a custom :attr:`FirewallVars.template` names a file on the
#   *controller*, not in a role directory on the host -- the bundled template
#   is the constant below (apache's ``vhosts_template`` precedent).
# * **D7** the rule set is managed the way the role manages it -- by rewriting
#   ``/etc/firewall.bash`` and restarting the unit -- not with per-port
#   ``iptables.rule`` operations. ``firewall_additional_rules`` are raw shell
#   strings that only the script can splice, and an operation-level
#   ``iptables.flush`` would wipe a converged firewall on every run. The
#   handler therefore does what the role's does: re-executes the script via
#   ``systemctl restart firewall``.
# * the handler is per-host and stateless: ``restarted`` always reports a
#   change, faithful to Ansible's service module, and an empty notified set
#   yields no request -- the converged no-op.
# * the role's implicit variable gates become pydantic validation:
#   :attr:`FirewallVars.state` is a :data:`~typing.Literal`, ports bind from
#   ints or strings, and a malformed forwarded-port entry raises
#   ``ValidationError`` (a ``ValueError``) before any host is contacted.
# """

from collections.abc import Generator
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..actions import Shell
from ..core import op
from ..handlers import changed_hosts, service_handler
from ..hosts import CommandError, Host
from ..modules import apt, files, systemd
from ..modules.files import FileExists
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, act, checkpoint, fact

# --- role constants (defaults/main.yml + tasks/main.yml, Ubuntu path only) ---

#: ``tasks/main.yml``'s one package. The role's generic ``package:`` resolves
#: to apt on the target family.
IPTABLES_PACKAGES = ("iptables",)

#: The script the role renders and the unit that runs it -- ``dest:`` values,
#: verbatim. The script is also the flush task's ``creates=`` gate (D4).
FIREWALL_SCRIPT = "/etc/firewall.bash"
FIREWALL_UNIT = "/etc/systemd/system/firewall.service"

#: The flush command, verbatim (the ``command:`` task takes argv, not a shell).
FLUSH_COMMAND = "iptables -F"

#: ``service: name=firewall`` -- the unit the templates define and the handler
#: and final task act on.
SERVICE_NAME = "firewall"

#: The two ``mode:`` arguments of the role's template tasks.
SCRIPT_MODE = "0744"
UNIT_MODE = "0644"

#: The name the role's bundled ``templates/firewall.bash.j2`` is identified by;
#: anything else in :attr:`FirewallVars.template` is a controller-side path.
BUNDLED_TEMPLATE = "firewall.bash.j2"

#: ``templates/firewall.bash.j2``, verbatim. Rendered control-side by
#: :func:`render_firewall_script` and written with :func:`~reemote.modules.files.install`
#: (reemote has no template engine and a deploy body has no business reading a
#: host file). Only loop and if tags are used, so the plain
#: :func:`~reemote.roles._render.jinja2_environment` with Ansible's whitespace
#: defaults reproduces the role's output.
SCRIPT_TEMPLATE = """#!/bin/bash
# iptables firewall.
#
# This file should be located at /etc/firewall.bash, and is meant to work with
# the `geerlingguy.firewall` Ansible role.
#
# Common port reference:
#   22: SSH
#   25: SMTP
#   80: HTTP
#   123: NTP
#   443: HTTPS
#   2222: SSH alternate
#   8080: HTTP alternate
#
# @author Jeff Geerling

# No spoofing.
if [ -e /proc/sys/net/ipv4/conf/all/rp_filter ]
then
for filter in /proc/sys/net/ipv4/conf/*/rp_filter
do
echo 1 > $filter
done
fi

# Set the default rules.
iptables -P INPUT ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT ACCEPT

{% if firewall_flush_rules_and_chains %}
# Remove all rules and chains.
iptables -t nat -F
iptables -t mangle -F
iptables -F
iptables -X
{% endif %}

# Accept traffic from loopback interface (localhost).
iptables -A INPUT -i lo -j ACCEPT

# Forwarded ports.
{# Add a rule for each forwarded port #}
{% for forwarded_port in firewall_forwarded_tcp_ports %}
iptables -t nat -I PREROUTING -p tcp --dport {{ forwarded_port.src }} -j REDIRECT --to-port {{ forwarded_port.dest }}
iptables -t nat -I OUTPUT -p tcp -o lo --dport {{ forwarded_port.src }} -j REDIRECT --to-port {{ forwarded_port.dest }}
{% endfor %}
{% for forwarded_port in firewall_forwarded_udp_ports %}
iptables -t nat -I PREROUTING -p udp --dport {{ forwarded_port.src }} -j REDIRECT --to-port {{ forwarded_port.dest }}
iptables -t nat -I OUTPUT -p udp -o lo --dport {{ forwarded_port.src }} -j REDIRECT --to-port {{ forwarded_port.dest }}
{% endfor %}

# Open ports.
{# Add a rule for each open port #}
{% for port in firewall_allowed_tcp_ports %}
iptables -A INPUT -p tcp -m tcp --dport {{ port }} -j ACCEPT
{% endfor %}
{% for port in firewall_allowed_udp_ports %}
iptables -A INPUT -p udp -m udp --dport {{ port }} -j ACCEPT
{% endfor %}

# Accept icmp ping requests.
iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT

# Allow NTP traffic for time synchronization.
iptables -A OUTPUT -p udp --dport 123 -j ACCEPT
iptables -A INPUT -p udp --sport 123 -j ACCEPT

# Additional custom rules.
{% for rule in firewall_additional_rules %}
{{ rule }}
{% endfor %}

# Allow established connections:
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Log EVERYTHING (ONLY for Debug).
# iptables -A INPUT -j LOG

{% if firewall_log_dropped_packets %}
# Log other incoming requests (all of which are dropped) at 15/minute max.
iptables -A INPUT -m limit --limit 15/minute -j LOG --log-level 7 --log-prefix "Dropped by firewall: "
{% endif %}

# Drop all other traffic.
iptables -A INPUT -j DROP

{% if firewall_enable_ipv6 %}
# Configure IPv6 if ip6tables is present.
if [ -x "$(which ip6tables 2>/dev/null)" ]; then

{% if firewall_flush_rules_and_chains %}
  # Remove all rules and chains.
  ip6tables -F
  ip6tables -X
{% endif %}

  # Accept traffic from loopback interface (localhost).
  ip6tables -A INPUT -i lo -j ACCEPT

  # Open ports.
{# Add a rule for each open port #}
{% for port in firewall_allowed_tcp_ports %}
  ip6tables -A INPUT -p tcp -m tcp --dport {{ port }} -j ACCEPT
{% endfor %}
{% for port in firewall_allowed_udp_ports %}
  ip6tables -A INPUT -p udp -m udp --dport {{ port }} -j ACCEPT
{% endfor %}

  # Accept ICMPv6 message types necessary for SLAAC plus echo request/reply
  ip6tables -A INPUT -p ipv6-icmp --icmpv6-type 128 -j ACCEPT  # Echo Request
  ip6tables -A INPUT -p ipv6-icmp --icmpv6-type 129 -j ACCEPT  # Echo Reply
  ip6tables -A INPUT -p ipv6-icmp --icmpv6-type 133 -j ACCEPT  # Router Solicitation
  ip6tables -A INPUT -p ipv6-icmp --icmpv6-type 134 -j ACCEPT  # Router Advertisement
  ip6tables -A INPUT -p ipv6-icmp --icmpv6-type 135 -j ACCEPT  # Neighbor Solicitation
  ip6tables -A INPUT -p ipv6-icmp --icmpv6-type 136 -j ACCEPT  # Neighbor Advertisement

  # Allow NTP traffic for time synchronization.
  ip6tables -A OUTPUT -p udp --dport 123 -j ACCEPT
  ip6tables -A INPUT -p udp --sport 123 -j ACCEPT

  # Additional custom rules.
{% for rule in firewall_ip6_additional_rules %}
  {{ rule }}
{% endfor %}

  # Allow established connections:
  ip6tables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

  # Log EVERYTHING (ONLY for Debug).
  # ip6tables -A INPUT -j LOG

{% if firewall_log_dropped_packets %}
  # Log other incoming requests (all of which are dropped) at 15/minute max.
  ip6tables -A INPUT -m limit --limit 15/minute -j LOG --log-level 7 --log-prefix "Dropped by firewall: "
{% endif %}

  # Drop all other traffic.
  ip6tables -A INPUT -j DROP

fi
{% endif %}
"""

#: ``templates/firewall.unit.j2``, verbatim. The template carries no Jinja
#: variable, so this is a plain constant rather than something to render: the
#: role's ``template:`` task writes this exact text on every systemd host.
#: ``firewall.bash.j2`` owns the rule set; the unit only starts it at boot.
UNIT_FILE = """[Unit]
Description=Firewall
After=syslog.target network.target

[Service]
Type=oneshot
ExecStart=/etc/firewall.bash
ExecStop=/sbin/iptables -F
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

#: The two requests whose change queues ``restart firewall`` -- the role's one
#: ``notify:``, widened to the metadata companion the install's content-only
#: idempotence needs (the apache ports.conf pair). The unit-file task and the
#: service task carry no ``notify`` in the role, so they notify nothing here.
NOTIFYING = (
    "Copy firewall script into place",
    "Set firewall script mode",
)


# --- variables ---------------------------------------------------------------


class ForwardedPort(BaseModel):
    """One ``firewall_forwarded_tcp_ports``/``_udp_ports`` item.

    The role's template reads exactly ``.src`` and ``.dest``, so those are the
    declared fields; ``extra="allow"`` keeps any further key an inventory
    carries (the apache ``VHost`` precedent) and the model is frozen so the
    containing :class:`FirewallVars` stays hashable.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    #: ``forwarded_port.src``: the incoming port to redirect from.
    src: str
    #: ``forwarded_port.dest``: the local port to redirect to.
    dest: str

    @field_validator("src", "dest", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> Any:
        """An unquoted YAML port is an int; the role's template rendered either."""
        return str(value) if not isinstance(value, str) else value

    @field_validator("src", "dest")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """An empty port is a typo, and the script rule it feeds is invalid."""
        if not value:
            raise ValueError("a forwarded port needs both src and dest")
        return value


class FirewallVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``firewall`` role mapping, so ``allowed_tcp_ports`` there
    sets :attr:`allowed_tcp_ports`. Every Ansible default is carried over,
    including the two inert ones (:attr:`disable_firewalld`,
    :attr:`disable_ufw`, see D3) so such a key still binds.

    Sequences are ``tuple``, never ``list``: the model is frozen and hashable,
    and a value that reaches :func:`~reemote.core.op` must hash (design doc
    section 8.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``firewall_state``: what the final service task asks for.
    state: Literal["started", "stopped", "restarted", "reloaded"] = "started"
    #: ``firewall_enabled_at_boot``.
    enabled: bool = True
    #: ``firewall_flush_rules_and_chains``: the script's reset-to-defaults block.
    flush_rules_and_chains: bool = True
    #: ``firewall.bash.j2`` is the bundled template; anything else is read from
    #: the controller's filesystem by :func:`render_firewall_script` (D6).
    template: str = BUNDLED_TEMPLATE
    #: ``firewall_allowed_tcp_ports``, the role's four defaults.
    allowed_tcp_ports: tuple[str, ...] = ("22", "25", "80", "443")
    allowed_udp_ports: tuple[str, ...] = ()
    forwarded_tcp_ports: tuple[ForwardedPort, ...] = ()
    forwarded_udp_ports: tuple[ForwardedPort, ...] = ()
    #: ``firewall_additional_rules``: raw shell lines the script splices in
    #: verbatim between the opens and the established rule (see D7).
    additional_rules: tuple[str, ...] = ()
    #: ``firewall_enable_ipv6``: the script's ``ip6tables`` block.
    enable_ipv6: bool = True
    ip6_additional_rules: tuple[str, ...] = ()
    #: ``firewall_log_dropped_packets``: the 15/minute LOG rule.
    log_dropped_packets: bool = True
    #: Out of scope by agreement (D3): kept so an existing inventory binds.
    disable_firewalld: bool = False
    disable_ufw: bool = False

    @field_validator("allowed_tcp_ports", "allowed_udp_ports", mode="before")
    @classmethod
    def _stringify_ports(cls, value: Any) -> Any:
        """YAML may carry the ports unquoted; the template renders either."""
        return tuple(str(item) for item in value) if isinstance(value, (list, tuple)) else value


# --- pure helpers ------------------------------------------------------------


def render_firewall_script(v: FirewallVars) -> str:
    """``templates/firewall.bash.j2``, pre-rendered control-side.

    The context is the role's own ``firewall_``-prefixed variable set -- every
    variable, including the three the bundled template does not read, so a
    custom template under :data:`StrictUndefined` sees what Ansible would give
    it. Forwarded-port entries are dumped as plain dicts, which is the shape
    the template's ``forwarded_port.src`` attribute reads resolves to.

    A custom ``template`` is a controller-side path, read here rather than on
    the host; the bundled template needs no filesystem read (D6). The jinja2
    import is inside this function so that importing ``reemote.roles`` stays
    clean without the ``reemote[templates]`` extra (§17.4).
    """
    from ._render import jinja2_environment

    template_text = (
        SCRIPT_TEMPLATE
        if v.template == BUNDLED_TEMPLATE
        else Path(v.template).read_text(encoding="utf-8")
    )
    return jinja2_environment().from_string(template_text).render(
        firewall_state=v.state,
        firewall_enabled_at_boot=v.enabled,
        firewall_flush_rules_and_chains=v.flush_rules_and_chains,
        firewall_template=v.template,
        firewall_allowed_tcp_ports=list(v.allowed_tcp_ports),
        firewall_allowed_udp_ports=list(v.allowed_udp_ports),
        firewall_forwarded_tcp_ports=[port.model_dump() for port in v.forwarded_tcp_ports],
        firewall_forwarded_udp_ports=[port.model_dump() for port in v.forwarded_udp_ports],
        firewall_additional_rules=list(v.additional_rules),
        firewall_enable_ipv6=v.enable_ipv6,
        firewall_ip6_additional_rules=list(v.ip6_additional_rules),
        firewall_log_dropped_packets=v.log_dropped_packets,
        firewall_disable_firewalld=v.disable_firewalld,
        firewall_disable_ufw=v.disable_ufw,
    )


# --- helper deploys ----------------------------------------------------------


def install() -> Deploy:
    """``tasks/main.yml``'s first task: ``package: name=iptables state=present``.

    Branch-free: the one package name is frozen with the target family, and the
    task notifies nothing in the role. No cache refresh -- the role does not
    run ``apt-get update`` either.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            apt.packages,
            name="Ensure iptables is present",
            packages=IPTABLES_PACKAGES,
            sudo=True,
        )

    return deploy


def configure(content: str, state: str, enabled: bool) -> Deploy:
    """The role's two template tasks and the final service task, in play order.

    The script lands via the spool-and-install family because ``/etc`` is not
    writable by the login user, with the paired :func:`~reemote.modules.files.file`
    converging the ``0744`` the template task sets -- install idempotence is
    content-only, so a mode-only drift would otherwise never re-notify (the
    request carries a stable ``name=`` from :data:`NOTIFYING`). The unit file
    is static text (:data:`UNIT_FILE`), notifies nothing (the role's own fan-in
    is the one handler), and precedes the service request in the same wave --
    the driver applies each request before pulling the next, so the
    state-gated ``daemon_reload`` (D5) still sees this wave's write.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        yield op(
            files.install,
            name="Copy firewall script into place",
            content=content,
            dest=FIREWALL_SCRIPT,
            mode=SCRIPT_MODE,
            user="root",
            group="root",
            sudo=True,
        )
        yield op(
            files.file,
            name="Set firewall script mode",
            path=FIREWALL_SCRIPT,
            mode=SCRIPT_MODE,
            user="root",
            group="root",
            sudo=True,
        )
        yield op(
            files.install,
            name="Copy firewall systemd unit file into place",
            content=UNIT_FILE,
            dest=FIREWALL_UNIT,
            mode=UNIT_MODE,
            user="root",
            group="root",
            sudo=True,
        )

        # ``service: name=firewall state={{ firewall_state }}`` -- the verbs map
        # as in apache: restarted/reloaded are stateless (they always report
        # changed), the default started is fact-gated and converges.
        yield op(
            systemd.service,
            name="Configure the firewall service",
            service=SERVICE_NAME,
            running=state != "stopped",
            restarted=state == "restarted",
            reloaded=state == "reloaded",
            enabled=enabled,
            daemon_reload=True,
            sudo=True,
        )

    return deploy


def handler(notified: frozenset[str]) -> Deploy:
    """``handlers/main.yml``: ``service: name=firewall state=restarted``.

    ``restarted`` is stateless and always reports a change, faithful to
    Ansible's service module; an empty ``notified`` set yields no request,
    which is the converged no-op Ansible's handler gives for free.
    ``daemon_reload=True`` is the same implicit-reload made explicit (D5) and
    state-gated, so a replayed handler converges.
    """
    return service_handler(notified, (SERVICE_NAME,), action="restarted", daemon_reload=True)


# --- the role ----------------------------------------------------------------


def firewall(v: VarsSet[FirewallVars]) -> Deploy:
    """Install and configure an iptables firewall on Ubuntu hosts, and run its handler.

    Args:
        v: The role's resolved variables, one :class:`FirewallVars` per host
            (``resolve_vars(FirewallVars, "firewall", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. It publishes no outputs -- no
        other role consumes them -- so it returns ``None`` on every path,
        including the family guard's early return.

    Raises:
        CommandError: The first-run flush exited non-zero -- the role ran the
            play with ``become:`` and lets the command task fail, and the
            barrier result is data, so the body raises here (D4). Raised after
            the install wave applied and before any config request is built.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # Wave 0: the package. The FileExists barrier below closes the wave,
        # which is what makes the gate read the previous converge's file.
        yield from install()(host)

        # The role's `creates=/etc/firewall.bash`, as an in-stream fact (D4).
        script_present = bool((yield fact(FileExists, path=FIREWALL_SCRIPT)))

        # Wave 1 (first run only): the flush. A converged host parks on nothing
        # here, so its stream skips a wave -- the wave count is host-dependent.
        if not script_present:
            result = yield act(
                Shell,
                command=FLUSH_COMMAND,
                name="Flush iptables the first time playbook runs",
                sudo=True,
            )
            # A barrier never raises on a non-zero exit -- the Result is data
            # -- so the role's failing command task becomes this raise.
            if result["exit_status"] != 0:
                raise CommandError(
                    host.name, FLUSH_COMMAND, result["exit_status"], result["stderr"]
                )

        # Wave 2: the rendered script, the unit, the service state, then read
        # this wave's own outcome. The checkpoint must immediately follow the
        # stage it reports on; one placed after the handler wave would read
        # the handler's entries and notify nobody.
        yield from configure(render_firewall_script(hv), hv.state, hv.enabled)(host)
        wave_report = yield checkpoint("configure done")

        notified = changed_hosts(wave_report, NOTIFYING)

        # Wave 3: the handler, only on the hosts the checkpoint selected.
        yield from handler(notified)(host)

    return deploy


__all__ = [
    "FirewallVars",
    "ForwardedPort",
    "firewall",
]
