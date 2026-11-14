# Module examples: gpg

Scripts under `examples/modules/gpg/` — one per fact or operation of the
[`gpg`](../../modules/gpg.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`gpg_gpg_key.py` reading
`GpgKey`, `gpg_gpg_key_details.py` reading `GpgKeyDetails`,
`gpg_gpg_keyrings.py` reading `GpgKeyrings`, `gpg_gpg_keys.py`
reading `GpgKeys`, `gpg_gpg_secret_keys.py` reading `GpgSecretKeys`,
`gpg_gpg_trust.py` reading `GpgTrust`, and `gpg_gpg_verify.py` reading
`GpgVerify`, each over `gather_fact`) are not rendered here — they read a
single fact, and the facts themselves are documented on the
[`gpg` module page](../../modules/gpg.md).

## Installing a keyring from a key stream

The key operation: `gpg.key` spools a controller-side key stream over scp
and installs it as a keyring file at `dest` — dearmored on the target
when the content is ASCII-armored — idempotent on the destination's
existence without a `keyid` (the baseline's no-keyid semantics). The script
yields the same install request twice (the second a guaranteed no-op in
the apply phase through the fresh per-step existence probe); the stream
is one module-level `io.BytesIO` constant because `OperationRequest`
compares it by identity across the phases. The stream embeds the **real**
Docker Release (CE rpm) signing key, so the dearmor has valid material to
convert. The install writes under `/etc/apt/keyrings/` (`etc_t`), which a
CentOS host's SELinux `staff_t`-confined sudo can neither create nor
unlink: on the demo hosts a keyring from an earlier unconfined run already
exists, so the existence-idempotent request no-ops and the script exits 0,
while a *fresh* install there is denied until the host's sudo is unconfined
(or SELinux is permissive) — the same confinement the `selinux`, `sysctl`
and `dnf.key` examples document. The requests are flagged `sudo=True`; the
spool transfer stays unprivileged.

::: gpg_key
    options:
      members: []

## Dearmoring a key file on the target

The dearmor operation: `gpg.dearmor` converts an armored key file already
on the target host to binary keyring form — both `src` and `dest` are
remote paths, no bytes move over the connection. Like the baseline's
`gpg.dearmor` it is deliberately stateless: every run converts and
reports a change. The deploy stages a **real** armored key (the Docker
Release CE rpm signing key) with a `files.put` upload to a `/tmp` spool, so
the dearmor always finds valid material; the conversion runs
`gpg --batch --yes --dearmor`, so an existing output is overwritten and
repeated sweeps converge, while a `files.file` request with
`present=False` still clears any stale `dest` first to demonstrate
explicit removal. Both that cleanup and the dearmor's output write target
`/etc/apt/keyrings/` (`etc_t`), which a CentOS host's SELinux
`staff_t`-confined sudo can neither create nor unlink, so the apply is
denied there until the host's sudo is unconfined (or SELinux is permissive)
— the same confinement the `selinux`, `sysctl` and `dnf.key` examples
document; the dry-run plan and pytest suite pass regardless. The request is
flagged `sudo=True`.

::: gpg_dearmor
    options:
      members: []

## Importing a key into the target keychain or a keyring file

The import operation: `gpg.import_key` spools a controller-side key
stream over scp and imports it into the executing user's keychain with
`gpg --batch --yes --import`. Without a `keyid` it is deliberately
non-idempotent — the fingerprint of a raw stream is unknowable
controller-side, so the two keychain requests in the script stay changed
even in the apply log; pass `keyid=...` to compare against `GpgKeys`
instead. The stream is one module-level `io.BytesIO` constant (identity
across the phases) embedding the **real** Docker Release (CE rpm) signing
key, so `gpg --import` accepts it. Unlike `gpg.key`/`gpg.dearmor`, the
keychain import writes root's *keychain* (`~root/.gnupg`), not `/etc`, so
it **applies for real even on the SELinux `staff_t`-confined CentOS
hosts**: gpg reads the `/tmp` spool and imports into root's keychain, and
the elevated `sh -c` removes the spool afterwards.

The script's two `dest` requests import the same key into
`/tmp/docker-archive-keyring.gpg` instead
(`--no-default-keyring --keyring`), an additive merge into a keyring
*file*. They carry the key's fingerprint, so the first imports and the
second is a no-op in the apply log (idempotence against that file's
`GpgKeyrings` entry). `/tmp` keeps them applicable on the confined hosts
too — `staff_t` denies `/etc` writes but allows `/tmp`; a real deployment
would point `dest` at `/etc/apt/keyrings` or `/usr/share/keyrings`. All
requests are flagged `sudo=True`; the spool transfer stays unprivileged.

::: gpg_import_key
    options:
      members: []

## Exporting a key to a file on the target

The export operation: `gpg.export_key` redirects
`gpg --batch --yes --armor --export <keyid>` into `dest` on the target
host — both ends stay remote, no transfer. It is stateless: every run
exports and reports a change, and the script's second request overwrites
the first export's file. **Compile-checked by the test suite, not
executed against the shared demo hosts**: the key id must already exist
in root's keychain on each host. The requests are flagged `sudo=True`;
`secret=True` would export the secret half behind a fail-fast loopback
pinentry (no passphrase parameter exists).

::: gpg_export_key
    options:
      members: []

## Deleting a key from the target keychain

The delete operation: `gpg.delete_key` resolves the keyid to the matching
entry's full fingerprint from `GpgKeys` and runs
`gpg --batch --yes --delete-keys`. Idempotent — a keyid matching nothing
reports no change; the script yields the same delete twice and the
second request's fresh per-step fact read sees the key already gone.
**Compile-checked by the test suite, not executed against the shared
demo hosts**: deleting a key from root's keychain is a destructive change
to shared state. The requests are flagged `sudo=True`.

::: gpg_delete_key
    options:
      members: []

## Generating a key on the target

The generation operation: `gpg.gen_key` printf-pipes a batch parameter
block (`%no-protection` fixed) into `gpg --batch --gen-key` — hashable
string fields only, no transfer. The identity parameter is `real_name`
because `name` is an `op()` meta-parameter. Idempotent on the exact uid
in `GpgKeys`; the script's second request is a no-op in the apply phase.
**Compile-checked by the test suite, not executed against the shared
demo hosts**: rsa4096 generation is slow and adds a permanent key to
root's keychain. The requests are flagged `sudo=True`.

::: gpg_gen_key
    options:
      members: []

## Assigning ownertrust to a key

The trust operation: `gpg.trust` printf-pipes one `<fpr>:<n>:` line into
`gpg --batch --import-ownertrust`, mapping the level name to gpg's
numeric value (ultimate=6). Idempotent against `GpgTrust` by level
*name* — a trustdb entry of 5 or 6 both satisfy `ultimate`; the script
yields the same assignment twice and the second request's fresh per-step
fact read sees it already at that level. The requests are flagged
`sudo=True` for root's trust database.

::: gpg_trust
    options:
      members: []

## Refreshing keys from the keyservers

The refresh operation: `gpg.refresh_keys` runs
`gpg --batch --refresh-keys` with the requested ids (a module-level
*tuple* — `OperationRequest` kwargs must stay hashable) or every key
without them. Stateless and network-touching: both requests in the
script stay changed even in the apply log. **Compile-checked by the test
suite, not executed against the shared demo hosts**: it contacts public
keyservers from every guarded host and can be slow or fail when they are
unreachable. The requests are flagged `sudo=True` for root's keychain.

::: gpg_refresh_keys
    options:
      members: []