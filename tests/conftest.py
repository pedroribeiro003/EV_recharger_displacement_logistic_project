"""
Shared pytest fixtures for all test suites.

For integration tests, set DATABASE_URL in your environment or .env.test file.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models.base import Base


@pytest.fixture(scope="session")
def db_engine():
    """Create a test database engine (integration tests only)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping integration test")
    engine = create_engine(url, echo=False)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Yield a transactional DB session that rolls back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
