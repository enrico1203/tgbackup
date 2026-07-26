"""Schema upgrade at startup.

Alembic owns the schema: `upgrade_database` runs `alembic upgrade head` at every start,
so deploying a new version is enough to migrate the database. On an empty directory the
baseline revision creates every table, so `create_all` is no longer used anywhere.

Databases created before Alembic existed have no `alembic_version` table. Recreating
them is not an option, they hold the mapping between files and Telegram messages. They
are adopted instead: `ensure_schema` adds whatever columns the models gained in the
meantime, which is exactly what this module did on its own before, and the result is
stamped with the baseline revision. From the next start they follow the normal path.

`ensure_schema` only knows how to add columns. That was the limit that Alembic removes,
and it is enough here because the pre-Alembic schema only ever changed by gaining
columns.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .models import Base

log = logging.getLogger(__name__)

# The revision that describes the schema as it was when Alembic was introduced.
BASELINE_REVISION = "0001"

# alembic.ini and the alembic/ directory sit next to the app package, both in the
# repository and in the image, where WORKDIR is /app.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    # script_location in the file is relative to the working directory, which is not
    # guaranteed to be the backend root when the upgrade runs inside the application.
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return config


def _default_clause(column) -> str:
    """SQLite requires a constant default for columns added to non-empty tables."""
    default = column.default
    if default is not None and default.is_scalar:
        value = default.arg
        if isinstance(value, bool):
            return f" DEFAULT {1 if value else 0}"
        if isinstance(value, (int, float)):
            return f" DEFAULT {value}"
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f" DEFAULT '{escaped}'"
    if not column.nullable:
        # Without a default a NOT NULL column cannot be added: fall back to an empty
        # value consistent with the type.
        kind = str(column.type).upper()
        return " DEFAULT ''" if "CHAR" in kind or "TEXT" in kind else " DEFAULT 0"
    return ""


async def ensure_schema(connection: AsyncConnection) -> None:
    """Add the columns the models declare and the tables do not have yet.

    Used only to bring a pre-Alembic database up to the baseline. New work goes into a
    revision under alembic/versions.
    """
    for table in Base.metadata.sorted_tables:
        result = await connection.execute(text(f"PRAGMA table_info('{table.name}')"))
        existing = {row[1] for row in result}
        if not existing:
            # Brand new table: the baseline revision will take care of it.
            continue

        for column in table.columns:
            if column.name in existing:
                continue
            ddl = (
                f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                f"{column.type.compile(connection.dialect)}{_default_clause(column)}"
            )
            await connection.execute(text(ddl))
            log.info("Schema updated: %s.%s", table.name, column.name)


async def upgrade_database(engine: AsyncEngine) -> None:
    """Bring the database to the latest revision. Idempotent, runs at every start."""
    async with engine.begin() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        known = {table.name for table in Base.metadata.sorted_tables}
        legacy = "alembic_version" not in tables and bool(tables & known)
        if legacy:
            log.info("Database created before Alembic: aligning it with the baseline")
            await ensure_schema(connection)

    # Alembic is synchronous and opens its own sqlite3 connection on the same file.
    # It runs in a worker thread so the event loop is not blocked, and the async engine
    # is idle at this point, so the two never contend for the write lock.
    if legacy:
        await asyncio.to_thread(command.stamp, _alembic_config(), BASELINE_REVISION)
        log.info("Database stamped at revision %s", BASELINE_REVISION)

    await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
