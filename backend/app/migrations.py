"""Tiny schema upgrades for an existing salle.db.

Base.metadata.create_all() creates missing TABLES but never adds a column to a
table that already exists. These functions add the columns introduced later
(offline-sync `client_id`, cardio/custom exercise fields, library media, `coach_enabled`) to
databases created before they existed. All of them are idempotent.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

TABLES_WITH_CLIENT_ID = ("workout", "checkin")

# (table, column, SQL type + default). NOT NULL columns need a DEFAULT so existing rows are valid.
NEW_COLUMNS = (
    ("exercise", "is_cardio", "BOOLEAN NOT NULL DEFAULT 0"),
    ("exercise", "cardio_unit", "VARCHAR(20)"),
    ("exercise", "aliases", "TEXT NOT NULL DEFAULT ''"),
    ("exercise", "is_custom", "BOOLEAN NOT NULL DEFAULT 0"),
    ("exercise", "owner_id", "INTEGER"),
    ("exercise", "needs_review", "BOOLEAN NOT NULL DEFAULT 0"),
    ("exercise", "dataset_id", "VARCHAR(16)"),
    ("exercise", "is_library", "BOOLEAN NOT NULL DEFAULT 0"),
    ("exercise", "media_image", "VARCHAR(160)"),
    ("exercise", "media_gif", "VARCHAR(160)"),
    ("exercise", "steps_fr", "TEXT NOT NULL DEFAULT ''"),
    ("exercise", "target_muscle", "VARCHAR(60)"),
    ("exercise", "secondary_muscles", "TEXT NOT NULL DEFAULT ''"),
    ("user_profile", "coach_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
)


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


def add_program_columns(engine: Engine) -> None:
    """Add the columns of NEW_COLUMNS that an older database lacks."""
    with engine.begin() as conn:
        for table, column, ddl in NEW_COLUMNS:
            columns = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
            if columns and column not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        # The library import is keyed on this (same index name SQLAlchemy gives a fresh database)
        if [row[1] for row in conn.execute(text("PRAGMA table_info(exercise)"))]:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_exercise_dataset_id ON exercise (dataset_id)"))
