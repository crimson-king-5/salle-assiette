"""Shared test setup: every test gets a fresh, empty in-memory database.

The real salle.db file is never touched by the tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import library
from app.database import Base, get_db
from app.main import app
from app.seed import seed_default_program


@pytest.fixture(autouse=True)
def no_vendor_dataset(monkeypatch, tmp_path_factory):
    """Tests never read the real vendor dataset (git-ignored, 17 MB): a missing folder = no import."""
    monkeypatch.setattr(library, "VENDOR_DIR", tmp_path_factory.getbasetemp() / "no-vendor-dataset")


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",  # in memory: disappears when the test ends
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection, so every thread sees the same DB
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_default_program(session)
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
