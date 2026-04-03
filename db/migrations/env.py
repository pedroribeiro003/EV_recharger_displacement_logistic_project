import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Load .env so DATABASE_URL is available
load_dotenv()

# Alembic Config object
config = context.config

# Inject DATABASE_URL from environment
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Setup Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import metadata — this triggers all model imports
from db.models import Base  # noqa: E402

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Skip geoalchemy2 internal objects that Alembic doesn't understand."""
    return True


def render_item(type_, obj, autogen_context):
    """Handle GeoAlchemy2 geometry columns in autogenerate."""
    if type_ == "type" and hasattr(obj, "__visit_name__") and obj.__visit_name__ == "GEOMETRY":
        autogen_context.imports.add("import geoalchemy2")
        return f"geoalchemy2.types.Geometry(geometry_type={obj.geometry_type!r}, srid={obj.srid})"
    return False


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (outputs SQL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
