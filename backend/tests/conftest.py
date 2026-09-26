"""Shared test setup: every test gets a fresh, empty in-memory database.

The real salle.db file is never touched by the tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",  # in memory: disappears when the test ends
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection, so every thread sees the same DB
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
