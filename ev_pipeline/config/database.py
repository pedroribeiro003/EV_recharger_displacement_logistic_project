"""
config/database.py
Engine SQLAlchemy, SessionFactory e utilitário de sessão.
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,          # reconecta se a conexão morreu
    echo=False,
)

# Garante que search_path inclui todos os schemas ao conectar
@event.listens_for(engine, "connect")
def set_search_path(dbapi_conn, _):
    schemas = ", ".join([
        settings.db_schema_staging,
        settings.db_schema_core,
        settings.db_schema_analytics,
        "public",
    ])
    dbapi_conn.execute(f"SET search_path TO {schemas}")
    dbapi_conn.commit()


SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager para sessões transacionais.

    Uso:
        with get_session() as session:
            session.add(obj)
    """
    session: Session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_schemas_exist() -> None:
    """Cria os schemas se ainda não existirem."""
    schemas = [
        settings.db_schema_staging,
        settings.db_schema_core,
        settings.db_schema_analytics,
    ]
    with engine.connect() as conn:
        for schema in schemas:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        conn.commit()
