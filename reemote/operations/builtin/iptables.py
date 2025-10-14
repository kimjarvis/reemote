# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Iptables:
    """
    A class to encapsulate the functionality of iptables rules management in Linux systems.
    This class allows users to add, remove, and manage iptables rules with various parameters
    similar to the ansible.builtin.iptables module.

    Attributes:
        action (str): Whether the rule should be appended or inserted.
        chain (str): Specify the iptables chain to modify.
        chain_management (bool): If true, create/delete chains as needed.
        comment (str): Comment to add to the rule.
        ctstate (list): Connection states to match.
        destination (str): Destination specification.
        destination_port (str): Destination port or port range.
        destination_ports (list): Multiple destination ports.
        dst_range (str): Destination IP range.
        flush (bool): Flush all rules from chain/table.
        fragment (str): Fragment matching specification.
        gateway (str): Gateway for TEE jump target.
        gid_owner (str): Group ID for owner matching.
        goto (str): Continue processing in specified chain.
        icmp_type (str): ICMP type specification.
        in_interface (str): Input interface specification.
        ip_version (str): IP protocol version.
        jump (str): Target of the rule.
        limit (str): Rate limiting specification.
        limit_burst (str): Burst limit specification.
        log_level (str): Logging level for LOG jump.
        log_prefix (str): Log prefix for LOG jump.
        match (list): Extension modules for matching.
        match_set (str): IP set name for matching.
        match_set_flags (str): Flags for match_set parameter.
        numeric (bool): Skip DNS lookup in list operations.
        out_interface (str): Output interface specification.
        policy (str): Chain policy setting.
        protocol (str): Protocol specification.
        reject_with (str): Error packet type for REJECT.
        rule_num (str): Rule number for insertion.
        set_counters (str): Initialize packet/byte counters.
        set_dscp_mark (str): DSCP mark value.
        set_dscp_mark_class (str): Predefined DiffServ class.
        source (str): Source specification.
        source_port (str): Source port or port range.
        src_range (str): Source IP range.
        state (str): Whether rule should be present or absent.
        syn (str): SYN flag matching.
        table (str): Packet matching table.
        tcp_flags (dict): TCP flags specification.
        to_destination (str): Destination for DNAT.
        to_ports (str): Port redirection specification.
        to_source (str): Source for SNAT.
        uid_owner (str): User ID for owner matching.
        wait (str): Wait time for xtables lock.
        guard (bool): If False, commands will not be executed.
        sudo (bool): If True, execute with sudo privileges.

    **Examples:**

    .. code:: python

        # Block specific IP
        r = yield Iptables(chain="INPUT", source="8.8.8.8", jump="DROP", sudo=True)

        # Forward port 80 to 8600
        r = yield Iptables(
            table="nat",
            chain="PREROUTING",
            in_interface="eth0",
            protocol="tcp",
            match=["tcp"],
            destination_port="80",
            jump="REDIRECT",
            to_ports="8600",
            comment="Redirect web traffic to port 8600",
            sudo=True
        )

        # Allow related and established connections
        r = yield Iptables(
            chain="INPUT",
            ctstate=["ESTABLISHED", "RELATED"],
            jump="ACCEPT",
            sudo=True
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the provided parameters
        - Supports both IPv4 and IPv6 through ip_version parameter
        - All parameters are optional except those required for specific operations
    """

    def __init__(self,
                 action: str = "append",
                 chain: str = None,
                 chain_management: bool = False,
                 comment: str = None,
                 ctstate: list = None,
                 destination: str = None,
                 destination_port: str = None,
                 destination_ports: list = None,
                 dst_range: str = None,
                 flush: bool = False,
                 fragment: str = None,
                 gateway: str = None,
                 gid_owner: str = None,
                 goto: str = None,
                 icmp_type: str = None,
                 in_interface: str = None,
                 ip_version: str = "ipv4",
                 jump: str = None,
                 limit: str = None,
                 limit_burst: str = None,
                 log_level: str = None,
                 log_prefix: str = None,
                 match: list = None,
                 match_set: str = None,
                 match_set_flags: str = None,
                 numeric: bool = False,
                 out_interface: str = None,
                 policy: str = None,
                 protocol: str = None,
                 reject_with: str = None,
                 rule_num: str = None,
                 set_counters: str = None,
                 set_dscp_mark: str = None,
                 set_dscp_mark_class: str = None,
                 source: str = None,
                 source_port: str = None,
                 src_range: str = None,
                 state: str = "present",
                 syn: str = "ignore",
                 table: str = "filter",
                 tcp_flags: dict = None,
                 to_destination: str = None,
                 to_ports: str = None,
                 to_source: str = None,
                 uid_owner: str = None,
                 wait: str = None,
                 guard: bool = True,
                 sudo: bool = False):

        self.action = action
        self.chain = chain
        self.chain_management = chain_management
        self.comment = comment
        self.ctstate = ctstate or []
        self.destination = destination
        self.destination_port = destination_port
        self.destination_ports = destination_ports or []
        self.dst_range = dst_range
        self.flush = flush
        self.fragment = fragment
        self.gateway = gateway
        self.gid_owner = gid_owner
        self.goto = goto
        self.icmp_type = icmp_type
        self.in_interface = in_interface
        self.ip_version = ip_version
        self.jump = jump
        self.limit = limit
        self.limit_burst = limit_burst
        self.log_level = log_level
        self.log_prefix = log_prefix
        self.match = match or []
        self.match_set = match_set
        self.match_set_flags = match_set_flags
        self.numeric = numeric
        self.out_interface = out_interface
        self.policy = policy
        self.protocol = protocol
        self.reject_with = reject_with
        self.rule_num = rule_num
        self.set_counters = set_counters
        self.set_dscp_mark = set_dscp_mark
        self.set_dscp_mark_class = set_dscp_mark_class
        self.source = source
        self.source_port = source_port
        self.src_range = src_range
        self.state = state
        self.syn = syn
        self.table = table
        self.tcp_flags = tcp_flags
        self.to_destination = to_destination
        self.to_ports = to_ports
        self.to_source = to_source
        self.uid_owner = uid_owner
        self.wait = wait
        self.guard = guard
        self.sudo = sudo

    def __repr__(self):
        params = []
        for key, value in self.__dict__.items():
            if value is not None and key not in ['guard', 'sudo']:
                params.append(f"{key}={value!r}")
        return f"Iptables({', '.join(params)}, guard={self.guard!r}, sudo={self.sudo!r})"

    def _build_command(self):
        """Build the iptables command based on the provided parameters."""
        if self.ip_version == "ipv6":
            cmd_base = "ip6tables"
        else:
            cmd_base = "iptables"

        cmd_parts = [cmd_base]

        # Add wait parameter if specified
        if self.wait:
            cmd_parts.extend(["-w", self.wait])

        # Handle flush operation
        if self.flush:
            cmd_parts.append("--flush")
            if self.chain:
                cmd_parts.append(self.chain)
            if self.table != "filter":
                cmd_parts.extend(["-t", self.table])
            return " ".join(cmd_parts)

        # Handle policy setting
        if self.policy and self.chain:
            cmd_parts.extend(["-t", self.table, "-P", self.chain, self.policy])
            return " ".join(cmd_parts)

        # Handle chain management
        if self.chain_management:
            if self.state == "present":
                cmd_parts.extend(["-t", self.table, "-N", self.chain])
            elif self.state == "absent" and not any([getattr(self, attr) for attr in
                                                     ['action', 'comment', 'ctstate', 'destination', 'destination_port',
                                                      'destination_ports', 'dst_range', 'fragment', 'gateway',
                                                      'gid_owner',
                                                      'goto', 'icmp_type', 'in_interface', 'jump', 'limit',
                                                      'limit_burst',
                                                      'log_level', 'log_prefix', 'match', 'match_set',
                                                      'match_set_flags',
                                                      'out_interface', 'protocol', 'reject_with', 'rule_num',
                                                      'set_counters',
                                                      'set_dscp_mark', 'set_dscp_mark_class', 'source', 'source_port',
                                                      'src_range', 'syn', 'tcp_flags', 'to_destination', 'to_ports',
                                                      'to_source', 'uid_owner']]):
                cmd_parts.extend(["-t", self.table, "-X", self.chain])
            return " ".join(cmd_parts)

        # Build rule command
        if self.table != "filter":
            cmd_parts.extend(["-t", self.table])

        if self.chain:
            cmd_parts.extend(["-A" if self.action == "append" else "-I", self.chain])
            if self.action == "insert" and self.rule_num:
                cmd_parts.append(self.rule_num)

        # Add rule parameters
        if self.in_interface:
            cmd_parts.extend(["-i", self.in_interface])

        if self.out_interface:
            cmd_parts.extend(["-o", self.out_interface])

        if self.protocol:
            cmd_parts.extend(["-p", self.protocol])

        if self.source:
            cmd_parts.extend(["-s", self.source])

        if self.destination:
            cmd_parts.extend(["-d", self.destination])

        if self.src_range:
            cmd_parts.extend(["-m", "iprange", "--src-range", self.src_range])

        if self.dst_range:
            cmd_parts.extend(["-m", "iprange", "--dst-range", self.dst_range])

        if self.match_set and self.match_set_flags:
            cmd_parts.extend(["-m", "set", "--match-set", self.match_set, self.match_set_flags])

        if self.match:
            for m in self.match:
                cmd_parts.extend(["-m", m])

        if self.ctstate:
            cmd_parts.extend(["-m", "conntrack", "--ctstate", ",".join(self.ctstate)])

        if self.source_port:
            cmd_parts.extend(["--sport", self.source_port])

        if self.destination_port:
            cmd_parts.extend(["--dport", self.destination_port])

        if self.destination_ports:
            cmd_parts.extend(["-m", "multiport", "--dports", ",".join(self.destination_ports)])

        if self.syn == "match":
            cmd_parts.append("--syn")
        elif self.syn == "negate":
            cmd_parts.append("! --syn")

        if self.tcp_flags:
            flags = ",".join(self.tcp_flags.get("flags", []))
            flags_set = ",".join(self.tcp_flags.get("flags_set", []))
            cmd_parts.extend(["--tcp-flags", flags, flags_set])

        if self.fragment:
            cmd_parts.extend(["-f", self.fragment])

        if self.uid_owner:
            cmd_parts.extend(["-m", "owner", "--uid-owner", self.uid_owner])

        if self.gid_owner:
            cmd_parts.extend(["-m", "owner", "--gid-owner", self.gid_owner])

        if self.limit:
            cmd_parts.extend(["-m", "limit", "--limit", self.limit])

        if self.limit_burst:
            cmd_parts.extend(["--limit-burst", self.limit_burst])

        if self.jump:
            cmd_parts.extend(["-j", self.jump])
        elif self.goto:
            cmd_parts.extend(["-g", self.goto])

        if self.to_ports:
            cmd_parts.extend(["--to-ports", self.to_ports])

        if self.to_destination:
            cmd_parts.extend(["--to-destination", self.to_destination])

        if self.to_source:
            cmd_parts.extend(["--to-source", self.to_source])

        if self.reject_with:
            cmd_parts.extend(["--reject-with", self.reject_with])

        if self.gateway:
            cmd_parts.extend(["--gateway", self.gateway])

        if self.set_dscp_mark:
            cmd_parts.extend(["--set-dscp", self.set_dscp_mark])
        elif self.set_dscp_mark_class:
            cmd_parts.extend(["--set-dscp-class", self.set_dscp_mark_class])

        if self.log_prefix:
            cmd_parts.extend(["--log-prefix", f'"{self.log_prefix}"'])

        if self.log_level:
            cmd_parts.extend(["--log-level", self.log_level])

        if self.comment:
            cmd_parts.extend(["-m", "comment", "--comment", f'"{self.comment}"'])

        if self.set_counters:
            cmd_parts.extend(["-c", self.set_counters])

        return " ".join(cmd_parts)

    def execute(self):
        """Execute the iptables command."""
        cmd = self._build_command()
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo)
        r.changed = True