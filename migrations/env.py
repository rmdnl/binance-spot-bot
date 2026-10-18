from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from spot_bot.config import get_settings
from spot_bot.models import Base

config = context.config
settings = get_settings()
if config.config_file_name:
    fileConfig(config.config_file_name)

# Alembic must migrate the same metadata used by the application.
target_metadata = Base.metadata
DEFAULT_ALEMBIC_URL = "sqlite:///./spot_bot.db"


def get_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url")
    default_config_path = (Path(__file__).resolve().parents[1] / "alembic.ini").resolve()
    config_path = (
        Path(config.config_file_name).resolve()
        if config.config_file_name
        else None
    )
    has_explicit_url = bool(configured_url) and (
        config.attributes.get("sqlalchemy_url_explicit", False)
        or (config_path is not None and config_path != default_config_path)
        or configured_url != DEFAULT_ALEMBIC_URL
    )
    url = configured_url if has_explicit_url else settings.database_url
    return url.replace("sqlite+aiosqlite:///", "sqlite:///")


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
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
