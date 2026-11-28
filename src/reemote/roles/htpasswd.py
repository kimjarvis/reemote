# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The htpasswd-file role (migrated from ``geerlingguy.htpasswd``).
#
# Installs the Apache utilities and manages ``name:hash`` entries in one or
# more Apache htpasswd files: each credential in :class:`HtpasswdVars` names a
# file, a user, a password and the ownership the file must carry. An entry
# whose stored hash already verifies against the desired password keeps its
# line byte-for-byte (no new salt), a new or changed password is rewritten
# with a freshly hashed one, and lines for users the role does not manage are
# preserved. :func:`htpasswd` is the whole task list, and
# :class:`HtpasswdVars` is ``defaults/main.yml``, bound from the inventory
# entry's ``htpasswd`` role mapping by
# :func:`~reemote.rolevars.resolve_vars`.
#
# The stream carries two or three waves: the ``apt.packages`` request, then
# one ``fact(FileContents)`` read per distinct credential path -- each closing
# its wave -- and the ``files.install``/``files.file`` pair for that path.
# Because the driver applies each request before pulling the next, the
# read-back sees the file as the previous path's write left it, and the
# verify-then-keep merge makes the pair converge on a rerun.
#
# Hashing happens **control-side**, in pure Python: ``$apr1$`` (Apache's MD5-
# crypt, verified against ``htpasswd``/openssl output), ``{SHA}`` (base64
# SHA-1) and plain text. Deviations from the Ansible role, all deliberate:
#
# * D1: the target is the Ubuntu family; the ``include_vars`` OS dispatch
#   collapses to the frozen constants here and an off-target host returns at
#   the family guard before its first ``yield``.
# * D2: ``python3-passlib`` is dropped from the default packages -- the role
#   hashed via passlib on the controller; this one hashes in the standard
#   library. ``required_packages`` still overrides.
# * D3: the symbolic default mode ``u+rw,g+r`` becomes the octal ``"0640"``;
#   the spool-and-install family takes octal digits only.
# * D4: only ``apr_md5_crypt``, ``sha1``/``apr_sha1`` and ``plain`` are
#   supported; any other ``hash_scheme`` raises ``ValueError`` naming
#   https://github.com/kimjarvis/reemote/issues/74.
# * D5: ``htpasswd_nolog`` is inert -- the composed file rides the install
#   spool descriptor, so no request line ever renders a password or hash. The
#   one visible surface is the ``FileContents`` barrier line, which echoes the
#   file's current text like ``repo_epel``'s ``fetch_url`` line does; prefer a
#   hashed scheme over ``plain``.
# * D6: credentials sharing one path are merged in a single read/write pair
#   (Ansible ran one ``htpasswd`` task per credential); the last credential
#   for a path decides its owner/group/mode.
# """

import base64
import hashlib
import secrets
from collections.abc import Generator
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core import op
from ..hosts import Host
from ..modules import apt, files
from ..modules.files import FileContents
from ..rolevars import VarsSet
from ..stages import Deploy, StageItem, fact

#: The role's Debian-family ``htpasswd_required_packages`` default, frozen for
#: the one target family. ``python3-passlib`` is dropped (D2): the hashing here
#: is standard-library Python, control-side.
UBUNTU_REQUIRED_PACKAGES: tuple[str, ...] = ("apache2-utils",)

#: Ansible's ``htpasswd: mode`` default ``u+rw,g+r`` in the octal form the
#: spool-and-install family takes (D3).
DEFAULT_MODE = "0640"

#: The ``hash_scheme`` defaults/main.yml sets, kept as the default here.
DEFAULT_HASH_SCHEME = "apr_md5_crypt"

#: The schemes :func:`hash_password` implements (D4). ``sha1`` and
#: ``apr_sha1`` are the same ``{SHA}`` form in the Ansible module.
SUPPORTED_SCHEMES = ("apr_md5_crypt", "sha1", "apr_sha1", "plain")

#: The tracking issue for the missing entry-management operation (D4).
GAP_ISSUE_URL = "https://github.com/kimjarvis/reemote/issues/74"

# The PHK/Apache MD5-crypt base-64 alphabet and the byte transposition applied
# to the final digest before encoding.
ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_TRANSPOSE = (12, 6, 0, 13, 7, 1, 14, 8, 2, 15, 9, 3, 5, 10, 4, 11)


