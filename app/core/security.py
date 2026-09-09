"""Password hashing and JWT issue/verify.

Password hashing uses stdlib PBKDF2-HMAC-SHA256 rather than passlib/bcrypt:
one fewer compiled dependency, and the parameters are explicit and auditable
in-repo. The stored format carries its own parameters, so the iteration count
can be raised later and old hashes still verify (and can be re-hashed on next
successful login).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import get_settings

_ALGORITHM = "pbkdf2_sha256"
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return 'pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>'.

    The iteration count is taken from settings and written into the hash, so
    the work factor is a deployment decision and raising it later does not
    invalidate anything already stored.
    """
    iterations = get_settings().pbkdf2_iterations
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            _ALGORITHM,
            str(iterations),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification; never raises on malformed input."""
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt_b64),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(hash_b64))


def create_access_token(*, user_id: int, role: str, tenant_admin_id: int) -> str:
    """Issue a JWT.

    `tenant_admin_id` is the tenant key (decision D-1: the org_admin *is* the
    tenant). It is carried in the token so every request arrives with its scope
    already established and no lookup is needed to authorise a query.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "tenant_admin_id": tenant_admin_id,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()
        ),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate. Raises jwt.PyJWTError on any problem."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
