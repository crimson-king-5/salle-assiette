"""Database connection.

SQLite in dev: the whole database is one file, backend/salle.db.
Switching to PostgreSQL later only means changing DATABASE_URL.
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent / "salle.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# check_same_thread=False: FastAPI may use the connection from several threads.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    """Parent class of every table (see models.py)."""


def get_db():
    """Open a session for one request, and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Write `db: DbSession` in a route and FastAPI hands it an open session.
DbSession = Annotated[Session, Depends(get_db)]
