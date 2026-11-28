# files module

::: reemote.modules.files.files
    options:
      show_root_heading: false
      members:
        - Directory
        - DirectoryEntries
        - DirectoryEntry
        - File
        - FileContents
        - FileExists
        - FileFlags
        - FileLink
        - FindDirectories
        - FindFiles
        - FindFilesInfo
        - FindInFile
        - FindInFiles
        - FindLinks
        - FoundFile
        - Link
        - LocalSha1
        - Md5File
        - RemoteFileSha1
        - SameFile
        - Sha256File
        - Sha384File
        - Socket
        - copy
        - directory
        - download
        - edit
        - fetch_url
        - file
        - flags
        - get
        - ini_option
        - install
        - link
        - move
        - put
        - transform
        - unarchive



<!---

`reemote.modules.files.files` combines the file facts and the file operations
in a single source, so the read side (`FileLink`, `FileExists`,
`LocalSha1`, `RemoteFileSha1`, `Sha256File`, `Sha384File`, `Md5File`,
`SameFile`, `FileContents`, `File`,
`Directory`, `Link`, `Socket`,
`FileFlags`, `FindFiles`, `FindDirectories`, `FindLinks`, `FindInFile`,
`FindInFiles`, `FindFilesInfo`, `DirectoryEntries`)
sits beside the `files.link`, `files.file`, `files.put`, `files.get`,
`files.edit`, `files.install`, `files.transform`, `files.ini_option`, `files.move`, `files.copy`, `files.directory`,
`files.unarchive`
and `files.flags`
operations that consume it.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `LocalSha1` is the
one fact whose input is not the host: it hashes an IO-like stream on the
*control* machine — the source `files.put` uploads or the destination
`files.get` downloads into — which both compare against the remote file's hash.