def md5_crypt(password: str, salt: str, magic: str = "$apr1$") -> str:
    """Apache's MD5-crypt (``$apr1$``) / PHK's ``$1$`` password hash.

    Pure standard-library ``hashlib`` implementation, verified against
    ``openssl passwd -apr1 -salt`` vectors and 300 randomized passlib
    cross-checks. ``magic`` selects the variant: ``$apr1$`` for Apache,
    ``$1$`` for glibc-style md5crypt.
    """
    pwd = password.encode("utf-8")
    salt_b = salt.encode("ascii")
    magic_b = magic.encode("ascii")

    # Alternate digest: MD5(pwd || salt || pwd)
    db = hashlib.md5(pwd + salt_b + pwd).digest()

    # Start digest: MD5(pwd || magic || salt || repeat(db, len(pwd)) || bits)
    ctx = hashlib.md5()
    ctx.update(pwd)
    ctx.update(magic_b)
    ctx.update(salt_b)
    n = len(pwd)
    ctx.update((db * (n // 16 + 1))[:n])
    i = n
    while i:
        ctx.update(b"\x00" if i & 1 else pwd[:1])
        i >>= 1
    dc = ctx.digest()

    # 1000 mixing rounds
    for r in range(1000):
        t = hashlib.md5(pwd if r & 1 else dc)
        if r % 3:
            t.update(salt_b)
        if r % 7:
            t.update(pwd)
        t.update(dc if r & 1 else pwd)
        dc = t.digest()

    # h64 encoding of the transposed digest (little-endian per 3-byte group)
    reordered = bytes(dc[off] for off in _TRANSPOSE)
    out = []
    for g in range(0, 15, 3):
        v1, v2, v3 = reordered[g], reordered[g + 1], reordered[g + 2]
        out.append(ITOA64[v1 & 0x3F])
        out.append(ITOA64[((v2 & 0x0F) << 2) | (v1 >> 6)])
        out.append(ITOA64[((v3 & 0x03) << 4) | (v2 >> 4)])
        out.append(ITOA64[v3 >> 2])
    v1 = reordered[15]
    out.append(ITOA64[v1 & 0x3F])
    out.append(ITOA64[v1 >> 6])
    return magic + salt + "$" + "".join(out)


def _new_salt() -> str:
    """A fresh 8-character h64 salt, as ``htpasswd -nm`` would draw."""
    return "".join(secrets.choice(ITOA64) for _ in range(8))


def hash_password(password: str, scheme: str) -> str:
    """Hash ``password`` in the form ``scheme`` names, with a fresh salt."""
    if scheme == "apr_md5_crypt":
        return md5_crypt(password, _new_salt(), "$apr1$")
    if scheme in ("sha1", "apr_sha1"):
        digest = hashlib.sha1(password.encode("utf-8")).digest()
        return "{SHA}" + base64.b64encode(digest).decode("ascii")
    if scheme == "plain":
        return password
    raise ValueError(  # pragma: no cover - the validator rejects these first
        f"unsupported hash_scheme {scheme!r}; supported: {SUPPORTED_SCHEMES}; "
        f"see {GAP_ISSUE_URL}"
    )


def verify_password(password: str, stored: str) -> bool:
    """Does ``password`` verify against the stored hash field of a line?

    The mirror of the Ansible module's verify-then-keep: a matching line is
    rewritten verbatim (so the file -- and the idempotence check -- survive a
    random salt), anything else is replaced with a fresh hash.
    """
    if stored.startswith(("$apr1$", "$1$")):
        _, magic, salt, _ = stored.split("$", 3)
        return md5_crypt(password, salt, f"${magic}$") == stored
    if stored.startswith("{SHA}"):
        digest = hashlib.sha1(password.encode("utf-8")).digest()
        return stored == "{SHA}" + base64.b64encode(digest).decode("ascii")
    return stored == password


def merge_credentials(current: str | None, creds: tuple["HtpasswdCredential", ...]) -> str:
    """The desired file text for ``creds``, given the file's ``current`` text.

    Pure and a fixpoint: applying it to its own output changes nothing,
    because every managed line then verifies. Credentials are processed in
    order (D6), so for a repeated name the last one wins exactly as Ansible's
    sequential per-task file re-reads would; unmanaged lines keep their place
    and content.
    """
    lines = current.split("\n") if current else []
    if lines and lines[-1] == "":
        lines.pop()
    for cred in creds:
        index = next(
            (i for i, line in enumerate(lines) if line.startswith(f"{cred.name}:")),
            None,
        )
        if index is None:
            lines.append(f"{cred.name}:{hash_password(cred.password, cred.hash_scheme)}")
            continue
        stored = lines[index].split(":", 1)[1]
        if not verify_password(cred.password, stored):
            lines[index] = (
                f"{cred.name}:{hash_password(cred.password, cred.hash_scheme)}"
            )
    return "".join(f"{line}\n" for line in lines)


def group_credentials(
    creds: tuple["HtpasswdCredential", ...],
) -> list[tuple[str, tuple["HtpasswdCredential", ...]]]:
    """Credentials bucketed by path, in order of first appearance."""
    order: list[str] = []
    buckets: dict[str, list[HtpasswdCredential]] = {}
    for cred in creds:
        if cred.path not in buckets:
            order.append(cred.path)
            buckets[cred.path] = []
        buckets[cred.path].append(cred)
    return [(path, tuple(buckets[path])) for path in order]


class HtpasswdCredential(BaseModel):
    """One ``htpasswd_credentials`` list item: a user in a file.

    Frozen so the vars model stays hashable through the deploy body.
    ``password`` is never rendered: it only reaches the remote file through
    the install spool (D5).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    name: str
    password: str
    owner: str
    group: str
    mode: str = DEFAULT_MODE
    hash_scheme: str = DEFAULT_HASH_SCHEME

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, value: str) -> str:
        """A relative path would silently write somewhere unexpected."""
        if not value.startswith("/"):
            raise ValueError(f"credential path must be absolute, got {value!r}")
        return value

    @field_validator("name")
    @classmethod
    def _name_has_no_colon(cls, value: str) -> str:
        """``name:hash`` is the file format; a colon would corrupt it."""
        if ":" in value:
            raise ValueError(f"credential name must not contain a colon, got {value!r}")
        if not value:
            raise ValueError("credential name must be a non-empty string")
        return value

    @field_validator("mode")
    @classmethod
    def _mode_is_octal(cls, value: str) -> str:
        """The install family takes octal digits only (D3)."""
        if len(value) not in (3, 4) or not value.isascii() or any(
            c not in "01234567" for c in value
        ):
            raise ValueError(
                f"credential mode must be 3 or 4 octal digits, got {value!r} "
                "(e.g. 'u+rw,g+r' is '0640')"
            )
        return value

    @field_validator("hash_scheme")
    @classmethod
    def _scheme_is_supported(cls, value: str) -> str:
        """Fail fast on a scheme the pure-Python hasher does not implement."""
        if value not in SUPPORTED_SCHEMES:
            raise ValueError(
                f"unsupported hash_scheme {value!r}; this role implements "
                f"{SUPPORTED_SCHEMES}; for other schemes see {GAP_ISSUE_URL}"
            )
        return value


class HtpasswdVars(BaseModel):
    """The role's static inputs: ``defaults/main.yml``, unprefixed.

    Field names are bare -- :func:`~reemote.rolevars.resolve_vars` binds the
    inventory entry's ``htpasswd`` role mapping, so ``credentials`` there sets
    :attr:`credentials`. The model is frozen, so it stays hashable through the
    deploy body (:class:`~reemote.rolevars.VarsSet` is closed over by the
    factory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    credentials: tuple[HtpasswdCredential, ...] = ()
    required_packages: tuple[str, ...] = UBUNTU_REQUIRED_PACKAGES
    nolog: bool = True

    @field_validator("required_packages", mode="before")
    @classmethod
    def _packages_to_tuple(cls, value: Any) -> Any:
        """Accept a list or a bare string in the inventory mapping."""
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list):
            return tuple(value)
        return value


def htpasswd(v: VarsSet[HtpasswdVars]) -> Deploy:
    """Manage Apache htpasswd files on Ubuntu-family hosts.

    Args:
        v: The role's resolved variables, one :class:`HtpasswdVars` per host
            (``resolve_vars(HtpasswdVars, "htpasswd", inventory)``).

    Returns:
        A deploy for :func:`~reemote.run_waves`. Publishes nothing, so the
        generator returns ``None``; a composing role sequences it with
        ``yield from scoped("htpasswd", htpasswd(v))(host)``.

    Raises:
        ValueError: A credential's path is relative, its name contains a
            colon, its mode is not octal digits, or its ``hash_scheme`` is
            outside :data:`SUPPORTED_SCHEMES` (D4). All are raised by
            :class:`HtpasswdVars` binding, before any host is contacted.
    """

    def deploy(host: Host) -> Generator[StageItem, Any]:
        hv = v.for_host(host)
        if host.family != "Ubuntu":
            return

        # The role's `package:` task. With no credentials the packages are
        # still installed, exactly like Ansible; an empty override skips it.
        if hv.required_packages:
            yield op(
                apt.packages,
                name="Install the Apache utilities.",
                packages=hv.required_packages,
                sudo=True,
            )

        for path, creds in group_credentials(hv.credentials):
            # The barrier that replaces the module's read of the file: the
            # two-tier elevated read sees 0640 root-owned files, None if the
            # file does not exist yet.
            current = yield fact(FileContents, path=path)
            last = creds[-1]
            # Content-idempotent whole-file write; the spool path is derived
            # from dest, so no secret ever reaches a plan line (D5).
            yield op(
                files.install,
                name=f"Manage htpasswd entries in {path}.",
                content=merge_credentials(current, creds),
                dest=path,
                mode=last.mode,
                user=last.owner,
                group=last.group,
                sudo=True,
            )
            # The install's idempotence is content-only; this companion
            # converges an owner/group/mode drift on an unchanged file.
            yield op(
                files.file,
                name=f"Ensure {path} ownership and mode.",
                path=path,
                user=last.owner,
                group=last.group,
                mode=last.mode,
                sudo=True,
            )

    return deploy


__all__ = [
    "HtpasswdCredential",
    "HtpasswdVars",
    "htpasswd",
]
