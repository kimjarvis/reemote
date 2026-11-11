# gpg module

::: reemote.modules.gpg.gpg
    options:
      show_root_heading: false
      members:
        - GpgKey
        - GpgKeyDetails
        - GpgKeyInfo
        - GpgKeyMetadata
        - GpgKeyTrust
        - GpgKeyring
        - GpgKeyrings
        - GpgKeys
        - GpgSecretKeys
        - GpgTrust
        - GpgVerification
        - GpgVerify
        - dearmor
        - delete_key
        - export_key
        - gen_key
        - import_key
        - key
        - refresh_keys
        - trust



<!---

`reemote.modules.gpg.gpg` holds the gpg facts — `GpgKey`, the keys held
in one key file on the target host, read with `gpg --with-colons <src>`
without importing it, `GpgKeyrings`, every keyring file found under given
directories with its format and the keys it holds, read by two `find`
sweeps that prefix each file's `gpg --with-colons` output with a
`KEYRING:` marker line, `GpgKeys`, the public keys in the executing user's
keychain (or one explicit keyring file), read from
`gpg --list-keys --with-colons`, and `GpgSecretKeys`, the secret keys in
that keychain, read from `gpg --list-secret-keys --with-colons`. All four
mirror the baseline's `gpg` facts and share one entry shape, the `GpgKeyInfo`
TypedDict (`id`, `validity`, `length`, plus the optional `fingerprint`,
`uid_hash`, `uid` and `subkeys` the colon records attach); `GpgKeyrings`
wraps those entries in `GpgKeyring` dicts carrying each file's `path` and
`format`. The baseline keys its answers by key id (and its keyrings by path);
reemote returns lists of typed entries instead — the same retyping as
`apt.AptKeys`. Beside those ports live three reemote originals with no
the baseline counterpart: `GpgTrust`, the ownertrust assignments read from
`gpg --export-ownertrust` into `GpgKeyTrust` entries, `GpgKeyDetails`,
one key's full metadata read from a targeted `gpg --list-keys` into a
`GpgKeyMetadata` dict (`None` when the key is absent), and `GpgVerify`, a
read-only signature check read from `gpg --status-fd 1 --verify` into a
`GpgVerification` dict. Key sources are stream-only: the baseline's `GpgKey`
URL download branch is deliberately dropped (a URL `src` raises
`ValueError`), and the keyserver touchpoint is the `gpg.refresh_keys`
operation, never a fact.

On the operation side, `gpg.key` mirrors the baseline's `gpg.key` with
stream-only sources — a GPG key stream is spooled over scp and installed
as a keyring file (dearmored on the target when the content is
ASCII-armored), idempotent on the destination's existence or, with
`keyid`, on the `GpgKeyrings` fact; `present=False` removes a whole
keyring file or selectively deletes keys, pruning emptied binary
keyrings. The baseline's `keyserver` fetch, URL download and `mode` parameter
are deliberately dropped (0644 is fixed). `gpg.dearmor` mirrors the baseline's
`gpg.dearmor`: convert an armored key file already on the target host to
binary form, deliberately stateless (always yields). Six reemote
originals with no baseline counterpart complete the key lifecycle:
`gpg.import_key` and `gpg.export_key` move keys in and out of the
executing user's keychain, `gpg.delete_key` removes a key (public or both
halves), `gpg.gen_key` generates an unprotected key from a batch
parameter block, `gpg.trust` assigns ownertrust to a fingerprint, and
`gpg.refresh_keys` refreshes keys from the keyservers — the keyserver
touchpoint `gpg.key` deliberately dropped.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `GpgKey` takes the
key file's path on the *target* host: it runs `gpg --with-colons <src>`
(shlex-quoted) and parses the colon records into `GpgKeyInfo` entries,
mirroring the baseline's `gpg.GpgKey` — for example the Docker release key
reads as `[{"id": "8D81803C0EBFCD88", "validity": "-", "length": 4096,
"subkeys": [{"id": "7EA0A9C3F273FCD8", "validity": "-", "length": 4096}],
"uid_hash": "", "uid": "Docker Release (CE deb) <docker@docker.com>"}]`.
An empty `src` or one carrying a URL scheme raises `ValueError` — the baseline
would fetch a URL target-side with `(wget -O - <url> || curl -sSLf <url>)
| gpg --with-colons`, but reemote never downloads mid-gather (stream the
key through the `gpg.key` operation instead, then read the installed
file). A missing or unreadable file exits 2 with empty stdout, which
reads as `[]`; a missing `gpg` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError] (the baseline's
`requires_command` gating is deliberately dropped).