`File` mirrors the baseline's `files.File`: it guards the path with
`test -e`/`test -L`, then describes it with GNU `stat -c`, falling back to
`ls -ld`, and returns the metadata dict — `user`,
`group`, `mode` (the octal-looking int the baseline returns, e.g. `644` or `4755`,
setuid/setgid/sticky bits included), `size`, and `atime`/`mtime`/`ctime` as
*naive UTC* `datetime`s (the baseline's convention; the `ls -ld` fallback provides
only an mtime, so its atime/ctime read as `None`). A path that does not exist
reads as `None`; one that exists but is not a regular file (a directory,
symlink, socket, ...) reads as `False` — `Directory`, `Link` and `Socket`
are the directory-, link- and socket-accepting siblings, and only `Link`'s
dict carries the `link_target` key. Like
the baseline, a leading `~/` stays unquoted so the remote shell expands it. Where
the baseline reads once, reemote reads twice: a path that comes back empty is
re-probed elevated with `sudo`, because `test -e` cannot tell "absent" from
"hidden by a parent directory the login user may not search" — a `02770`
`redis:redis` `/etc/redis` refuses the lookup and the shell reports the `EACCES`
as "no such file" (issue #70). A separate unprivileged compound walks the
path's directories outward to the nearest existing one and asks its `-x` bit, so
only a genuinely invisible path costs the elevation and only a negative
*elevated* answer reads as absent. `Directory`, `Link` and `Socket` inherit the
same two-tier read. Where
`FileExists` is the cheap `test -e` probe for existence-only checks, `File`
is the richer stat-level read — the one `files.file` consumes to compare the
mode and owner it is asked for, and the right fact for auditing ownership,
permissions or timestamps.

`Directory` mirrors the baseline's `files.Directory` — its `File` subclass with
`type = "directory"`: the very same guard, stat/ls chain and metadata dict,
but the path's type flag must be `d`, so a regular file, symlink, socket, ...
reads as `False` instead. Use it to audit a directory's ownership, mode
(setuid/setgid/sticky bits included — `/tmp`'s `1777`, a setgid share's
`2775`) or timestamps before placing files inside it. It describes the
directory *itself*, never the names inside it — for the listing see
`DirectoryEntries` (shallow, with types) or `FindDirectories` (recursive
paths).

`Socket` mirrors the baseline's `files.Socket` — the same `File` subclass with
`type = "socket"`: the very same guard, stat/ls chain and metadata dict,
but the path's type flag must be `s`, so a regular file, directory,
symlink, ... reads as `False` instead. Use it to audit a daemon's
listening socket — `/var/run/docker.sock`, a database or agent IPC
socket — where owner and mode decide which users may talk to the daemon.

`Sha256File`, `Sha384File` and `Md5File` mirror the baseline's hash fact family:
one guarded command tries the tool spellings in order (`sha256sum`,
`shasum -a 256`, `sha256`, ...) reading the file through *stdin* (`< path`)
so the checksum tool never sees the filename — GNU coreutils escapes
backslash/newline-bearing names in its argv-form output, which the stdin
form avoids. Both output spellings parse: the GNU/shasum `<hash>  -` line
and the bare BSD digest. A missing file — or a host with none of the tools
installed — exits zero through the `|| true` guard and reads as `None`.
`Sha256File` is the digest Ansible's `stat` module defaults to and the one
repository/keyring verification recipes compare; `Md5File` is for legacy
checksum manifests, not a security boundary. `RemoteFileSha1` predates the
family and deliberately keeps its simpler `sha1sum <path>` probe: it backs
the `files.put`/`files.get` idempotence contract and `files.install`'s binary
bytes path.

`FindFiles`, `FindDirectories` and `FindLinks` mirror the baseline's find-path
family — the enumeration facts the stat-only `File`/`Directory` metadata
facts cannot replace: they discover names the deploy author does not know
("every `conf.d/*/conf.yaml`", "every legacy apt `.list` file"). One
`find <path> -type f|d|l` plus the shared filters returns the matching
paths verbatim in find's traversal order, always guarded with `|| true` so
a missing start path reads as `[]`. The filters are the baseline's set:
`fname`/`iname` name globs, `maxdepth`, `size`/`min_size`/`max_size` (ints
or human-readable spellings like `"1KB"`/`"2MiB"`, always rendered in
bytes), `regex`, `path_match`, and the raw `args` escape hatch (which
suppresses a typed filter spelling the same predicate); `quote_path=False`
leaves the start path and name globs to the shell. The bare `list[str]`
return is the agreed exception to the TypedDict guideline — a path string
carries all the information. They are the read side of a gather-then-branch
two-deploy cleanup: gather the hits in one deploy, compute the closure
(e.g. found minus tracked), and remove the surplus with `files.file`/
`files.directory` in the next — see the
[conf.d cleanup example](../examples/demonstrations/composition.md).

`FindInFile` mirrors the baseline's `files.FindInFile`: `grep -e <pattern>`
prints the matching lines, and when grep fails the fallback arm
distinguishes *no match* from *no file* — an existing regular file echoes
an exists sentinel that reads as `[]`, a missing one echoes nothing and
reads as `None`. `extended_regex=True` selects `grep -E`;
`interpolate_variables=True` double-quotes the pattern so the remote shell
expands `$VAR` references, otherwise it is single-quoted literally. Use it
to audit configuration content — "is this legacy source still enabled?",
"does the agent config still name the old endpoint?" — without pulling the
whole file through `FileContents`.

`FindInFiles` is a reemote original (the baseline ships no counterpart) that
closes the gap between the *name*-selecting find family and the *single-file*
content grep of `FindInFile`: one recursive `grep -rlE <pattern> <path>`
returns the **paths** of every file under a tree whose *content* matches the
extended regex — `-r` recurses, `-l` prints one path per matching file (never
their lines), `-E` selects the extended regex. It always answers with a
`list[str]`: the `|| true` guard masks no-match (grep exit 1), an absent or
unreadable start path and a missing grep binary alike to `[]`, so unlike
`FindInFile` there is no `None` — a recursive search over a tree has no single
"the file is missing" answer to distinguish. `interpolate_variables=True`
double-quotes the pattern so the remote shell expands `$VAR` references,
otherwise it is single-quoted literally. This is the read half of the very
common "delete unmanaged files matching content" Ansible idiom — an
APCu/OpCache stale-cache sweep, a legacy-endpoint config cleanup — and it
feeds the same gather-then-branch two-deploy split `FindFiles` does: gather
the hits in one deploy, compute the closure (found minus managed), and remove
the surplus with `files.file`/`files.directory` in the next.

`FindFilesInfo` is a reemote original (the baseline ships no counterpart): the
find family's filters plus GNU `find -printf` gather path, size, mode,
user, group and mtime for every hit in *one* command — the metadata
Ansible's `find` module returns per hit, which bare `FindFiles` would need
one stat-level `File` read per hit to match. Entries are `FoundFile` dicts
sorted by path for a deterministic listing; the mtime is the File
convention's whole-second naive UTC datetime (`None` when unparseable).
GNU-only: BSD find has no `-printf`, and the `|| true` guard masks that to
`[]`. Use it to branch on per-hit metadata — "delete logs older than X",
"report world-writable files under the share" — in a single read.

`DirectoryEntries` is a reemote original: the shallow listing the
stat-only `Directory` fact cannot give — one
`find <path> -mindepth 1 -maxdepth 1 -printf '%f\t%y\n'` sweep reports each
immediate entry's basename and type (`DirectoryEntry`: "file",
"directory", "link", "socket", "fifo", "block", "character") without
recursing, dotfiles included, the directory itself excluded, sorted by
name. It is Ansible's `find` with `recurse: false` — the cheap "what is in
this directory right now?" read behind legacy-file cleanup and
drop-in-directory audits. GNU-only like `FindFilesInfo`; a missing start
path reads as `[]`.

