import os
from dotenv import load_dotenv

load_dotenv()

import sys

sys.path.append(os.getcwd())

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

from app.database import DATABASE_URL

if not DATABASE_URL:
    raise RuntimeError("В env.py Alembic не смог получить DATABASE_URL из app/database.py")

config.set_main_option("sqlalchemy.url", DATABASE_URL)

fileConfig(config.config_file_name)

from app.database import Base
from app import models

target_metadata = Base.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations в 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
