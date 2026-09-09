"""Create the schema and apply the append-only guards.

Stands in for Alembic. The brief scopes migrations to "if needed" and the
schema is created from the models in one pass; the guard statements are the
part a plain `create_all` would miss, which is why this script exists rather
than an inline call.

For a real deployment, swap this for Alembic and move the guard statements
into the first migration so they are versioned with the schema.

    python -m scripts.init_db [--drop]
"""

from __future__ import annotations

import argparse
import sys

from app.core.database import Base, apply_append_only_guards, engine
from app.core.config import get_settings

# Importing the models module is what registers the tables on Base.metadata.
# Aliased rather than `import app.models`, which binds the name `app` to the
# package and shadows anything else called `app` in this namespace.
from app import models as _models  # noqa: F401


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the database schema")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="drop all tables first (destructive; local use only)",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"Database: {settings.database_url}")

    with engine.begin() as connection:
        if args.drop:
            if settings.environment == "production":
                print("Refusing to drop tables in a production environment.")
                return 1
            print("Dropping existing tables...")
            # The append-only triggers reference audit_log and must go first,
            # or the drop fails on SQLite.
            from sqlalchemy import text

            from app.core.database import DROP_APPEND_ONLY_SQLITE

            if settings.database_url.startswith("sqlite"):
                for statement in DROP_APPEND_ONLY_SQLITE:
                    connection.execute(text(statement))
            Base.metadata.drop_all(connection)

        print("Creating tables...")
        Base.metadata.create_all(connection)

        print("Applying append-only guards on audit_log...")
        apply_append_only_guards(connection)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
