from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Session:
    """Context-manager-free session factory for scripts."""
    return SessionLocal()


def session_scope():
    """Simple context manager for use with `with session_scope() as s:`."""
    from contextlib import contextmanager

    @contextmanager
    def _inner():
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _inner()
