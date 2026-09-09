"""Request-scoped dependencies: authentication and role gates."""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User, UserRole
from app.repositories import get_user

bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class Principal:
    """Authenticated caller plus tenant scope.

    tenant_admin_id comes from the token, so a request can't widen its own
    scope by changing a path or query parameter.
    """

    user: User
    tenant_admin_id: int

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def role(self) -> str:
        return self.user.role


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None:
        raise _UNAUTHENTICATED
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        # Same error either way. Telling "expired" from "bad signature" tells
        # an attacker which half they got right.
        raise _UNAUTHENTICATED from None

    user = get_user(db, int(payload.get("sub", 0)))
    if user is None:
        raise _UNAUTHENTICATED

    # Claims have to still match the database. A role changed after the token
    # was issued shouldn't be honoured until it's reissued.
    if user.role != payload.get("role") or user.tenant_admin_id != payload.get(
        "tenant_admin_id"
    ):
        raise _UNAUTHENTICATED

    return Principal(user=user, tenant_admin_id=int(payload["tenant_admin_id"]))


def require_org_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != UserRole.ORG_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires the org_admin role",
        )
    return principal


def require_candidate(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != UserRole.CANDIDATE.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires the candidate role",
        )
    return principal


def not_found(what: str) -> HTTPException:
    """404 rather than 403 for out-of-scope resources. A 403 confirms the
    thing exists, which leaks another tenant's data by inference."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")
