"""Adeguamento dello schema all'avvio.

`create_all` crea le tabelle mancanti ma non aggiunge colonne a tabelle che esistono
gia. Qui si confrontano le colonne dichiarate nei modelli con quelle presenti nel
database e si aggiungono quelle mancanti con un ALTER TABLE.

Su SQLite ALTER TABLE ADD COLUMN e istantaneo e non riscrive la tabella, quindi non
serve nulla di piu strutturato per un database di un singolo utente.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import Base

log = logging.getLogger(__name__)


def _default_clause(column) -> str:
    """SQLite pretende un default costante per le colonne aggiunte a tabelle piene."""
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
        # Senza default una colonna NOT NULL non e aggiungibile: si ripiega su un
        # valore vuoto coerente col tipo.
        return " DEFAULT ''" if "CHAR" in str(column.type).upper() or "TEXT" in str(column.type).upper() else " DEFAULT 0"
    return ""


async def ensure_schema(connection: AsyncConnection) -> None:
    for table in Base.metadata.sorted_tables:
        result = await connection.execute(text(f"PRAGMA table_info('{table.name}')"))
        existing = {row[1] for row in result}
        if not existing:
            # Tabella nuova: ci pensa create_all.
            continue

        for column in table.columns:
            if column.name in existing:
                continue
            ddl = (
                f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                f"{column.type.compile(connection.dialect)}{_default_clause(column)}"
            )
            await connection.execute(text(ddl))
            log.info("Schema aggiornato: %s.%s", table.name, column.name)
