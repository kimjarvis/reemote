# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class YumRepository:
    """
    A class to encapsulate the functionality of managing YUM repositories in RPM-based Linux distributions.
    It allows users to add or remove YUM repositories with various configuration options.

    Attributes:
        name (str): Unique repository ID. This option builds the section name of the repository in the repo file.
        state (str): State of the repo file ('present' or 'absent').
        description (str): A human-readable string describing the repository.
        baseurl (list): URL(s) to the directory where the yum repository's 'repodata' directory lives.
        metalink (str): Specifies a URL to a metalink file for the repomd.xml.
        mirrorlist (str): Specifies a URL to a file containing a list of baseurls.
        file (str): File name without the .repo extension to save the repo in.
        enabled (bool): This tells yum whether or not use this repository.
        gpgcheck (bool): Tells yum whether or not it should perform a GPG signature check on packages.
        gpgkey (list): URL(s) pointing to the ASCII-armored GPG key file for the repository.
        reposdir (str): Directory where the .repo files will be stored.
        async_ (bool): If set to true Yum will download packages and metadata from this repo in parallel.
        attributes (str): The attributes the resulting filesystem object should have.
        bandwidth (str): Maximum available network bandwidth in bytes/second.
        cost (str): Relative cost of accessing this repository.
        countme (bool): Whether a special flag should be added to a randomly chosen metalink/mirrorlist query.
        deltarpm_metadata_percentage (str): When the relative size of deltarpm metadata vs pkgs is larger than this.
        deltarpm_percentage (str): When the relative size of delta vs pkg is larger than this.
        enablegroups (bool): Determines whether yum will allow the use of package groups for this repository.
        exclude (list): List of packages to exclude from updates or installs.
        failovermethod (str): Method for failover ('roundrobin' or 'priority').
        gpgcakey (str): A URL pointing to the ASCII-armored CA key file for the repository.
        group (str): Name of the group that should own the filesystem object.
        http_caching (str): Determines how upstream HTTP caches are instructed to handle HTTP downloads.
        include (str): Include external configuration file.
        includepkgs (list): List of packages you want to only use from a repository.
        ip_resolve (str): Determines how yum resolves host names.
        keepalive (bool): This tells yum whether or not HTTP/1.1 keepalive should be used.
        keepcache (str): Determines whether or not yum keeps the cache of headers and packages.
        metadata_expire (str): Time (in seconds) after which the metadata will expire.
        metadata_expire_filter (str): Filter the metadata_expire time.
        mirrorlist_expire (str): Time (in seconds) after which the mirrorlist locally cached will expire.
        mode (str): The permissions the resulting filesystem object should have.
        module_hotfixes (bool): Disable module RPM filtering.
        owner (str): Name of the user that should own the filesystem object.
        password (str): Password to use with the username for basic authentication.
        priority (str): Enforce ordered protection of repositories.
        protect (bool): Protect packages from updates from other repositories.
        proxy (str): URL to the proxy server that yum should use.
        proxy_password (str): Password for this proxy.
        proxy_username (str): Username to use for proxy.
        repo_gpgcheck (bool): This tells yum whether or not it should perform a GPG signature check on the repodata.
        retries (str): Set the number of times any attempt to retrieve a file should retry.
        s3_enabled (bool): Enables support for S3 repositories.
        selevel (str): The level part of the SELinux filesystem object context.
        serole (str): The role part of the SELinux filesystem object context.
        setype (str): The type part of the SELinux filesystem object context.
        seuser (str): The user part of the SELinux filesystem object context.
        skip_if_unavailable (bool): If set to true yum will continue running if this repository cannot be contacted.
        ssl_check_cert_permissions (bool): Whether yum should check the permissions on the paths for the certificates.
        sslcacert (str): Path to the directory containing the databases of the certificate authorities.
        sslclientcert (str): Path to the SSL client certificate yum should use to connect to repos/remote sites.
        sslclientkey (str): Path to the SSL client key yum should use to connect to repos/remote sites.
        sslverify (bool): Defines whether yum should verify SSL certificates/hosts at all.
        throttle (str): Enable bandwidth throttling for downloads.
        timeout (str): Number of seconds to wait for a connection before timing out.
        ui_repoid_vars (str): When a repository id is displayed, append these yum variables to the string.
        unsafe_writes (bool): Influence when to use atomic operation to prevent data corruption.
        username (str): Username to use for basic authentication to a repo.

    **Examples:**

    .. code:: python

        # Add a repository
        r = yield YumRepository(
            name="epel",
            description="EPEL YUM repo",
            baseurl=["https://download.fedoraproject.org/pub/epel/$releasever/$basearch/"]
        )
        print(r.repo)

        # Remove a repository
        r = yield YumRepository(name="epel", state="absent")
        print(r.state)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - All comments will be removed if modifying an existing repo file.
        - Section order is preserved in an existing repo file.
        - Parameters in a section are ordered alphabetically in an existing repo file.
        - The repo file will be automatically deleted if it contains no repository.
    """

    def __init__(self,
                 name: str,
                 state: str = "present",
                 description: str = None,
                 baseurl: list = None,
                 metalink: str = None,
                 mirrorlist: str = None,
                 file: str = None,
                 enabled: bool = None,
                 gpgcheck: bool = None,
                 gpgkey: list = None,
                 reposdir: str = "/etc/yum.repos.d",
                 async_: bool = None,
                 attributes: str = None,
                 bandwidth: str = None,
                 cost: str = None,
                 countme: bool = None,
                 deltarpm_metadata_percentage: str = None,
                 deltarpm_percentage: str = None,
                 enablegroups: bool = None,
                 exclude: list = None,
                 failovermethod: str = None,
                 gpgcakey: str = None,
                 group: str = None,
                 http_caching: str = None,
                 include: str = None,
                 includepkgs: list = None,
                 ip_resolve: str = None,
                 keepalive: bool = None,
                 keepcache: str = None,
                 metadata_expire: str = None,
                 metadata_expire_filter: str = None,
                 mirrorlist_expire: str = None,
                 mode: str = None,
                 module_hotfixes: bool = None,
                 owner: str = None,
                 password: str = None,
                 priority: str = None,
                 protect: bool = None,
                 proxy: str = None,
                 proxy_password: str = None,
                 proxy_username: str = None,
                 repo_gpgcheck: bool = None,
                 retries: str = None,
                 s3_enabled: bool = None,
                 selevel: str = None,
                 serole: str = None,
                 setype: str = None,
                 seuser: str = None,
                 skip_if_unavailable: bool = None,
                 ssl_check_cert_permissions: bool = None,
                 sslcacert: str = None,
                 sslclientcert: str = None,
                 sslclientkey: str = None,
                 sslverify: bool = None,
                 throttle: str = None,
                 timeout: str = None,
                 ui_repoid_vars: str = None,
                 unsafe_writes: bool = False,
                 username: str = None):

        self.name = name
        self.state = state
        self.description = description
        self.baseurl = baseurl
        self.metalink = metalink
        self.mirrorlist = mirrorlist
        self.file = file
        self.enabled = enabled
        self.gpgcheck = gpgcheck
        self.gpgkey = gpgkey
        self.reposdir = reposdir
        self.async_ = async_
        self.attributes = attributes
        self.bandwidth = bandwidth
        self.cost = cost
        self.countme = countme
        self.deltarpm_metadata_percentage = deltarpm_metadata_percentage
        self.deltarpm_percentage = deltarpm_percentage
        self.enablegroups = enablegroups
        self.exclude = exclude
        self.failovermethod = failovermethod
        self.gpgcakey = gpgcakey
        self.group = group
        self.http_caching = http_caching
        self.include = include
        self.includepkgs = includepkgs
        self.ip_resolve = ip_resolve
        self.keepalive = keepalive
        self.keepcache = keepcache
        self.metadata_expire = metadata_expire
        self.metadata_expire_filter = metadata_expire_filter
        self.mirrorlist_expire = mirrorlist_expire
        self.mode = mode
        self.module_hotfixes = module_hotfixes
        self.owner = owner
        self.password = password
        self.priority = priority
        self.protect = protect
        self.proxy = proxy
        self.proxy_password = proxy_password
        self.proxy_username = proxy_username
        self.repo_gpgcheck = repo_gpgcheck
        self.retries = retries
        self.s3_enabled = s3_enabled
        self.selevel = selevel
        self.serole = serole
        self.setype = setype
        self.seuser = seuser
        self.skip_if_unavailable = skip_if_unavailable
        self.ssl_check_cert_permissions = ssl_check_cert_permissions
        self.sslcacert = sslcacert
        self.sslclientcert = sslclientcert
        self.sslclientkey = sslclientkey
        self.sslverify = sslverify
        self.throttle = throttle
        self.timeout = timeout
        self.ui_repoid_vars = ui_repoid_vars
        self.unsafe_writes = unsafe_writes
        self.username = username

    def __repr__(self):
        return (f"YumRepository(name={self.name!r}, "
                f"state={self.state!r}, "
                f"description={self.description!r}, "
                f"baseurl={self.baseurl!r}, "
                f"metalink={self.metalink!r}, "
                f"mirrorlist={self.mirrorlist!r}, "
                f"file={self.file!r}, "
                f"enabled={self.enabled!r}, "
                f"gpgcheck={self.gpgcheck!r}, "
                f"gpgkey={self.gpgkey!r}, "
                f"reposdir={self.reposdir!r})")

    def execute(self):
        # Build the command arguments
        args = []

        # Required parameters
        args.extend(["--name", self.name])
        args.extend(["--state", self.state])

        # Optional parameters
        if self.description:
            args.extend(["--description", self.description])
        if self.baseurl:
            for url in self.baseurl:
                args.extend(["--baseurl", url])
        if self.metalink:
            args.extend(["--metalink", self.metalink])
        if self.mirrorlist:
            args.extend(["--mirrorlist", self.mirrorlist])
        if self.file:
            args.extend(["--file", self.file])
        if self.enabled is not None:
            args.extend(["--enabled", str(self.enabled).lower()])
        if self.gpgcheck is not None:
            args.extend(["--gpgcheck", str(self.gpgcheck).lower()])
        if self.gpgkey:
            for key in self.gpgkey:
                args.extend(["--gpgkey", key])
        if self.reposdir:
            args.extend(["--reposdir", self.reposdir])
        if self.async_ is not None:
            args.extend(["--async", str(self.async_).lower()])
        if self.attributes:
            args.extend(["--attributes", self.attributes])
        if self.bandwidth:
            args.extend(["--bandwidth", self.bandwidth])
        if self.cost:
            args.extend(["--cost", self.cost])
        if self.countme is not None:
            args.extend(["--countme", str(self.countme).lower()])
        if self.deltarpm_metadata_percentage:
            args.extend(["--deltarpm_metadata_percentage", self.deltarpm_metadata_percentage])
        if self.deltarpm_percentage:
            args.extend(["--deltarpm_percentage", self.deltarpm_percentage])
        if self.enablegroups is not None:
            args.extend(["--enablegroups", str(self.enablegroups).lower()])
        if self.exclude:
            args.extend(["--exclude", ",".join(self.exclude)])
        if self.failovermethod:
            args.extend(["--failovermethod", self.failovermethod])
        if self.gpgcakey:
            args.extend(["--gpgcakey", self.gpgcakey])
        if self.group:
            args.extend(["--group", self.group])
        if self.http_caching:
            args.extend(["--http_caching", self.http_caching])
        if self.include:
            args.extend(["--include", self.include])
        if self.includepkgs:
            args.extend(["--includepkgs", ",".join(self.includepkgs)])
        if self.ip_resolve:
            args.extend(["--ip_resolve", self.ip_resolve])
        if self.keepalive is not None:
            args.extend(["--keepalive", str(self.keepalive).lower()])
        if self.keepcache:
            args.extend(["--keepcache", self.keepcache])
        if self.metadata_expire:
            args.extend(["--metadata_expire", self.metadata_expire])
        if self.metadata_expire_filter:
            args.extend(["--metadata_expire_filter", self.metadata_expire_filter])
        if self.mirrorlist_expire:
            args.extend(["--mirrorlist_expire", self.mirrorlist_expire])
        if self.mode:
            args.extend(["--mode", self.mode])
        if self.module_hotfixes is not None:
            args.extend(["--module_hotfixes", str(self.module_hotfixes).lower()])
        if self.owner:
            args.extend(["--owner", self.owner])
        if self.password:
            args.extend(["--password", self.password])
        if self.priority:
            args.extend(["--priority", self.priority])
        if self.protect is not None:
            args.extend(["--protect", str(self.protect).lower()])
        if self.proxy:
            args.extend(["--proxy", self.proxy])
        if self.proxy_password:
            args.extend(["--proxy_password", self.proxy_password])
        if self.proxy_username:
            args.extend(["--proxy_username", self.proxy_username])
        if self.repo_gpgcheck is not None:
            args.extend(["--repo_gpgcheck", str(self.repo_gpgcheck).lower()])
        if self.retries:
            args.extend(["--retries", self.retries])
        if self.s3_enabled is not None:
            args.extend(["--s3_enabled", str(self.s3_enabled).lower()])
        if self.selevel:
            args.extend(["--selevel", self.selevel])
        if self.serole:
            args.extend(["--serole", self.serole])
        if self.setype:
            args.extend(["--setype", self.setype])
        if self.seuser:
            args.extend(["--seuser", self.seuser])
        if self.skip_if_unavailable is not None:
            args.extend(["--skip_if_unavailable", str(self.skip_if_unavailable).lower()])
        if self.ssl_check_cert_permissions is not None:
            args.extend(["--ssl_check_cert_permissions", str(self.ssl_check_cert_permissions).lower()])
        if self.sslcacert:
            args.extend(["--sslcacert", self.sslcacert])
        if self.sslclientcert:
            args.extend(["--sslclientcert", self.sslclientcert])
        if self.sslclientkey:
            args.extend(["--sslclientkey", self.sslclientkey])
        if self.sslverify is not None:
            args.extend(["--sslverify", str(self.sslverify).lower()])
        if self.throttle:
            args.extend(["--throttle", self.throttle])
        if self.timeout:
            args.extend(["--timeout", self.timeout])
        if self.ui_repoid_vars:
            args.extend(["--ui_repoid_vars", self.ui_repoid_vars])
        if self.unsafe_writes is not None:
            args.extend(["--unsafe_writes", str(self.unsafe_writes).lower()])
        if self.username:
            args.extend(["--username", self.username])

        # Construct the command
        cmd = "yum_repository " + " ".join(args)

        # Execute the command
        r = yield Command(cmd)
        r.changed = True
        r.repo = self.name
        r.state = self.state