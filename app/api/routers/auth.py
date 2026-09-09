"""Registration and login."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_principal
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User, UserRole
from app.repositories import get_user_by_email
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Register an org_admin or a candidate.

    An org_admin becomes their own tenant. A candidate must name the admin
    whose tenant they join — that binding is what later scopes every query
    they can make.
    """
    if get_user_by_email(db, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    if payload.role == UserRole.CANDIDATE.value:
        if payload.tenant_admin_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="tenant_admin_id is required when registering a candidate",
            )
        admin = db.get(User, payload.tenant_admin_id)
        if admin is None or not admin.is_admin():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="tenant_admin_id does not identify an org_admin",
            )

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        tenant_admin_id=payload.tenant_admin_id,
    )
    db.add(user)
    db.flush()

    if user.role == UserRole.ORG_ADMIN.value:
        # An admin is their own tenant; resolvable only after the id exists.
        user.tenant_admin_id = user.id

    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(
            user_id=user.id, role=user.role, tenant_admin_id=user.tenant_admin_id
        ),
        role=user.role,
        user_id=user.id,
        tenant_admin_id=user.tenant_admin_id,
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = get_user_by_email(db, payload.email)
    # Verify against a dummy hash when the user is absent so response timing
    # does not reveal which emails are registered.
    stored = user.password_hash if user else "pbkdf2_sha256$480000$AAAA$AAAA"
    if not verify_password(payload.password, stored) or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    return TokenResponse(
        access_token=create_access_token(
            user_id=user.id, role=user.role, tenant_admin_id=user.tenant_admin_id
        ),
        role=user.role,
        user_id=user.id,
        tenant_admin_id=user.tenant_admin_id,
    )


@router.get("/me", response_model=UserOut)
def me(principal: Principal = Depends(get_principal)) -> User:
    return principal.user
