"""Canonical high-entropy credentials and constant-time digest comparison."""

import base64
import hashlib
import hmac
import re
import secrets


def issue_token() -> str:
    """Generate an independent 256-bit base64url credential."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def token_digest(token: str) -> bytes | None:
    """Parse only canonical 32-byte base64url tokens, then hash their bytes."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    raw = base64.urlsafe_b64decode(token + "=")
    if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != token:
        return None
    return hashlib.sha256(raw).digest()


def matches_token(token: str, expected: bytes) -> bool:
    """Compare a parsed credential with its server-side digest."""
    digest = token_digest(token)
    return digest is not None and hmac.compare_digest(digest, expected)
