"""Engine, session factory, and the DB-level append-only guard."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    """SQLite ignores FK constraints unless asked. Without this the tenant
    scoping and cascade behaviour differ between SQLite and PostgreSQL."""
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Append-only enforcement lives in the database, not in application code.
# Anything reachable through the ORM can be bypassed; a trigger cannot.
_SQLITE_APPEND_ONLY = [
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is forbidden');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is forbidden');
    END;
    """,
]

# PostgreSQL equivalent, applied by scripts/init_db.py when the URL is not
# SQLite. Revoking the privilege outright is stronger than a trigger because it
# cannot be dropped by the application role.
_POSTGRES_APPEND_ONLY = [
    "REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC",
    "REVOKE UPDATE, DELETE ON audit_log FROM CURRENT_USER",
]

DROP_APPEND_ONLY_SQLITE = [
    "DROP TRIGGER IF EXISTS audit_log_no_update",
    "DROP TRIGGER IF EXISTS audit_log_no_delete",
]


def apply_append_only_guards(connection) -> None:
    statements = (
        _SQLITE_APPEND_ONLY
        if settings.database_url.startswith("sqlite")
        else _POSTGRES_APPEND_ONLY
    )
    for statement in statements:
        connection.execute(text(statement))


def get_db() -> Iterator[Session]:
    """FastAPI dependency. One session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
