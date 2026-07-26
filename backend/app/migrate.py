"""Schema alignment at startup.

`create_all` creates missing tables but does not add columns to tables that already
exist. Here the columns declared in the models are compared with the ones present in
the database, and the missing ones are added with an ALTER TABLE.

On SQLite, ALTER TABLE ADD COLUMN is instant and does not rewrite the table, so nothing
more elaborate is needed for a single user database.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import Base

log = logging.getLogger(__name__)


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
    for table in Base.metadata.sorted_tables:
        result = await connection.execute(text(f"PRAGMA table_info('{table.name}')"))
        existing = {row[1] for row in result}
        if not existing:
            # Brand new table: create_all will take care of it.
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
