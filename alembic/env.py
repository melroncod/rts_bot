# alembic/env.py

import os
from dotenv import load_dotenv

# 1) Сначала подгружаем .env, чтобы POSTGRES_* переменные стали доступны
load_dotenv()

import sys
# 2) Добавляем корень проекта в PYTHONPATH, чтобы Alembic находил пакеты app и их модули
sys.path.append(os.getcwd())

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# 3) Читаем конфиг Alembic (alembic.ini)
config = context.config

# 4) Получаем строку подключения, сформированную в app/database.py
#    (она же собирает POSTGRES_* из .env)
from app.database import DATABASE_URL
if not DATABASE_URL:
    raise RuntimeError("В env.py Alembic не смог получить DATABASE_URL из app/database.py")

# 5) Подставляем эту строку вместо пустого env:DATABASE_URL
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# 6) Включаем логгирование (если требуется, но обычно оставляется как есть)
fileConfig(config.config_file_name)

# 7) Импортируем все модели, чтобы Base.metadata видел все таблицы
from app.database import Base
from app import models  # просто импортируем, чтобы зарегистрировать все классы моделей

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
    """Run migrations в 'online' режиме."""
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
