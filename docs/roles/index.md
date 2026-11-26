# Roles

reemote ships eighteen configuration roles as **library code**. Each one is the
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
[`datadog`](datadog.md) is the corpus's one `async def` factory, and its call-site
is `await datadog(v)`. What a factory returns is one generator per host that
yields operation requests **and** barriers: a `fact` read, an `act` command, a
`checkpoint` that closes the current wave and reads its report, a `control` fetch.
That is what lets the reads an Ansible play took *between* its steps — a version
probe, a `stat` loop, a notification check — live inside the deploy instead of
forcing a plan-then-apply split. [`run_waves`](../drivers.md#wave-loop) is the
driver that understands barriers; `run()` is not.

## The catalogue

| Role | Migrates | Target | Waves | Outputs | `reemote[templates]` | `run_waves` passes |
| --- | --- | --- | --- | --- | --- | --- |
| [repo_epel](repo_epel.md) | [`geerlingguy.repo-epel`](https://github.com/geerlingguy/ansible-role-repo-epel) | RedHat | 3–4 | none | no | 1 |
| [apache](apache.md) | [`geerlingguy.apache`](https://github.com/geerlingguy/ansible-role-apache) | Ubuntu | 3 + one per SSL vhost | `ApacheOutputs(version, service)` | **yes** | 1 |
| [php](php.md) | [`geerlingguy.php`](https://github.com/geerlingguy/ansible-role-php) | Ubuntu | 12 fresh / 11 converged | `PhpOutputs(version, extension_conf_paths, webserver_notified)` | no | 1 |
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

## Reading the columns

- **Waves** is a range, not a constant. Each barrier closes its own wave and a
  wave with no records is not appended, so the count moves with the host: how
  many SSL certificates `apache` has, how many global packages `composer`
  requires, whether a handler fired at all. "Converged" is the count on a rerun
  against a host that already matches — the interesting number, because a role
  whose wave count *grows* on the second pass has a request that never settles.
- **Outputs** names the dataclass the deploy returns to a composing role. Two
  roles publish one: `apache` and `php`. `php_xdebug` exists to consume
  `PhpOutputs` — the PHP version and extension paths it used to carry as a copied
  constant. Every other role returns `None`, and does so on its family-guard's
  early return too.
- **`reemote[templates]`** is needed by the six roles that render configuration
  through Jinja2 or PyYAML — `apache`, `datadog`, `memcached`, `nginx`, `ntp` and
  `postgresql`, the list [`reemote.roles._render`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/_render.py)
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
trusts. Six roles read `fact(LinuxDistribution)` for their own reasons —
`docker`, `java`, `ntp`, `php`, `postgresql`, `repo_epel` — and `java`, `ntp`,
`postgresql` and `repo_epel` treat that read as the check that the machine matches
the declaration, raising before any request is built when it does not.

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
