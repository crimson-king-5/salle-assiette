"""Tiny schema upgrade for an existing salle.db.

Base.metadata.create_all() creates missing TABLES but never adds a column to a
table that already exists. This adds the offline-sync column `client_id`
(see Workout / Checkin in models.py) to databases created before it existed.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

TABLES_WITH_CLIENT_ID = ("workout", "checkin")


def add_client_id_columns(engine: Engine) -> None:
    """Idempotent: does nothing when the columns and indexes are already there."""
    with engine.begin() as conn:
        for table in TABLES_WITH_CLIENT_ID:
            columns = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
            if not columns:
                continue  # table doesn't exist (yet): create_all will build it with the column
            if "client_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN client_id VARCHAR(36)"))
            # Same name SQLAlchemy gives the index on a fresh database
            conn.execute(text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS ix_{table}_client_id ON {table} (client_id)"
            ))
