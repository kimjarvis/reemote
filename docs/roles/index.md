# Roles

reemote ships forty-one configuration roles as **library code**. Each one is the
migration of a published Ansible role — the same tasks, the same variables, the
same handlers — expressed in the library's own wave/barrier model instead of a
YAML play. A role is a factory under `reemote.roles` that takes a typed variables
model and returns a resumable per-host deploy, so it is imported, called,
composed and tested like any other part of the library:

```python
from reemote import resolve_vars, run_waves
from reemote.roles import NfsVars, nfs


async def main(inventory):
    vars_set = resolve_vars(NfsVars, "nfs", inventory)  # the entry's nfs role mapping
    report, hosts = await run_waves(inventory, nfs(vars_set))
    print(report.tree())
```

The factory is an ordinary function, so `nfs(vars_set)` is *called*, not awaited —
[`datadog`](datadog.md) and [`zabbix_agent`](zabbix_agent.md) are the corpus's two
`async def` factories, and their call-sites are `await datadog(v)` and
`await zabbix_agent(v)`. What a factory returns is one generator per host that
yields operation requests **and** barriers: a `fact` read, an `act` command, a
`checkpoint` that closes the current wave and reads its report, a `control` fetch.
That is what lets the reads an Ansible play took *between* its steps — a version
probe, a `stat` loop, a notification check — live inside the deploy instead of
forcing a plan-then-apply split. [`run_waves`](../drivers.md#wave-loop) is the
driver that understands barriers; `run()` is not.

## Roles by type

The forty-one roles group by what they configure. The [catalogue](#the-catalogue)
below is the full reference, with each role's source, target family, wave count
and outputs.

### Web servers

| Role | Purpose |
| --- | --- |
| [apache](apache.md) | Apache httpd — ports, module and conf symlinks, rendered vhosts and enable links, service state, one restart handler |
| [apache_php_fpm](apache_php_fpm.md) | Apache PHP-FPM — the `proxy.load`/`proxy_fcgi.load` enable symlinks and the gated `restart apache` handler |
| [nginx](nginx.md) | nginx — the optional official PPA, vhosts, a `nginx -t`-validated `nginx.conf`, service state, two handler channels |
| [php](php.md) | PHP — packages, `php.ini` in every conf path, FPM pools, the `restart webserver` → `restart php-fpm` handler chain |
| [php_versions](php_versions.md) | PHP versions — the Sury repository, the purge of every other version, and the version-keyed paths the `php` role installs from |
| [php_xdebug](php_xdebug.md) | Xdebug — built from source and loaded into PHP, composing the `php` role inside its own body |

### Databases

| Role | Purpose |
| --- | --- |
| [postgresql](postgresql.md) | PostgreSQL — packages, cluster init, `postgresql.conf` and `pg_hba.conf`, then users, databases and privileges; the two-pass role |

### Caches and shared storage

| Role | Purpose |
| --- | --- |
| [memcached](memcached.md) | memcached — the package, `/etc/memcached.conf`, the service, one restart handler |
| [redis](redis.md) | Redis — the setgid `/etc/redis` directory, the rendered `redis.conf`, the package, the service, one restart handler |
| [nfs](nfs.md) | NFS — the packages, the export directories, `/etc/exports`, the gated service and an `exportfs -ra` command handler |
| [glusterfs](glusterfs.md) | GlusterFS — the optional Gluster PPA with its reinstall split, the server/client packages and the `glusterd` unit started and enabled |
| [minio](minio.md) | MinIO — the service user and directories, the unpacked `minio`/`mc` bundle, the rendered env and unit, the restart → port-wait handler chain, opt-in buckets, users and mirror units |

### Language runtimes and tooling

| Role | Purpose |
| --- | --- |
| [java](java.md) | Java — the distribution's default OpenJDK packages and an optional `JAVA_HOME` profile script |
| [nodejs](nodejs.md) | Node.js — the NodeSource key and deb822 repository, the version-pinned `nodejs` package, npm's global prefix and packages |
| [pip](pip.md) | pip — the distro's pip package, the PEP 668 `EXTERNALLY-MANAGED` marker removed, then the package list |
| [ruby](ruby.md) | Ruby — the apt ruby packages, the rubygems package, the profile-script `$PATH` entry, Bundler and the gem list |
| [composer](composer.md) | Composer — the signature-verified installer, the phar move, an optional `auth.json`, global packages, the `$PATH` entry |
| [drush](drush.md) | Drush — the launcher phar and its executable bit |
| [git](git.md) | Git — the apt-cache refresh and the `git_packages` install, as one request |
| [kubectl](kubectl.md) | kubectl — the checksum-pinned release binary, installed version-suffixed with the bare name symlinked beside it |
| [mailhog](mailhog.md) | MailHog — the two release binaries downloaded into `/opt/mailhog` and the started, enabled `mailhog.service` unit |
| [supervisor](supervisor.md) | Supervisor — the pip-installed `supervisor` package, the `0755` log/config directories, the rendered `supervisord.conf`, the present/absent program `conf.d` files, the rendered systemd unit, and one restart handler |
| [daemonize](daemonize.md) | daemonize — the `build-essential` toolchain, the release tarball extracted behind a `creates:` gate, and the `configure`/`make`/`make install` build gated on a `PATH` probe |

### Vendor repositories and platforms

| Role | Purpose |
| --- | --- |
| [repo_epel](repo_epel.md) | EPEL — installs the repository package and flips `enabled=` in the `[epel]` section alone |
| [repo_remi](repo_remi.md) | Remi — imports the vendor key and installs the release RPM; `[remi]` ships disabled for per-transaction `--enablerepo=remi` use, while the RPM itself leaves `[remi-modular]` and `[remi-safe]` enabled |
| [docker](docker.md) | Docker CE — the vendor repository with its URL-signed keyring, the edition packages, `daemon.json`, the service, the `docker_users` memberships |
| [datadog](datadog.md) | Datadog agent — the signing keys and repository, the package, `/etc/datadog-agent`, `conf.d` checks, two restart handlers |
| [zabbix_agent](zabbix_agent.md) | Zabbix agent — the vendor keyring and repository lines with their stale-source purge, the agent packages, the rendered `zabbix_agentd.conf`, the service, one restart handler |
| [kibana](kibana.md) | Kibana — the dependencies, the Elasticsearch signing key and Elastic apt repository, the `kibana` package, the service, the rendered `kibana.yml`, one restart handler |
| [elasticsearch](elasticsearch.md) | Elasticsearch — the dependencies, the Elastic signing key and apt repository, the `elasticsearch` package, the rendered config and version-appropriate heap file, the service and port wait, one restart handler |
| [node_exporter](node_exporter.md) | node_exporter — the release tarball unpacked and the binary moved into place, the service group and account, the rendered systemd unit, the running/enabled unit, the restart handler and the metrics-endpoint check |

### Base system

| Role | Purpose |
| --- | --- |
| [hosts](hosts.md) | Hostname and hosts file — the rendered `/etc/hostname` and `/etc/hosts` (loopback, default-route, additional entries, the IPv6 block), each with its metadata companion |
| [swap](swap.md) | Swap file — the `/etc/fstab` entry, the file built at the wanted size (`dd`, `0600`, `mkswap`), activated and deactivated around removals and resizes, and `vm.swappiness` in a sysctl drop-in |
| [systemd](systemd.md) | systemd units — the load path, the rendered unit or drop-in file, the two reload handlers, each unit's running and enabled state, and the opt-in uninstall |

### Security and certificates

| Role | Purpose |
| --- | --- |
| [security](security.md) | SSH and host hardening — the `sshd_config` directives and access lists, the sudoers loops, Fail2Ban and unattended-upgrades |
| [clamav](clamav.md) | ClamAV — the antivirus packages, the install-gated `freshclam` run, the `clamd.conf` edits, both units and one restart handler |
| [firewall](firewall.md) | iptables firewall — the once-per-machine flush gate, the rendered `/etc/firewall.bash` script and its unit, service state, one restart handler |
| [certbot](certbot.md) | Certbot — the release-dispatched package, the configured certificates (standalone or webroot) and the renewal cron entry |
| [htpasswd](htpasswd.md) | htpasswd — the `apache2-utils` install and each managed file's merged `name:hash` lines, converged through a `FileContents` barrier |

### Accounts

| Role | Purpose |
| --- | --- |
| [users](users.md) | Accounts — the groups, per-user primary groups, users with shell/comment/home/membership, generated key pairs, authorized keys, profile blocks, and the deletions last |

### Time synchronisation

| Role | Purpose |
| --- | --- |
| [ntp](ntp.md) | NTP — the daemon, `tzdata`, the system timezone, the templated config, the `restart ntp` and `restart cron` handlers |

## The catalogue

| Role | Migrates | Target | Waves | Outputs | `reemote[templates]` | `run_waves` passes |
| --- | --- | --- | --- | --- | --- | --- |
| [repo_epel](repo_epel.md) | [`geerlingguy.repo-epel`](https://github.com/geerlingguy/ansible-role-repo-epel) | RedHat | 3–4 | none | no | 1 |
| [apache](apache.md) | [`geerlingguy.apache`](https://github.com/geerlingguy/ansible-role-apache) | Ubuntu | 3 + one per SSL vhost | `ApacheOutputs(version, service)` | **yes** | 1 |
| [php](php.md) | [`geerlingguy.php`](https://github.com/geerlingguy/ansible-role-php) | Ubuntu | 12 fresh / 11 converged | `PhpOutputs(version, extension_conf_paths, webserver_notified)` | no | 1 |
| [php_versions](php_versions.md) | [`geerlingguy.php-versions`](https://github.com/geerlingguy/ansible-role-php-versions) | Ubuntu | 3 fresh / 2 converged | `PhpVersionsOutputs(version, packages, conf_paths, …)` | no | 1 |
| [php_xdebug](php_xdebug.md) | [`geerlingguy.php-xdebug`](https://github.com/geerlingguy/ansible-role-php-xdebug) | Ubuntu (php's guard) | 15 fresh / 13 converged | none | no | 1 |
| [nginx](nginx.md) | [`geerlingguy.nginx`](https://github.com/geerlingguy/ansible-role-nginx) | Ubuntu | 4 fresh / 3 converged | none | **yes** | 1 |
| [postgresql](postgresql.md) | [`geerlingguy.postgresql`](https://github.com/geerlingguy/ansible-role-postgresql) | RedHat | pass A 7 (6 converged), pass B 2 | none | **yes** | **2** |
| [datadog](datadog.md) | [`datadog.datadog`](https://github.com/DataDog/ansible-datadog) 5.6.0 | Ubuntu | up to 10 | none | **yes** | 1 |
| [composer](composer.md) | [`geerlingguy.composer`](https://github.com/geerlingguy/ansible-role-composer) | Ubuntu | 5 + 2P fresh / 3 + P converged | none | no | 1 |
| [drush](drush.md) | [`geerlingguy.drush`](https://github.com/geerlingguy/ansible-role-drush) | Ubuntu | exactly 3 | none | no | 1 |
| [ntp](ntp.md) | [`geerlingguy.ntp`](https://github.com/geerlingguy/ansible-role-ntp) | Ubuntu | 5 changed / 4 converged | none | **yes** | 1 |
| [docker](docker.md) | [`geerlingguy.docker`](https://github.com/geerlingguy/ansible-role-docker) | Ubuntu | 5 changed / 4 converged | none | no | 1 |
| [java](java.md) | [`geerlingguy.java`](https://github.com/geerlingguy/ansible-role-java) | Ubuntu | 2 | none | no | 1 |
| [memcached](memcached.md) | [`geerlingguy.memcached`](https://github.com/geerlingguy/ansible-role-memcached) | Ubuntu | 2 changed / 1 converged | none | **yes** | 1 |
| [nfs](nfs.md) | [`geerlingguy.nfs`](https://github.com/geerlingguy/ansible-role-nfs) | Ubuntu | 2 changed / 1 converged | none | no | 1 |
| [nodejs](nodejs.md) | [`geerlingguy.nodejs`](https://github.com/geerlingguy/ansible-role-nodejs) | Ubuntu | exactly 1 | none | no | 1 |
| [pip](pip.md) | [`geerlingguy.pip`](https://github.com/geerlingguy/ansible-role-pip) | Ubuntu | 2 | none | no | 1 |
| [git](git.md) | [`geerlingguy.git`](https://github.com/geerlingguy/ansible-role-git) | Ubuntu | 1 | none | no | 1 |
| [security](security.md) | [`geerlingguy.security`](https://github.com/geerlingguy/ansible-role-security) | Ubuntu | 2 changed / 1 converged | none | no | 1 |
| [certbot](certbot.md) | [`geerlingguy.certbot`](https://github.com/geerlingguy/ansible-role-certbot) | Ubuntu | `2 + 2C` (`C` certs); 2 install-only | none | no | 1 |
| [redis](redis.md) | [`geerlingguy.redis`](https://github.com/geerlingguy/ansible-role-redis) | Ubuntu | 2 changed / 1 converged | none | **yes** | 1 |
| [users](users.md) | [`singleplatform-eng/ansible-users`](https://github.com/singleplatform-eng/ansible-users) | Ubuntu | exactly 1 (`2` under the `on_create` gate) | none | no | 1 |
| [hosts](hosts.md) | [`ajsalminen/ansible-role-hosts`](https://github.com/ajsalminen/ansible-role-hosts) | Ubuntu | 2 (1 with a content override) | none | no | 1 |
| [kubectl](kubectl.md) | [`andrewrothstein.kubectl`](https://github.com/andrewrothstein/ansible-kubectl) | Ubuntu | exactly 2 | none | no | 1 |
| [systemd](systemd.md) | [`0x0I/systemd`](https://github.com/0x0I/ansible-role-systemd) | Ubuntu | 2 (3 with a per-user unit) | none | **yes** | 1 |
| [glusterfs](glusterfs.md) | [`geerlingguy.glusterfs`](https://github.com/geerlingguy/ansible-role-glusterfs) | Ubuntu | 2 fresh / 2 converged | none | no | 1 |
| [firewall](firewall.md) | [`geerlingguy.firewall`](https://github.com/geerlingguy/ansible-role-firewall) | Ubuntu | 4 first run / 2 converged | none | **yes** | 1 |
| [zabbix_agent](zabbix_agent.md) | [`dj-wasabi/ansible-zabbix-agent`](https://github.com/ansible-community/ansible-zabbix-agent) | Ubuntu | 5 fresh / 4 converged | none | **yes** | 1 |
| [swap](swap.md) | [`geerlingguy.swap`](https://github.com/geerlingguy/ansible-role-swap) | Ubuntu | 5 fresh / 2 converged | none | no | 1 |
| [minio](minio.md) | [`wezhai/minio`](https://github.com/wezhai/minio) | Ubuntu | 8 changed / 8 converged on the full set (1 + handler + one per `mc` act + sync tail) | `MinioOutputs(service, install_dir, endpoint)` | **yes** | 1 |
| [kibana](kibana.md) | [`geerlingguy.kibana`](https://github.com/geerlingguy/ansible-role-kibana) | Ubuntu | 2 changed / 1 converged | none | **yes** | 1 |
| [repo_remi](repo_remi.md) | [`geerlingguy.repo-remi`](https://github.com/geerlingguy/ansible-role-repo-remi) | RedHat | 3–4 | none | no | 1 |
| [elasticsearch](elasticsearch.md) | [`geerlingguy.elasticsearch`](https://github.com/geerlingguy/ansible-role-elasticsearch) | Ubuntu | 2 changed / 2 converged (the wait always reports) | none | **yes** | 1 |
| [node_exporter](node_exporter.md) | [`geerlingguy.node_exporter`](https://github.com/geerlingguy/ansible-role-node_exporter) | Ubuntu | 3 fresh (4 resolving `latest`) / 3 converged | none | no | 1 |
| [ruby](ruby.md) | [`geerlingguy.ruby`](https://github.com/geerlingguy/ansible-role-ruby) | Ubuntu | 2 | none | no | 1 |
| [apache_php_fpm](apache_php_fpm.md) | [`geerlingguy.apache-php-fpm`](https://github.com/geerlingguy/ansible-role-apache-php-fpm) | Ubuntu | 2 fresh / 1 converged | none | no | 1 |
| [clamav](clamav.md) | [`geerlingguy.clamav`](https://github.com/geerlingguy/ansible-role-clamav) | Ubuntu | 4 fresh / 2 converged | none | no | 1 |
| [mailhog](mailhog.md) | [`geerlingguy.mailhog`](https://github.com/geerlingguy/ansible-role-mailhog) | Ubuntu | exactly 2 | none | no | 1 |
| [supervisor](supervisor.md) | [`geerlingguy.supervisor`](https://github.com/geerlingguy/ansible-role-supervisor) | Ubuntu | 2 changed / 1 converged | none | **yes** | 1 |
| [htpasswd](htpasswd.md) | [geerlingguy.htpasswd](https://github.com/geerlingguy/ansible-role-htpasswd) | Ubuntu | `1 + P` (`P` credential paths); 1 install-only | none | no | 1 |
| [daemonize](daemonize.md) | [`geerlingguy.daemonize`](https://github.com/geerlingguy/ansible-role-daemonize) | Ubuntu | 2 fresh / 1 converged | none | no | 1 |

## Reading the columns

- **Waves** is a range, not a constant. Each barrier closes its own wave and a
  wave with no records is not appended, so the count moves with the host: how
  many SSL certificates `apache` has, how many global packages `composer`
  requires, whether a handler fired at all. "Converged" is the count on a rerun
  against a host that already matches — the interesting number, because a role
  whose wave count *grows* on the second pass has a request that never settles.
- **Outputs** names the dataclass the deploy returns to a composing role. Four
  roles publish one: `apache`, `php`, `php_versions` and `minio`. `php_xdebug`
  exists to consume
  `PhpOutputs` — the PHP version and extension paths it used to carry as a copied
  constant. Every other role returns `None`, and does so on its family-guard's
  early return too.
- **`reemote[templates]`** is needed by the fourteen roles that render configuration
  through Jinja2 or PyYAML — `apache`, `datadog`, `elasticsearch`, `firewall`,
  `kibana`,
  `memcached`, `minio`, `nginx`, `ntp`, `postgresql`, `redis`, `supervisor`,
  `systemd` and
  `zabbix_agent`, the list
  [`reemote.roles._render`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/_render.py)
  exists to carry. The others render in plain Python — `json.dumps`, an f-string, a
  `str.join` — and need nothing beyond the library.
- **`run_waves` passes** is `1` for every role but `postgresql`, which cannot be
  one pass: its `become_user: postgres` data plane needs an inventory
  `privilege` view the system tasks must not run under, so it ships two
  factories, `postgresql` and `postgresql_data`, run as pass A and pass B.

## Each role at a glance

- **[repo_epel](repo_epel.md)** — installs the EPEL repository and flips
  `enabled=` in the `[epel]` section alone, leaving the RPM's other sections
  byte-identical. Two `fact` barriers and a `control` fetch, no handler.
- **[apache](apache.md)** — packages, `ports.conf` listen lines, module and conf
  symlinks, rendered vhosts and their enable symlinks, service state, one restart
  handler. The template role, and the one whose wave count is host-dependent.
- **[php](php.md)** — packages, the packaged-ini cleanup, `php.ini` in every conf
  path, the stray-config sweep, FPM pools, the `restart webserver` → `restart
  php-fpm` chain. Three checkpoints, unioned: the role that carries Ansible's
  handler semantics.
- **[php_versions](php_versions.md)** — the Sury PHP repository, whose *binary*
  signing key the role downloads into a repo-named keyring and then references by
  path, the purge of every other version's `-common` package, and
  the purge of the shared `php-common` behind a one-checkpoint `register` +
  `when` gate. The outputs-publisher: its thirteen `set_fact` aliases are the
  `PhpVersionsOutputs` a composing deploy feeds to the `php` role.
- **[php_xdebug](php_xdebug.md)** — builds Xdebug from source and loads it into
  PHP. The composition proof: `yield from php(v)(host)` inside its own body, so
  the dependency is part of the stream rather than a program run first.
- **[nginx](nginx.md)** — the `dirmngr` prerequisite, the optional official PPA,
  the vhosts, a `nginx -t`-validated `nginx.conf`, service state, two handler
  channels. Three `changed_hosts` reads over two checkpoints.
- **[postgresql](postgresql.md)** — packages, cluster init, `postgresql.conf` and
  `pg_hba.conf`, the service, then users, databases and privileges. The only
  two-pass role, and the only one that ships two factories on two host views.
- **[datadog](datadog.md)** — the agent's signing keys and repository, the
  package, `/etc/datadog-agent`, `conf.d` checks, custom checks, two restart
  handlers. The first `async def` factory: five keys fetched control-side.
- **[composer](composer.md)** — the signature-verified installer, the phar move,
  `~/.composer`, an optional `auth.json`, global packages, the `$PATH` entry. The
  clearest `control` barrier: the signature fetch is gated on *this* host's need.
- **[drush](drush.md)** — the Drush launcher phar and its executable bit. Two
  `fact` barriers then `yield from`, and nothing else: no checkpoint, no handler,
  no `act`, no `control`.
- **[ntp](ntp.md)** — the NTP daemon, `tzdata`, the system timezone, the
  post-install `systemd-timesyncd` stop, the templated config, `restart ntp` and
  `restart cron`. Two checkpoints, read separately: two channels, two reports.
- **[docker](docker.md)** — the Docker CE repository with its URL-signed keyring,
  the edition packages, `daemon.json`, an ExecStart drop-in, the service, the
  `docker_users` memberships. Two checkpoints unioned into one handler.
- **[java](java.md)** — the distribution's default OpenJDK and an optional
  `JAVA_HOME` profile script. The cheapest end-to-end role: one barrier, one
  request wave, no checkpoint.
- **[memcached](memcached.md)** — the package, `/etc/memcached.conf`, the service,
  one restart handler. The checkpoint-only role: no facts at all.
- **[nfs](nfs.md)** — the NFS packages, the export directories, `/etc/exports`,
  the gated service, and `reload nfs`. The command-handler role: its handler is an
  `act(Shell, ...)` barrier running `exportfs -ra`, and the body raises its own
  `CommandError`.
- **[nodejs](nodejs.md)** — the NodeSource key and deb822 repository, the
  version-pinned `nodejs` package, npm's global prefix directory and `$PATH`
  script, the global packages and the optional `package.json` install. Zero
  barriers, one wave: nothing in the plan needs a read of the machine.
- **[pip](pip.md)** — the distro's pip package, the PEP 668 `EXTERNALLY-MANAGED`
  marker removed from the interpreter's own stdlib path, then the package list.
  One `fact(PythonVersion)` barrier, because that path varies by release.
- **[git](git.md)** — the apt-cache refresh and the `git_packages` install, as one
  request. The cheapest shape in the corpus: zero barriers, one wave, one
  operation, so a dry pass plans the whole wave rather than parking.
- **[security](security.md)** — the eight `sshd_config` hardening directives and
  the two access lists, both sudoers loops, Fail2Ban and unattended-upgrades, then
  `reload systemd`, `restart ssh` and `reload fail2ban`. The one-checkpoint, three
  channel case: a single `changed_hosts` report read three times, and ten edits of
  one file composing inside a single wave.
- **[certbot](certbot.md)** — the release-dispatched package, the configured
  certificates (standalone with its `systemctl` pre/post hooks, or webroot) and
  the renewal cron entry. The most barrier-dense role without a checkpoint: a
  `stat` gate and probe/create action gates per cert, all in one stream.
- **[redis](redis.md)** — the setgid `/etc/redis` directory, the rendered
  `/etc/redis/redis.conf`, the package, the service, one restart handler. The
  Debian-ordering case: the config requests precede the package install inside
  the single wave, because the install starts the daemon — and still the
  checkpoint-only shape, no facts at all.
- **[users](users.md)** — the groups, the accounts with their shells, homes and
  memberships, generated key pairs, authorized keys, profile blocks, and the
  deletions last. The pure request wave: every gate is vars-only, so the whole
  task list is one wave with zero barriers on the default path — even a dry
  pass plans it entire — and the one `fact(Users)` snapshot appears only under
  the `update_password: on_create` gate.
- **[hosts](hosts.md)** — renders `/etc/hostname` and `/etc/hosts` from the vars
  and one `DefaultIpv4Address` barrier, installing each file with a metadata
  companion. The template role without Jinja2: the render is plain Python,
  verified byte-identical to an Ansible-defaults render of the upstream
  templates, so no extra is needed — one barrier, one request wave, no
  checkpoint.
- **[kubectl](kubectl.md)** — the checksum-pinned release binary download, the
  version-suffixed install and the bare-name symlink. The single-gate role: one
  `fact(server.Arch)` barrier closes the first wave, the three tasks follow in
  the second, and both the architecture map and the checksum table fail fast
  before a byte is fetched.
- **[systemd](systemd.md)** — the load-path directory, the rendered unit or
  drop-in file, `Reload systemd units` and `Reload systemd daemon`, each unit's
  running and enabled state, and the opt-in uninstall. The per-request
  notification case: the checkpoint's report is sliced by request *name* as well
  as by host, because both upstream handlers loop over registered results and
  gate on each item's own `changed`. Its one `fact(Users)` read — the unit
  owner's home and uid — appears only under a per-user unit, so two hosts on the
  same variables can run two and three waves.
- **[glusterfs](glusterfs.md)** — the `dirmngr` and `software-properties-common`
  prerequisites, the optional Gluster PPA, the reinstall-on-PPA-add removal
  split, the server and client packages and the `glusterd` unit's
  running/enabled state. The single-gate, no-fact role: one `checkpoint` feeds
  the reinstall split, and the role has no handlers, so no second checkpoint
  exists to read.
- **[firewall](firewall.md)** — the `iptables` package, the once-per-machine
  flush, the rendered `/etc/firewall.bash` script and its `firewall.service`
  unit, the unit's running/enabled state, one restart handler. The script role:
  the rule set stays a rendered bash script rather than per-port `iptables`
  operations, and its `creates=` gate is a `fact` barrier feeding an `act` whose
  `Result` is data — the body checks the exit status and raises its own
  `CommandError`.
- **[zabbix_agent](zabbix_agent.md)** — the vendor keyring and repository lines
  with their stale-source purge, the agent packages and optional apt pin, the
  SELinux gate, the rendered `zabbix_agentd.conf`, the TLS-PSK file, the service,
  one restart handler. The second `async def` factory: the signing key is one
  inventory-wide control-side fetch, and the frozen `sign_keys` table raises
  loudly on a release it does not know.
- **[swap](swap.md)** — one swap file: the `/etc/fstab` entry keyed by the
  device, the file built at the wanted size at `0600` root:root, `mkswap`ed and
  `swapon`ed, `swapoff`ed before any removal or resize, plus `vm.swappiness`
  persisted and applied. The fact-fork role: one `fact(files.File)` read
  decides both include branches, and the four commands are `act` barriers the
  body fail-checks itself — on a converged host the whole command chain is not
  merely idempotent, it is absent.
- **[minio](minio.md)** — the service group/user, the five directories, the
  unpacked `minio`/`mc` bundle, the rendered `/etc/default/minio` and unit, the
  restart → console-port-wait handler chain, and the opt-in `mc` buckets,
  policies/users and `mc mirror` units. The `act`-heavy role: every imperative
  `mc` shell is a command barrier whose result is data, so the wave count grows
  with the provisioning lists.
- **[kibana](kibana.md)** — the `apt-transport-https`/`gnupg2` dependencies, the
  Elasticsearch key downloaded into `trusted.gpg.d`, the Elastic apt repository
  with its change-gated refresh, the package, the started/enabled service, the
  rendered `/etc/kibana/kibana.yml` and its metadata companion, one restart
  handler. The vendor-repo checkpoint-only role: the key and repository are
  operations with their own fact gates, so no barrier feeds them.
- **[repo_remi](repo_remi.md)** — installs Remi's RPM repository: the pinned-id
  GPG key import, then the staged release RPM install. The EPEL twin minus the
  configure stage: two `fact` barriers and a `control` fetch, no checkpoint, and
  a converged host emits no request at all.
- **[elasticsearch](elasticsearch.md)** — the `apt-transport-https`/`gnupg2`
  dependencies, the Elasticsearch key downloaded into `trusted.gpg.d`, the
  Elastic apt repository appended to `sources.list` with its change-gated
  refresh, the package, the rendered `elasticsearch.yml` and the version-selected
  heap file with their metadata companions, then the restart handler, the
  service and the port wait. The `flush_handlers` tail role: the post-checkpoint
  wave never disappears, and the stateless wait — never a notifier — is its one
  honest always-changed line (D6).
- **[node_exporter](node_exporter.md)** — the release tarball unpacked in
  `/tmp` and the binary moved to `/usr/local/bin`, the service group and
  account with their optional fixed ids, the rendered `node_exporter.service`
  unit, the running/enabled unit, its restart handler and the metrics-endpoint
  check. The all-four-barriers role: an `act` probe decides whether the
  download pair enters the stream at all, a `control` fetch resolves `latest`,
  the `checkpoint` reads the notification, and a `fact` verifies the running
  exporter — so the wave count moves with the host and the converged pass
  carries no download request rather than an unchanged one.
- **[ruby](ruby.md)** — the apt ruby packages with the gated cache refresh, the
  rubygems package, the `/etc/profile.d` gem-`$PATH` script, Bundler and the
  gem loop. The gem-loop twin of `pip`: one `fact(FileExists)` barrier, because
  the loop's `gem list` reads need the binary apt lands, and the item's
  `user_install` scope — not a run-as parameter — decides whether the
  request elevates.
- **[apache_php_fpm](apache_php_fpm.md)** — the two `mods-available` →
  `mods-enabled` proxy symlinks and the checkpoint-gated `restart apache`
  handler. The corpus's smallest role — upstream `tasks/main.yml` is one
  two-item `file` loop and nothing else — and the most wave-dependent one: a
  converged host's tree collapses to a single wave, because the handler yields
  no request and its wave is never appended.
- **[clamav](clamav.md)** — the `clamav` package trio, the install-gated
  `freshclam` run, the two `clamd.conf` edits with their mode companion, the
  optional `freshclam.conf` edits, both unit states, one restart handler. The
  command-task-plus-notify case: the play's `register` + `failed_when` +
  `notify` trio is one `act` barrier whose `Result` the body interprets — and
  whose successful run queues the restart itself, because a barrier is not a
  request.
- **[mailhog](mailhog.md)** — the MailHog and mhsendmail release binaries
  downloaded into `/opt/mailhog`, the f-string-rendered `mailhog.service` unit,
  started and enabled with the state-gated `daemon-reload`. The sibling of
  `kubectl` without the checksum table: one `fact(server.Arch)` gate picks
  between the role's two published Linux archives, five requests follow in the
  second wave, and a converged rerun re-fetches both URLs to compare content
  while issuing no reload at all.
- **[supervisor](supervisor.md)** — the pip-installed `supervisor` package
  (optionally pinned), the `0755` log and config directories, the rendered
  `supervisord.conf` with its `{SHA}`-hashed password, the program `conf.d`
  files in both directions, the rendered systemd unit, and one restart
  handler. The two-service-tasks-as-one case: `state: started` and
  `enabled: true`, each behind its own `when:`, collapse into a single
  `systemd.service` request whose false gates are the `None` sentinels — and
  the handler keeps the upstream `when: supervisor_started` as a body-level
  check, so a `started: false` host configures but never restarts.

* **htpasswd** — migrates `geerlingguy.htpasswd`: it installs the
  Apache utilities, reads each managed file back through a `FileContents`
  barrier, merges the configured credentials control-side — a line whose
  stored hash already verifies the desired password is reproduced verbatim,
  so random salts converge — and writes the result through spool-and-install
  with a `files.file` metadata companion.
- **[daemonize](daemonize.md)** — the `build-essential` toolchain the role's README
  names as a prerequisite, the release tarball extracted into the workspace behind
  Ansible's `creates:` gate, and — only on a host where the binary is not already
  on `PATH` — the `configure`/`make`/`make install` build as root. The single-fact,
  no-checkpoint build case: one `fact(server.Which)` read is the role's
  `register` + `when: rc != 0`, so a host that already built the binary never
  emits the build wave — and there are no handlers for a checkpoint to feed.

## Running one

Every role ships a standalone runner in
[`examples/roles/`](https://github.com/kimjarvis/reemote/tree/main/examples/roles)
that drives it against `inventory.json` in the current working directory:

```bash
uv run python examples/roles/nfs.py
```

Each runner loads and validates the inventory, resolves the role's variables, and
then runs the deploy twice — a `dry=True` preview, then the apply — printing the
wave tree after each. A dry pass stops at the first barrier that needs a real
report, so a checkpoint parks the run exactly where the notification read belongs
(`run_waves` still connects: a `fact` barrier is a read, not a proposal). Running
the program a second time against a converged host is the honest preview: every
line reports no change, no handler is queued, and the handler wave disappears.

The inventory entry's `family` decides what happens. Each role's body opens with
the OS guard — `if host.family != "Ubuntu": return …` — so a host on another
family emits no requests, no barriers and no report lines at all rather than
failing on the first `apt` call. The `family` field is a declaration the role
trusts. Nine roles read `fact(LinuxDistribution)` for their own reasons —
`certbot`, `docker`, `java`, `ntp`, `php`, `php_versions`, `postgresql`,
`repo_epel`, `repo_remi` — and
`certbot`, `java`, `ntp`, `postgresql`, `repo_epel` and `repo_remi` treat that
read as the check
that the machine matches the declaration, raising before any request is built
when it does not.

## Using a role inside your own deploy

A role is a component, not a program: it is a `Deploy` like any other, so a
parent embeds it with `yield from` and tags it with `scoped()` to say whose
requests were whose.

```python
from reemote import resolve_vars, run_waves, scoped
from reemote.roles import ApacheVars, PhpVars, apache, php


async def main(inventory):
    apache_vars = resolve_vars(ApacheVars, "apache", inventory)
    php_vars = resolve_vars(PhpVars, "php", inventory)
    versions: dict[str, str] = {}

    def site(host):
        apache_out = yield from scoped("apache", apache(apache_vars))(host)
        if not apache_out.version:   # apache's family guard skipped this host,
            return                   # so php does not stack on top of it
        versions[host.name] = apache_out.version
        yield from scoped("php", php(php_vars))(host)

    report, hosts = await run_waves(inventory, site)
    print(versions)
```

`scoped(label, deploy)` stamps `label` onto the front of every item's report path
— the wave tree then says which role emitted each line — while forwarding the
driver's `send()` values inward and the child's `return` outward, which is what
lets the parent capture `apache_out` with an ordinary assignment. Embedding a role
without it is legal but unlabeled; the older `compose` and `sub_deploy` helpers
sequence `OperationRequest`s only and raise `DeployRequestError` the moment a child
yields a barrier, so they cannot carry a role at all.

The worked example is
[`examples/roles/composing_roles.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/composing_roles.py),
which composes `apache` and `php` and consumes their outputs to drive the parent —
including the detail above, that a dry pass never publishes outputs because the
body parks on the first barrier before the capture line runs. The barrier and
`scoped` contract is [Barriers](../stages.md); the variables channel — model
defaults, then the entry's role mapping, then call-site `overrides` — is
[Role variables](../rolevars.md#precedence).

## What every role guarantees

- **Typed variables, not `host.extra` spelunking.** Each `<Role>Vars` is a frozen
  pydantic model with bare field names, and a bad value raises in `resolve_vars`
  before any host is contacted.
- **Stable request names.** The `name=` on every request is the notification
  channel `changed_hosts` matches on, so a rename is a behaviour change; the
  handler factories in [Handlers](../handlers.md) generate theirs.
- **Transfers never elevate.** Root-owned config files go through the
  spool-and-install family — `files.install`, `files.transform`,
  `files.download` — with `sudo=True` on the request, and a `files.file` metadata
  companion where the Ansible template also owned `mode=`.
- **Two-pass convergence.** Every role's offline test runs `run_waves` twice on a
  `SimulatedHost` and pins both trees, so the second pass reporting no change is
  an assertion, not a hope.
- **Documented deviations.** Each page closes with what the role does *differently*
  from the Ansible version and what was scoped out — an opt-in
  build-from-source branch, a second OS family — so the gap is stated rather than
  discovered.