`GpgKeyrings` takes `directories` — a list of paths or a single path
string; an empty argument raises `ValueError` where the baseline would
silently search the working directory — and runs two `find` sweeps that
read every `.gpg`/`.kbx` binary keyring (`--keyring <file>
--no-default-keyring`) and every `.asc` armored keyring (passed directly
to `gpg`), each behind a `KEYRING:` marker line, mirroring the baseline's
`gpg.GpgKeyrings`. Each readable file becomes one `GpgKeyring` entry —
`{path, format, keys}` where `format` is the extension lowercased
(`gpg`, `kbx` or `asc`) and `keys` are `GpgKeyInfo` entries in listing
order; entries keep marker order (binary keyrings first, then armored
ones) and a file `gpg` cannot read contributes no entry at all. This is
the general-purpose keyring inspection fact;
[`AptKeys`][reemote.modules.apt.apt.AptKeys] is the APT-flavored
projection that flattens the same sweep into one key list and drops the
per-keyring paths and formats. A missing `gpg` is swallowed by each
exec's `|| true` and reads as `[]`, while a missing `find` exits 127 —
reported as [`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`GpgKeys` takes an optional `keyring` path: without one it runs
`gpg --list-keys --with-colons` against the executing user's default
keychain, with one it appends `--keyring <path> --no-default-keyring`
(shlex-quoted) to list that single file, mirroring the baseline's `gpg.GpgKeys`.
The colon records parse into `GpgKeyInfo` entries — a primary key's
`fpr`/`uid` records attach its `fingerprint`, `uid_hash` and `uid`, a
`sub` record starts a subkey (only the last one before the next primary
key is kept, exactly like the baseline), and `tru` noise and blank lines are
skipped. The answer reflects the login user's `GNUPGHOME`; to inspect
another user's keys, elevate the gather's host connection or point
`keyring` at a file. An empty keychain prints nothing and reads as `[]`;
a missing `gpg` exits 127 — reported as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`GpgSecretKeys` is the secret-key twin of `GpgKeys`: it runs
`gpg --list-secret-keys --with-colons` (with the same optional `keyring`
switch), parsing `sec` primary and `ssb` subkey records into the same
`GpgKeyInfo` entries, mirroring the baseline's `gpg.GpgSecretKeys`. Only key
metadata is listed — no secret key material ever leaves the host. A
keychain with no secret keys reads as `[]`; a missing `gpg` exits 127 —
reported as [`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`GpgTrust` is a reemote original with no baseline counterpart: it runs
`gpg --batch --export-ownertrust` and parses the `FPR:N:` lines into
`GpgKeyTrust` entries — `{fingerprint, ownertrust, level}` where
`ownertrust` is the raw numeric value the export prints and `level` its
name (unknown=1, never=2, marginal=3, full=4, ultimate=5 or 6; the
mapping was verified against gpg 2.4.7, where a freshly generated own
key exports 6). Comment headers, blank lines and malformed rows are
skipped, and an unrecognized number keeps its raw value under level
`unknown`. The `|| true` guard swallows every gpg failure — including a
missing `gpg` — so an empty or unreadable trust database reads as `[]`
rather than raising
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`GpgKeyDetails` takes a `keyid` — a key id, long id or fingerprint,
shlex-quoted — and runs
`gpg --list-keys --with-colons -- <keyid>` against the executing user's
default keychain, parsing the records into one `GpgKeyMetadata` dict:
the shared parser supplies id, fingerprint, validity, length and
subkeys, the `pub` record's fixed fields supply `algo`, `created`,
`capabilities` and (only when non-empty) `expires`, and every `uid`
record contributes to `uids`. Dates stay raw epoch strings so the dict
is JSON-printable as-is. An empty keyid raises `ValueError`; a keyid
matching nothing reads as `None`, and the `|| true` guard means an
absent key and an absent `gpg` both read as `None` rather than raising
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

`GpgVerify` models signature verification as a read-only fact (gpg
`--verify` changes no state): it runs
`gpg --batch --status-fd 1 --verify <signature> [<file>]` — both paths
on the *target* host, shlex-quoted; without `file` the signature must be
attached or cleartext, with it the signature is detached over that file
— and parses the `[GNUPG:]` status lines into one `GpgVerification`
dict: `{valid, status}` plus the signing `keyid` and, for good/bad
signatures, the `signer` user id. `valid` is True only for `GOODSIG`
(signature-correctness only — trust is `GpgTrust`'s question), and when
several statuses appear the deciding one is picked by precedence
GOODSIG > BADSIG > NO_PUBKEY > ERRSIG (the missing key is the actionable
diagnosis over the accompanying ERRSIG). An empty signature raises
`ValueError`; no recognized status line reads as
`{"valid": False, "status": "error"}`, which — behind the `|| true`
guard — also covers a missing `gpg` rather than raising
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError].

## Operations

[`gpg.key`][reemote.modules.gpg.gpg.key] installs or removes GPG keyring
files. The install half takes `src` as a seekable IO-like *stream* on the
controller (the baseline's filename/URL `src` — which the target host reads or
downloads itself — is deliberately dropped; a `str` raises `ValueError`,
so fetch a URL on the controller and pass its bytes). The bytes ride an
unprivileged scp transfer into a dest-derived `/tmp` directory, then one elevated
`sh -c` creates the destination directory, dearmors the content when it
sniffs the `BEGIN PGP PUBLIC KEY BLOCK` armor marker (or copies it
verbatim when already binary, or unconditionally with `dearmor=False`),
chmods the keyring `0644` and removes the spool directory. Flag the request
`sudo=True` for system keyring directories; the transfer stays
unprivileged regardless. Idempotence follows the baseline: without `keyid` an
existing `dest` file is left alone, with `keyid` (a key id, long id or
fingerprint, `0x` prefixes accepted, one or a list) every requested id
must already be in `dest`'s keyring entry. The baseline's `keyserver` fetch and
its `mode` parameter are deliberately dropped — 0644 is fixed (the
`apt.key` precedent), and `gpg.refresh_keys` is the keyserver touchpoint.

The removal half (`present=False`) mirrors the baseline's three branches:
`dest` alone removes the whole keyring file (idempotent on its existence);
`dest` + `keyid` selectively deletes the matching keys from that one
keyring by full fingerprint (`gpg --batch --yes --no-default-keyring
--keyring <file> --delete-keys ...`), then prunes the file when the delete
empties it; `keyid` + `working_dirs` scans every keyring under those
directories for the same selective removal. Selective removal from an
armored `.asc` file that still holds other keys raises `ValueError` — the
file cannot be edited in-place; remove it explicitly with `present=False`
and no `keyid` instead. For the APT repository-keyring use case,
[`apt.key`][reemote.modules.apt.apt.key] is the install-only convenience
wrapper that resolves relative names under `/etc/apt/keyrings/`.

[`gpg.dearmor`][reemote.modules.gpg.gpg.dearmor] converts an ASCII-armored
GPG key file to binary form, both paths on the *target* host: one elevated
`sh -c` creates the destination directory, runs
`gpg --batch --yes --dearmor -o <dest> <src>` (`--yes` overwrites an
existing output, which `--batch` alone refuses) and chmods the output
`0644` (the baseline's `mode` parameter is deliberately dropped). Like the baseline
it is stateless/non-idempotent — it always converts and always reports a
change, reading no fact. To convert a controller-side stream, install it
with `gpg.key` instead, whose install dearmors on the target. It
deliberately stays in-place rather than joining the spool-and-install
family (`gpg.key`, `gpg.import_key`): the payload is gpg's own binary
output produced on the target host, not controller-side file text, and
`dest` is overwritten wholesale — there is no existing content a
read-back fact could preserve. Spooling would mean reimplementing armor
decoding (base64 + CRC24) controller-side just to produce binary content
the family's `str` payload cannot carry, and the `sh -c` wrap already
keeps a `sudo`-flagged request elevated across the whole chain.

[`gpg.import_key`][reemote.modules.gpg.gpg.import_key] is a reemote
original: it imports a key from a seekable IO-like *stream* on the
controller into the executing user's keychain. The bytes ride an
unprivileged scp transfer into a sha1-derived `/tmp` directory, then one `sh -c`
runs `gpg --batch --yes --import <spool>` and removes the spool directory. With
`keyid` the import is idempotent — the id is matched against `GpgKeys`
(or `GpgSecretKeys` with `secret=True`) and an already-imported key is
left alone; without `keyid` it always imports and always reports a
change, because the fingerprint of a raw key stream is unknowable
controller-side. A `str` src raises `ValueError` (the same stream-only
decision as `gpg.key`).

[`gpg.export_key`][reemote.modules.gpg.gpg.export_key] is a reemote
original: one `sh -c` creates the destination directory and redirects
`gpg --batch --yes --export <keyid>` (or `--export-secret-keys` with
`secret=True`, behind `--pinentry-mode loopback` so a passphrase prompt
fails fast instead of hanging) into `dest` on the target host;
`armor=True` adds `--armor` for ASCII output. It is stateless — it always
exports and always reports a change. There is deliberately no passphrase
parameter (secrets never ride in plans or logs), so exporting a
passphrase-protected secret key fails at apply time. It deliberately
stays a shell redirect rather than joining the spool-and-install family
(`gpg.key`, `gpg.import_key`): the payload is gpg's output produced on
the target host, not controller-side content, and `dest` is overwritten
wholesale, so there is no existing file text to preserve — spooling
would pull the exported key (secret material with `secret=True`) back
through the controller into plans and logs, and the `sh -c` wrap already
keeps a `sudo`-flagged request elevated across the redirect.

[`gpg.delete_key`][reemote.modules.gpg.gpg.delete_key] is a reemote
original: it deletes a key from the executing user's keychain by id,
long id or fingerprint, idempotent against `GpgKeys` (or `GpgSecretKeys`
with `secret=True`) — a keyid matching nothing reports no change. The
matching entry's full fingerprint drives
`gpg --batch --yes --delete-keys <fpr>` (gpg refuses ambiguous short ids
under `--batch`); `secret=True` deletes both halves with
`--delete-secret-and-public-keys`.

[`gpg.gen_key`][reemote.modules.gpg.gpg.gen_key] is a reemote original:
it generates a key for `"{real_name} <{email}>"` from a batch parameter
block (`Key-Type`, `Key-Length`, `Name-Real`, `Name-Email`,
`Expire-Date`, `%no-protection`) printf-piped into `gpg --batch --gen-key`
— everything renders from hashable string fields, so no transfer is
needed. The parameter is `real_name`, not `name`, because `name` is an
`op()` meta-parameter. Idempotent: when `GpgKeys` already holds that
exact uid the generation is skipped. `algo`, `length` and `expire`
default to rsa/4096/never; passphrase-protected generation is
deliberately out of scope (`%no-protection` is fixed).

[`gpg.trust`][reemote.modules.gpg.gpg.trust] is a reemote original: it
assigns ownertrust to a full fingerprint by printf-piping one
`<fpr>:<n>:` line into `gpg --batch --import-ownertrust`. `level` is one
of `unknown`, `never`, `marginal`, `full` or `ultimate` (anything else
raises `ValueError`) and maps to gpg's numeric values (ultimate=6).
Idempotent against `GpgTrust` by level *name* — a trustdb entry of 5 or
6 both satisfy `ultimate`.

[`gpg.refresh_keys`][reemote.modules.gpg.gpg.refresh_keys] is a reemote
original and the namespace's only keyserver touchpoint:
`gpg --batch --refresh-keys` with optional key ids (one string or a
collection; prefer tuples via `op()`), or every key in the keychain
without them. It is a stateless *network* operation — whether a
keyserver holds an update is unknowable without contacting it, so it
always runs and always reports a change, and unreachable keyservers
surface as `CommandError` at apply time.

All six keychain operations act on the *executing user's* default
`GNUPGHOME` (no `homedir`/user parameter in v1); flag the request
`sudo=True` for root's keychain. Transfers stay unprivileged regardless.

## Example

The read-only fact-gathering scripts `gpg_gpg_key.py`,
`gpg_gpg_key_details.py`, `gpg_gpg_keyrings.py`, `gpg_gpg_keys.py`,
`gpg_gpg_secret_keys.py`, `gpg_gpg_trust.py` and `gpg_gpg_verify.py`
under
[`examples/modules/gpg/`](../examples/modules/gpg.md) gather
`GpgKey`, `GpgKeyDetails`, `GpgKeyrings`, `GpgKeys`, `GpgSecretKeys`,
`GpgTrust` and `GpgVerify` respectively
across an inventory with
[`gather_fact`][reemote.drivers.gather_fact] and print each host's result
as JSON. The operation scripts `gpg_key.py`, `gpg_dearmor.py`,
`gpg_import_key.py`, `gpg_export_key.py`, `gpg_delete_key.py`,
`gpg_gen_key.py`, `gpg_trust.py` and `gpg_refresh_keys.py` deploy
`gpg.key`, `gpg.dearmor`, `gpg.import_key`, `gpg.export_key`,
`gpg.delete_key`, `gpg.gen_key`, `gpg.trust` and `gpg.refresh_keys`
with a dry run before the apply; the destructive and network-touching
scripts are compile-checked by the test suite, not executed against the
shared demo hosts.

--->
