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
existence without a `keyid` (pyinfra's no-keyid semantics). The script
yields the same install request twice (the second a guaranteed no-op in
the apply phase through the fresh per-step existence probe); the stream
is one module-level `io.BytesIO` constant because `OperationRequest`
compares it by identity across the phases. **Compile-checked by the test
suite, not executed against the shared demo hosts**: it writes under
`/etc/apt/keyrings/` and the placeholder bytes are not a real key —
replace the stream before running it against your own machines. The
requests are flagged `sudo=True`; the spool transfer stays unprivileged.

::: gpg_key
    options:
      members: []

## Dearmoring a key file on the target

The dearmor operation: `gpg.dearmor` converts an armored key file already
on the target host to binary keyring form — both `src` and `dest` are
remote paths, no bytes move over the connection. Like pyinfra's
`gpg.dearmor` it is deliberately stateless: every run converts and
reports a change. **Compile-checked by the test suite, not executed
against the shared demo hosts**: it writes under `/etc/apt/keyrings/`
and the armored source must already exist on each host. The request is
flagged `sudo=True`.

::: gpg_dearmor
    options:
      members: []

## Importing a key into the target keychain

The import operation: `gpg.import_key` spools a controller-side key
stream over scp and imports it into the executing user's keychain with
`gpg --batch --yes --import`. Without a `keyid` it is deliberately
non-idempotent — the fingerprint of a raw stream is unknowable
controller-side, so both requests in the script stay changed even in the
apply log; pass `keyid=...` to compare against `GpgKeys` instead. The
stream is one module-level `io.BytesIO` constant (identity across the
phases). **Compile-checked by the test suite, not executed against the
shared demo hosts**: the placeholder bytes are not a real key —
`gpg --import` would reject them. The requests are flagged `sudo=True`
for root's keychain; the spool transfer stays unprivileged.

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
keyservers from every host and can be slow or fail when they are
unreachable. The requests are flagged `sudo=True` for root's keychain.

::: gpg_refresh_keys
    options:
      members: []