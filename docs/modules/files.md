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
        - edit
        - file
        - flags
        - get
        - install
        - link
        - move
        - put



<!---

`reemote.modules.files.files` combines the file facts and the file operations
in a single source, so the read side (`FileLink`, `FileExists`,
`LocalSha1`, `RemoteFileSha1`, `Sha256File`, `Sha384File`, `Md5File`,
`SameFile`, `FileContents`, `File`,
`Directory`, `Link`, `Socket`,
`FileFlags`, `FindFiles`, `FindDirectories`, `FindLinks`, `FindInFile`,
`FindFilesInfo`, `DirectoryEntries`)
sits beside the `files.link`, `files.file`, `files.put`, `files.get`,
`files.edit`, `files.install`, `files.move`, `files.copy`, `files.directory`
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
`test -e`/`test -L`, then describes it with GNU `stat -c`, falling back to BSD
`stat -f` and finally `ls -ld`, and returns the metadata dict — `user`,
`group`, `mode` (the octal-looking int the baseline returns, e.g. `644` or `4755`,
setuid/setgid/sticky bits included), `size`, and `atime`/`mtime`/`ctime` as
*naive UTC* `datetime`s (the baseline's convention; the `ls -ld` fallback provides
only an mtime, so its atime/ctime read as `None`). A path that does not exist
reads as `None`; one that exists but is not a regular file (a directory,
symlink, socket, ...) reads as `False` — `Directory`, `Link` and `Socket`
are the directory-, link- and socket-accepting siblings, and only `Link`'s
dict carries the `link_target` key. Like
the baseline, a leading `~/` stays unquoted so the remote shell expands it. Where
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
the `files.put`/`files.get` idempotence contract.

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
`files.move`,
`files.copy`, `files.directory` and `files.flags`,
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

`files.install` is a reemote original: the generic "write text content to a
root-owned path" operation the namespace-specific spool-and-install operations
(`apt.sources_file`, `dnf.repo`, `systemd.dropin`) each spell out for their own
config paths. It reads the destination's current text with `FileContents` and,
only when it differs from the desired `content`, yields two descriptors from
one request — an `InstallSpoolCommand` transfer spooling the text to a
dest-derived `/tmp` path over unprivileged scp, then an `InstallCommand` shell
running the elevated `mkdir -p`/`cp`/optional `chown`/`chmod`/`rm -f` install
wrapped in `sh -c`
(so a `sudo=True` request elevates every step, not just the chain head). Because
both descriptors ride one request, the whole write fits a single deploy —
unlike the put-then-`files.move` recipe, which spans two runs. `content` is a
plain `str` (text is what `FileContents` compares; binary content stays
`files.put`'s job), so no identity-stable stream is needed at the call site.
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
first). One read-back caveat: `FileContents` cats unprivileged, so a file
installed with a mode the login user cannot read (a `0640` service-account
config) reads back as empty content and re-installs on every deploy — content
idempotence needs a login-readable file.
There is no `present=False` branch — `files.file(present=False)` owns removal.
Installing under `/etc` needs root, so flag the request `sudo=True`; the
transfer stays unprivileged regardless.

`files.move` mirrors the baseline's `files.move`: `src` is a remote path, `dest`
is the remote *directory* to move it into (the moved path becomes
`dest/basename(src)`), and `overwrite=True` removes an existing target first
(`rm -rf`) instead of raising. It is **stateless** like the baseline — a valid
move always yields a command, there is no idempotent no-op branch — and it
reads `File(src)`, `Directory(dest)` and `File(dest/basename(src))` to
validate. An absent source, an absent or non-directory destination, and an
existing target without `overwrite` raise `ValueError`. The baseline's
phase-aware softness for the two absence cases (a log while planning, an
error only while executing) is deliberately dropped — reemote operations
are phase-unaware, so they raise in *both* phases. A consequence: a
"create a file then move it" flow cannot share one deploy (prepare would
raise on the not-yet-created source) and spans two runs instead. Both
commands are shell text, so `sudo=True` elevates the whole move — a
vehicle for the two-run spool-and-install shape: `files.edit` spools the
transformed content into a login-user-writable path in one run, then
`files.move` installs it into a root-owned directory elevated in a second
run. For whole-content writes prefer `files.install` above (one deploy),
and for the hosts file specifically the dedicated `server.etc_hosts`
operation now covers the old `/etc/hosts` recipe natively.

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
