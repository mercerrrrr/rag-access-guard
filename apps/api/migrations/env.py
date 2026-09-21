"""Configure Alembic for the application database."""

from alembic import context
from sqlalchemy import create_engine, pool

from rag_access_guard_api.config import Settings
from rag_access_guard_api.persistence import Base


def database_url() -> str:
    """Return the validated SQLAlchemy database URL."""
    return str(Settings().database_url)


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""
    context.configure(
        url=database_url(),
        target_metadata=Base.metadata,
        compare_server_default=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through a synchronous psycopg connection."""
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, compare_server_default=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
