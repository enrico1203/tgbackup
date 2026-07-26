"""Run the migrations against a database that already holds rows.

A migration that only ever runs on an empty database proves nothing. SQLite refuses
`ADD COLUMN ... NOT NULL` without a default as soon as a table has a single row, and
a table rebuilt in batch mode can lose or refuse data it already held. The real database
this project runs against has tens of thousands of rows, so CI reproduces that shape:

  1. upgrade to the baseline revision;
  2. put one row in every table, built by reading the schema, so the seed keeps working
     when the models change and nobody remembers this file exists;
  3. upgrade to head, which is where a broken revision fails;
  4. check that the rows are still there and that head really describes the models.

Only the tables the baseline creates are seeded. A table introduced by a later revision
is empty while that revision runs, which is the correct state anyway.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/data"))
DB_PATH = DATA_DIR / "tgbackup.db"
BASELINE = "0001"


def alembic(*args: str) -> None:
    result = subprocess.run(["alembic", *args], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"alembic {' '.join(args)} failed")
    print(result.stdout.strip() or f"alembic {' '.join(args)}: ok")


def sample_value(declared_type: str):
    """A value that satisfies the column type. The content is irrelevant, its presence is not."""
    kind = declared_type.upper()
    if "INT" in kind:
        return 1
    if "REAL" in kind or "FLOA" in kind or "DOUB" in kind or "NUMERIC" in kind:
        return 1.0
    if "DATETIME" in kind or "TIMESTAMP" in kind or "DATE" in kind:
        return "2026-01-01 00:00:00.000000"
    if "BOOL" in kind:
        return 0
    return "x"


def user_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'"
    )
    return sorted(row[0] for row in rows)


def seed(connection: sqlite3.Connection) -> list[str]:
    seeded = []
    for table in user_tables(connection):
        columns = []
        values = []
        for _, name, declared_type, not_null, default, primary_key in connection.execute(
            f"PRAGMA table_info('{table}')"
        ):
            # An INTEGER PRIMARY KEY fills itself in, and a column that is nullable or
            # already has a default does not need help.
            if primary_key or (not not_null) or default is not None:
                continue
            columns.append(name)
            values.append(sample_value(declared_type))

        placeholders = ", ".join("?" * len(columns))
        names = ", ".join(f'"{column}"' for column in columns)
        statement = (
            f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})'
            if columns
            else f'INSERT INTO "{table}" DEFAULT VALUES'
        )
        connection.execute(statement, values)
        seeded.append(table)
    connection.commit()
    return seeded


def main() -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True)

    alembic("upgrade", BASELINE)

    connection = sqlite3.connect(DB_PATH)
    seeded = seed(connection)
    print(f"Seeded one row into: {', '.join(seeded)}")
    connection.close()

    alembic("upgrade", "head")

    connection = sqlite3.connect(DB_PATH)
    empty = [
        table
        for table in user_tables(connection)
        if table in seeded
        and connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
    ]
    revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    connection.close()

    if empty:
        raise SystemExit(f"The migrations emptied these tables: {', '.join(empty)}")

    print(f"Rows survived the upgrade, database at revision {revision[0]}")

    # The models must not have moved past the last revision.
    alembic("check")


if __name__ == "__main__":
    main()