`Link` mirrors the baseline's `files.Link` — the same `File` subclass with
`type = "link"`: the very same guard, stat/ls chain and metadata dict, but
the path's type flag must be `l`, and the dict additionally carries
`link_target` — the target path with the stat/ls quoting stripped (GNU
`'name'`, older GNU/RHEL 6 `` `name' ``, uutils `"name"`), exactly as the
link records it, so a relative target stays relative. The `test -L` guard
lets a *dangling* symlink through to the `ls -ld` fallback, so a broken
link is still reported with its target. Where `FileLink` is the cheap
`readlink` probe `files.link` consumes, `Link` is the richer stat-level
read for auditing the link itself — ownership, mode, timestamps —
alongside where it points.

`SameFile` mirrors the baseline's `files.SameFile`: one `test <path> -ef <target>`
probe whose `&& echo true || echo false` guard always exits zero, returning
the plain bool — two paths name the same device *and* inode, or they do not.
A nonexistent or unrelated path is the plain `False` answer, never a failed
read. It is the hard-link half of `files.link`'s idempotence: a hard link is
not a symlink (the `Link` fact reads `False` for one), so the operation
re-reads the path through `File` and asks `SameFile` whether that file *is*
the requested target. Use it on its own to audit whether a `cp`/`ln`
destination truly shares an inode with its source. Unlike the `File` family
there is no leading `~/` exception — both paths are quoted verbatim, so a
tilde path simply reads as an absent file.

`FileFlags` is the Linux reading of the baseline's BSD `files.Flags` (the
developer-chosen adaptation): it guards the path with `test -e`, then runs
`lsattr -d` with stderr masked, and returns the attribute letters of the
row's fixed-width flags field, padding dashes dropped and sorted — `i` for
immutable, `a` for append-only, ... An absent path answers `[]`, exactly as
the baseline's `Flags` process answers for no output, and a filesystem without
ext2-family attributes masks to the same `[]`. Use it to audit the
attributes behind `files.flags` before a `+i` plan strands a removal, or to
document what an append-only log file really carries. Like `File`, a
leading `~/` stays unquoted so the remote shell expands it.


## Operations

Operations live in namespace modules so a deploy reads `files.link`,
`files.file`, `files.put`, `files.get`, `files.edit`, `files.install`,
`files.transform`, `files.ini_option`,
`files.move`,
`files.copy`, `files.directory`, `files.unarchive` and `files.flags`,
mirroring the baseline's
API. Every
operation is an async generator taking an [`OpContext`][reemote.core.OpContext],
awaiting facts via `ctx.facts`, setting `ctx.changed`/`ctx.reason` where a
change is needed, and yielding one [`Command`][reemote.commands.Command]
descriptor per mutation.

`files.put` and `files.get` yield the *non-shell* descriptors:
`PutCommand` and
`GetCommand` have no `shell()` —
[`send_command`][reemote.drivers.send_command] dispatches them over the host's
asyncssh connection with scp instead of running shell text, so the `sudo`/`su`
flags do not apply and the remote path must be writable (put) or readable (get)
by the login user. Each takes an **IO-like object only** on the control-machine
side (`src: IO[Any]` for put, `dest: IO[Any]` for get — where the baseline accepts a
local filename too, a path string is deliberately not supported): put rewinds
its source before every read, get rewinds and truncates its destination before
writing and rewinds it again after, and the same object must be passed in both
phases (request equality compares it by identity). Idempotence is content-based
in both directions: `files.put` compares the source's `LocalSha1` against the
remote file's `RemoteFileSha1` and uploads only on a mismatch, while
`files.get` compares the remote file's `RemoteFileSha1` against the
destination's current `LocalSha1` and downloads only on a mismatch. The baseline's
`files.get` options `add_deploy_dir`, `create_local_dir` and `force` are
dropped — there is no local path to place, create or force.

`files.edit` is the generic content-editing primitive: it reads a file's
current text with
`FileContents`, applies a caller-supplied *pure* `transform`
(`str | None -> str`), and yields `files.put`'s `PutCommand` only when the
result differs from the current content. Any "edit a file in place" task — a
`sysctl` key, a resource limit, a managed block — is then just a transform,
with no new operation; a few common formats have since grown dedicated
operations built on the same read-transform-write idea
(`server.etc_hosts` for the hosts file, `sysctl.setting` for sysctl keys),
while the generic primitive stays the answer for everything else. The baseline's `files.block` is covered exactly that way: the
`block_transform` factory in the
[block example](../examples/modules/files.md#managing-a-marked-block-baseline-filesblock-parity)
builds a marked-block transform from the baseline's option set (content, markers,
regex positioning, removal), so no bespoke block editor is ported. The
transform must be idempotent (so a second edit is a
fixpoint) and a stable, hashable module-level callable (it is compared by value
across phases, so a lambda built inside the deploy generator would trip
`DeterminismError`). Because the write rides the same unprivileged scp transfer
as `files.put`, the target must be writable by the login user; a root-owned file
such as `/etc/hosts` must be transformed into a writable path and moved into
place with `files.move` flagged `sudo=True` — or written in one deploy with
`files.install` below.

`files.install` is a reemote original: the generic "write text or binary
content to a root-owned path" operation the namespace-specific
spool-and-install operations
(`apt.sources_file`, `dnf.repo`, `systemd.dropin`) each spell out for their own
config paths. It reads the destination's current content — `FileContents` for
a `str`, the binary-safe `RemoteFileSha1` for `bytes` (issue #23) — and,
only when it differs from the desired `content`, yields two descriptors from
one request — an `InstallSpoolCommand` (text) or `InstallBinarySpoolCommand`
(bytes) transfer spooling the content to a
dest-derived `/tmp` path over unprivileged scp, then an `InstallCommand` shell
running the elevated `mkdir -p`/`cp`/optional `chown`/`chmod`/`rm -f` install
wrapped in `sh -c`
(so a `sudo=True` request elevates every step, not just the chain head). Because
both descriptors ride one request, the whole write fits a single deploy —
unlike the put-then-`files.move` recipe, which spans two runs. `content` is a
plain `str` or `bytes` — both hashable and equal across phases, so no
identity-stable stream is needed at the call site. The bytes path is the
binary spool-and-install `files.unarchive`'s `UnarchiveSpoolCommand`
pioneered, generalized from "extract an archive" to "install a file": a
`.phar` launcher lands at `/usr/local/bin/drush` in one `sudo=True` request,
idempotent on the sha1 digest (no UTF-8 decode, so any bytes work). The sha1
read-back is unprivileged, so give a binary install a login-readable mode
(the `0755` launcher shape) — a login-unreadable binary destination reads as
absent and re-installs every deploy.
The spool name is the destination's sha1 prefix — deterministic across phases
and collision-free between different targets, though predictable on
world-writable `/tmp` (collision avoidance, not a security boundary). `mode`
accepts octal-digit text only (`"0644"`, `"600"`; a symbolic mode raises
`ValueError`) and is applied on every write, but idempotence is content-only:
a mode-only drift does not re-install (use `files.file` to converge metadata).
The optional `user`/`group` parameters ride the same contract: they add a
`chown` (a group alone renders `chgrp`) inside the elevated install chain,
before the `chmod` — a chown clears setuid/setgid bits, so the mode is applied
last — and the file lands with the requested owner instead of the root:root
the elevated `cp` creates. The owner is applied on every write but never
compared, so an owner-only drift is `files.file`'s job too, and the named
account must already exist (provision it with `server.user`/`server.group`
first). The optional `validate` parameter is the atomic validate-then-install
hook Ansible's `template`/`copy` `validate:` provides: a command template run
on the spooled `/tmp` file *between* the transfer and the install, so a
non-zero exit raises `CommandError` and the `InstallCommand` never runs — the
destination keeps its previous content and a bad config never lands, because
the bytes checked are the very bytes about to be copied into place. The
`{spool}`/`{dest}` placeholders are substituted with shell-quoted paths
(`validate="nginx -t -c {spool}"`), keeping the template a hashable,
deterministic `str`, and the command inherits the request's privilege flags, so
a `sudo=True` install validates elevated (as root) — matching Ansible's
become-scoped `validate` and letting a checker read every file the staged
config references. It is the narrow, safe slice of "a command inside a
deploy": scoped to validating the content being installed at one well-defined
injection point, not a generic shell operation, so the action/deploy boundary
stays intact. The text read-back is two-tier: `FileContents` cats unprivileged
first and, when the login user cannot read the file (a `0640` service-account
config), re-reads it elevated with `sudo` — so content idempotence holds for
restrictive modes too, and the re-read's run-as user and any password come
from the inventory `privilege` data. The absent arm is two-tier too (issue #70):
a file inside a directory the login user may not search prints nothing at all,
so the traversal gate turns that absence into one elevated re-probe before the
operation plans a write.
There is no `present=False` branch — `files.file(present=False)` owns removal.
Installing under `/etc` needs root, so flag the request `sudo=True`; the
transfer stays unprivileged regardless.

`files.transform` is the root-owned sibling of `files.edit` and a reemote
original: it applies the same caller-supplied *pure* `transform`
(`str | None -> str`) to a file's current text, but writes the result through
the spool-and-install path instead of `files.edit`'s unprivileged scp. It reads
the target with `FileContents`, hands the text (`None` when the file is absent)
to `transform`, and — only when the result differs — delegates the write to
`files.install` above: an `InstallSpoolCommand` transfer spools the transformed
text to a dest-derived `/tmp` path over unprivileged scp, then an
`InstallCommand` shell overlays the target with it under an elevated `sh -c`.
This closes the gap `files.edit` cannot: because the put/edit transfers never
elevate, `files.edit` needs a login-user-writable target and cannot converge a
root-owned config such as `/etc/httpd/conf/httpd.conf` in one deploy, whereas a
single `sudo=True` `files.transform` request both preserves the rest of the
protected file and lands the edit. It is the generic form of the pattern
`server.etc_hosts` hard-wires to `/etc/hosts`. The `transform` obeys the same
two rules as `files.edit` — idempotent (`transform(transform(x)) ==
transform(x)`, so a second run is a fixpoint) and a stable, hashable
module-level callable (compared by value across phases, so a lambda built
inside the deploy generator would trip `DeterminismError`); see
[Migrating a protected-file line edit](../migrating.md#migrating-a-protected-file-line-edit-lineinfile)
for how to write one. `mode`/`user`/`group`/`validate` are forwarded straight
to the install, so the request that lands the edit can also set permissions and
ownership and validate the staged spool before it lands (a non-zero exit aborts
before the target is touched — the same atomic validate-then-install hook
`files.install` above provides); idempotence stays content-only (a mode- or
owner-only drift does not
re-install — `files.file` converges metadata), and the read-back is install's
too — a file the login user cannot read is re-read elevated, so a restrictive
mode does not re-install every deploy. Editing under `/etc` needs root, so
flag the request `sudo=True`; the spool transfer stays unprivileged regardless.

`files.ini_option` is Ansible `ini_file` parity (issue #24): it sets one
option in one section of a root-owned INI file, preserving every other
section, option, comment and blank line. It is `files.transform` with the
section-scoped edit built in — read `FileContents`, apply a pure helper that
rewrites only the one active option line inside the named section (`=` and
`:` separators both recognized, case-sensitive, first occurrence wins),
inserts a missing option after the section's last non-blank line, or appends
a missing section blank-line-separated at EOF — then delegate the write to
`files.install` above. Neither neighbour fits the shape: `dnf.repo`
constructs a whole *single-section* `.repo` file (it would clobber the
RPM-shipped `epel.repo`'s `[epel-debuginfo]`/`[epel-source]` sections), and
`files.edit` is line-scoped (a bare `^enabled=` replace would flip every
section's `enabled` line). The edit is hand-rolled line surgery rather than a
stdlib `configparser` round-trip on purpose: configparser drops comments,
normalizes spacing and reorders options, breaking both preservation and
content idempotence. A line already rendered exactly as requested returns
the content verbatim, so the edit is a fixpoint and a second identical
request reports no change; the file's trailing-newline state is preserved.
`no_extra_spaces=True` renders `option=value` instead of Ansible's default
`option = value`; `create=False` mirrors `ini_file`'s `create: no` (an absent
file raises `ValueError` instead of being created). Deliberately unshipped:
`state: absent` removal (a `files.transform` drop-line transform owns it),
`allow_no_value`, `backup` (non-deterministic, the project-wide drop) and
interpolation (values are literal text). `mode`/`user`/`group`/`validate`
forward straight to the install and idempotence stays content-only, exactly
as for `files.transform`; flag the request `sudo=True` for `/etc` targets.
See [Migrating an INI option edit](../migrating.md#migrating-an-ini-option-edit-ini_file)
for the `ini_file` mapping.

`files.download` is the general download-to-path operation and a reemote
original: it takes an `http(s)` `url` and lands its body at a (root-owned)
`dest` in one `sudo=True` request, retiring the hand-rolled `urllib.request` +
`io.BytesIO` workaround that `dnf.key`/`apt.key`/`gpg.key` force (each
*rejects* a URL `src`) and giving the package modules a text URL fetch to point
at (`dnf.repo`/`dnf.rpm`/`apt.deb` reject a URL `src` because they build or
install a host-side binary file, not because reemote cannot download). The fetch is *control-side* — the control machine
opens the connection (`urllib` in a worker thread), not the target host, so the
download works when only the control side has internet egress and the host never
needs `curl`/`wget` (the opposite of Ansible's `get_url`, which fetches on the
target). The body shares the scheme guard and checksum contract with
`files.fetch_url` below through the `_fetch_url_verified` seam, decodes a
UTF-8 body to text (a binary body rides install's bytes path as raw bytes)
and delegates the write to
`files.install` above, so a download inherits install's content-only idempotence
(a converged file yields zero commands), its dest-derived `/tmp` spool, its
owner/mode handling, its optional `validate` hook and its two-privilege-level
signature (unprivileged scp transfer + elevated `sh -c` install). The optional
`checksum` is Ansible's `get_url` spelling —
`"<algorithm>:<hexdigest>"` (eg `"sha256:9f2c..."`) — verified against the
fetched bytes before anything is written; pinning it is also what makes the
two-phase fetch deterministic, because the body runs once per phase (prepare,
then apply, so the URL is fetched twice per host) and both phases verify the
same digest and so install byte-identical content. Text or binary (issue #23,
full `get_url` parity): a text body installs through `FileContents`
idempotence, a binary body (a `.phar` launcher, a `.rpm`) through the
binary-safe `RemoteFileSha1` idempotence — never the text-decode `ValueError`.
The canonical binary case is the geerlingguy.drush launcher:
`files.download(url=<drush.phar URL>, dest="/usr/local/bin/drush", mode="0755",
sudo=True)`.
`mode`/`user`/`group`/`validate` forward straight to the install, `url` must be
`http(s)` (a `file:`/`ftp:` scheme raises `ValueError` before any fetch), and
idempotence and the two-tier text read-back (a file the login user cannot read is
re-read elevated) are install's; a binary install's sha1 read-back is
unprivileged, so give it a login-readable mode. Downloading
under `/etc` needs root, so flag the request `sudo=True`; the spool transfer
stays unprivileged regardless. See
[Migrating a URL download](../migrating.md#migrating-a-url-download-get_url)
for the `get_url`/`rpm_key` mapping.

`files.unarchive` is the pyinfra `files.unarchive` migration that closes
issue #21 (Ansible `unarchive` parity -- the php-xdebug source tarball) and
carries issue #58's `mode`: it extracts an archive into a remote directory,
idempotently gated by `creates`.
`src` is auto-detected: an `http(s)` URL is fetched *control-side* as binary
bytes (the `_fetch_url_bytes` seam `files.fetch_url` rides, without its
text-only decode -- a `.tgz`/`.zip` body never raises), spooled to a
URL-derived `/tmp` name over unprivileged scp, extracted, and the spool
removed; any other `src` is a remote path already on the target, extracted in
place and left alone (pyinfra's `remote_src=True`). The `remote_src` option
itself is dropped -- the scheme decides -- because pyinfra's
`remote_src=False` default uploads a *controller-side path*, a shape reemote's
IO-only transfer decision deliberately does not have. The format derives from
the archive's basename, case-insensitively (a URL query string never hides
it): the tar family (`.tar`, `.tar.gz`/`.tgz`, `.tar.bz2`/`.tbz2`,
`.tar.xz`/`.txz`, `.tar.zst`) extracts with `tar` and the compression flag the
suffix names, `.zip` with `unzip -o`; anything else raises `ValueError` before
any fetch or fact read. `creates` is Ansible's `creates:` gate, resolved
in-operation through the `FileExists` fact (`test -e`, so it sees the
extracted *directory* Ansible names, not just regular files): a converged
host reports `changed=False` with zero commands and skips the fetch entirely.
Without it the operation is stateless like pyinfra's -- extraction always
yields and always reports changed (there is no fact that can compare an
extracted tree). `dest` is created with its parents when absent (the issue's
requirement -- pyinfra raises on a non-existing directory); a `dest` held by
a non-directory and a remote `src` that is *not a regular file* raise
`ValueError` in every phase. An **absent** remote `src` raises in a
materialization that can send commands, and reports a plan line in one that
cannot -- the prepare of a `dry=True` `run` or a `dry=True` `run_waves` step
(issue #73) -- because the same deploy may stage the archive first:
`files.install` the tarball, then extract it, and the preview says the
extraction is planned once it is staged instead of failing on a path only the
apply creates. `files.move`/`files.copy` degrade the same way. `extra_opts` is a tuple, not pyinfra's list
(request arguments must be hashable), placed between the tool's flags and the
archive path -- tar keeps `-f` adjacent to the path so an extra is never
mistaken for it. `user`/`group` yield a recursive `chown` of `dest` after the
extraction (the named account must already exist), and `mode` (issue #58,
Ansible's `unarchive` `mode:`) yields a recursive `chmod` of `dest` after the
chown -- a chown clears setuid/setgid bits, so the mode lands last, the
`files.install`/`copy`/`move` ordering. The mode accepts every `files.file`
spelling (`755`, `"755"`, `"0755"`, `"0o755"`, or a symbolic mode) and is
normalised through `_ensure_mode_int`, so a non-octal typo raises `ValueError`
before any fetch. Unlike Ansible -- which modes only the members it extracted
-- the `chmod -R`/`chown -R` cover **everything under `dest`**, so extract into
a dedicated directory rather than a shared one such as `/tmp`. Both are emitted
unconditionally whenever the extraction runs, so both phases yield the identical
command sequence and convergence stays `creates`-scoped: a mode- or owner-only
drift on an already-extracted tree is not re-applied (no fact enumerates the
members) -- manage such a tree's metadata with `files.directory(recursive=True)`.
The optional `checksum` is
a reemote addition, the `files.download` contract
(`"<algorithm>:<hexdigest>"`): a URL-fetched body must match it or
`ValueError` raises before anything is staged -- pinning it is what keeps the
two-phase fetch deterministic, because the body runs once per phase and both
phases must stage byte-identical content. The extraction, mkdir, cleanup,
chown and chmod are shell text, so flag the request `sudo=True` for a root-owned
`dest`; the spool transfer stays unprivileged regardless.

`files.fetch_url` is the control-side GET helper and a reemote original: it
fetches an `http(s)` `url` on the *control* machine (`urllib` in a worker
thread, the same seam `files.download` rides) and returns the response body
to the program as UTF-8 text — Ansible's `uri` with `return_content: true`
mapped to a plain coroutine. It is **not an operation**: it takes no
`OpContext`, is never passed to `op()`, and is called from `main()` (or any
control-side code), so the determinism recipe is to fetch once and freeze the
returned text into the per-host plan the deploy consumes — the composer
installer-signature shape, where a fetched sha384 digest becomes
`files.download(checksum=f"sha384:{signature}")`'s pin. `files.download`
shares the fetch seam (`_fetch_url_verified`), so the two share one scheme
guard (a
`file:`/`ftp:` scheme raises `ValueError` before any fetch) and one
Ansible-style
`checksum` contract (an optional `"<algorithm>:<hexdigest>"` verified against
the raw bytes before the caller sees the content); this helper alone applies
the text-only decode (a binary body raises `ValueError` — installing one at a
path is `files.download`'s or `files.install(content=<bytes>)`'s job). On-target HTTP —
a POST, a webhook, anything that must run on the host — stays a `Shell`
action outside the deploy. See
[Migrating a content-returning GET](../migrating.md#migrating-a-content-returning-get-uri)
for the `uri`/`return_content` mapping.

`files.move` mirrors the baseline's `files.move`, extended with the issue #22
rename/metadata parity: `src` is a remote path and `dest` is auto-detected —
an existing remote *directory* receives `basename(src)` (the baseline's only
form), any other path is the full target path, so the move **renames**
(`mv src dest`). The rename form validates the target's *parent* directory
instead: an absent or non-directory parent raises `ValueError`.
`overwrite=True` removes an existing target first (`rm -rf`) instead of
raising, and `mode`/`user`/`group` yield a `chown`/`chmod` on the moved path
after the `mv` (chown first — it clears setuid/setgid bits). It is
**stateless** like the baseline — a valid move always yields a command,
there is no idempotent no-op branch — and it reads `File(src)`,
`Directory(dest)` and `File(full_dest)` to validate. An existing target without
`overwrite` raises `ValueError` in every phase. An absent source raises in a
materialization that can send commands and — restoring the baseline's
phase-aware softness for the one case it protected (issue #73) — reports a plan
line in one that cannot: a `dry=True` `run` or a `dry=True` `run_waves` step
previews a "create a file then move it" deploy, while a real `run` still
prepares strictly and fails before it mutates anything, so such a flow spans
two runs whenever it might be applied. All
commands are shell text, so `sudo=True` elevates the whole move — a
vehicle for the two-run spool-and-install shape: `files.edit` spools the
transformed content into a login-user-writable path in one run, then
`files.move` installs it into a root-owned directory elevated in a second
run. For whole-content writes prefer `files.install` above (one deploy),
for an idempotent remote-to-remote install prefer `files.copy` below, and
for the hosts file specifically the dedicated `server.etc_hosts`
operation now covers the old `/etc/hosts` recipe natively.

`files.copy` mirrors the baseline's `files.copy`, extended with the issue #22
Ansible `copy remote_src: true` parity — the copy renames, sets metadata and
converges idempotently on content drift, in one request. `dest` is
auto-detected exactly as `files.move`'s: an existing directory receives
`basename(src)`, any other path is the full target path (a rename, with the
parent directory validated in both phases). The source stays in place — the
difference from `files.move`. Idempotence is **sha1-gated in both overwrite
modes**: an existing regular-file target's `RemoteFileSha1` is compared
against the source's — equal content skips the copy, different content
raises `ValueError` with `overwrite` unset and yields `cp -r -f` with it set.
So `overwrite=True` is Ansible's `force: yes`: a drifted target converges
once, then reports no change (the baseline's stateless always-changed
overwrite is deliberately dropped). `mode`/`user`/`group` converge the
target's metadata in the same request: after a planned copy they yield an
unconditional `chown`/`chmod` (the `files.file` create branch — the copy
inherits the source's metadata), and on an equal-content no-op they are
compared against the `File` fact's row so only the differing commands are
yielded (the `files.file` update branch) — a mode- or owner-only drift
converges without re-copying. The mode accepts the `files.file` spellings
(a symbolic mode never equals the fact's octal int, so its `chmod` re-runs
every pass); the named account must already exist. A target that is not a
regular file, or a hash that cannot be read (a directory source), skips the
comparison and the copy is planned. An absent or non-directory rename parent,
and an existing *different* target without `overwrite`, raise `ValueError` in
every phase. An absent source raises in a materialization that can send commands
and reports a plan line in one that cannot (the same issue #73 degradation as
`files.move`), so a "create then copy" flow previews in one pass but still
spans two runs when it might be applied. A symlink source raises in every
phase -- both facts read `False`, not `None`, so upstream's rejection is never
mistaken for absence.
The commands are shell text, so `sudo=True` elevates the whole copy — the
one-request shape for installing a built artifact (a PHP module, say) under
a root-owned path with its final name, mode and owner.

`files.directory` mirrors the baseline's `files.directory`: it reads the
`Directory` fact once and — for an absent path — yields `mkdir -p` plus,
when requested, `chmod`/`chown` to create the tree with the desired
`user`/`group`/`mode`; for an existing directory, only the `chmod`/`chown`
whose requested values differ from the fact's metadata (`recursive=True`
spells them `chmod -R`/`chown -R`); and for `present=False`, `rm -rf` when
the path is a directory. A path held by a regular file or symlink raises
`ValueError` unless `force=True`, which removes it first (`rm -rf`) and
recreates. The baseline's `force_backup`/`force_backup_dir` are deliberately
dropped — their `mv` embeds a `datetime.now()` timestamp that would differ
between the prepare and apply phases and break the determinism guard, so
`force` always removes. The mode accepts every baseline spelling — `750`,
`"750"`, `"0750"`, `"0o750"` normalise to the octal-looking int the fact
carries, while a symbolic mode (`"u+x"`) never equals it, so the chmod
always re-runs. The shells touch only root-owned paths in practice, so
flag the request `sudo=True`.

`files.file` mirrors the baseline's `files.file` in the `files.directory` shape:
it reads the `File` fact once and — for an absent path — yields `touch`
plus, when requested, `chmod`/`chown` to create the file with the desired
`user`/`group`/`mode`; for an existing file, only the `chmod`/`chown`
whose requested values differ from the fact's metadata; and for
`present=False`, `rm -f` when the path is a regular file. A path held by a
directory or symlink raises `ValueError` unless `force=True`, which removes
it first (`rm -rf`) and recreates. `create_remote_dir` (default `True`)
`mkdir -p`s a *missing* parent before the touch — and chowns that new
parent when an owner was requested — but never edits an existing one, so
manage a directory's own mode/owner with `files.directory`. The `touch`
option is deliberately **non-idempotent** exactly as upstream's is: it
re-runs the `touch` on an existing file on every pass, timestamps only.
the baseline's `force_backup`/`force_backup_dir` are dropped for the same
determinism reason as `files.directory`, and the mode accepts the same
spellings. `touch`/`rm -f`/`chmod`/`chown` address arbitrary paths, so for
root-owned locations flag the request `sudo=True`.

`files.link` mirrors the baseline's `files.link` in the `files.file`/
`files.directory` shape: it reads the `Link` fact once and — for an absent
path — yields `ln -s` (plain `ln` when `symbolic=False`) plus, when
requested, `chown -h` to create the link with the desired `user`/`group`;
for an existing link whose target differs, `rm -f` followed by a fresh `ln`
(the baseline's *remove and recreate* rule — the command never carries `ln -f`)
and the `chown -h` whose requested owner differs from the row's; and for
`present=False`, `rm -f` when the path is a link. `chown -h` owns the link
itself, never its target. A hard link (`symbolic=False`) is not a symlink —
`Link` reads `False` for one — so the path is re-read through `File` and
equality with the target is decided by `SameFile`: same inode means the
hard link already *is* what was asked for (a no-op), a different file
follows the non-link rule below. A path held by a regular file or
directory raises `ValueError` unless `force=True`, which removes it first
(`rm -rf`) and recreates. `create_remote_dir` (default `True`) `mkdir -p`s
a *missing* parent before the `ln` — and chowns that new parent when an
owner was requested (the `files.directory` create branch, so no mode is
copied) — but never edits an existing one, so manage a directory's own
mode/owner with `files.directory`. The `target` is required whenever
`present=True`, and `path` accepts a string or `os.PathLike` (the baseline's
`src` spelling is renamed to match `files.file`/`files.directory`).
the baseline's `force_backup`/`force_backup_dir` are dropped for the same
determinism reason as `files.directory`/`files.file`, so `force` always
removes, and its tolerance of a symlink parent is dropped with the
internal `_no_fail_on_link` knob. `ln`/`rm -f`/`chown -h` address arbitrary
paths, so for root-owned locations flag the request `sudo=True`.

`files.flags` mirrors the baseline's `files.flags` in the developer-chosen Linux
adaptation: the baseline's verb pair (`chflags` over BSD `stat -f %Sf` flag names
like `hidden`) does not exist on Linux, so the same set/clear capability is
expressed over `lsattr -d` letters (read through the `FileFlags` fact) and
`chattr +flag`/`-flag`. The idempotence logic is kept verbatim: setting
yields only the requested letters missing from the current set
(`chattr +a,i`), clearing only the requested letters actually present
(`chattr -i`), and anything else is a no-op whose reason mirrors the baseline's
noop text. `flags` accepts one letter as a string or a tuple of letters (a
tuple at `op()` call sites, because request arguments must stay hashable);
any letter outside the ext2 vocabulary — a BSD flag name, a signed `"+i"` —
raises `ValueError` before anything reaches the host, and empty or absent
flags are the baseline's empty-list noop branch. An absent path reads no flags,
so the request plans a `chattr` that fails at apply — the baseline's `Flags`
answers `[]` for absence identically. `chattr` is root's tool in practice,
so for the common protected paths flag the request `sudo=True`.

--->
