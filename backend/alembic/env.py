"""Alembic environment.

Alembic is synchronous while the application talks to SQLite through aiosqlite. The
migration connection is therefore opened with the stdlib sqlite3 driver on the same
file. The two never overlap: migrations run at startup before the async engine is used.

Logging is not configured from alembic.ini on purpose. When the upgrade runs inside the
backend process, `fileConfig` would reset the logging already set up in main.py and the
migration lines would disappear from the container log.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context
from alembic.operations import ops
from sqlalchemy import create_engine, pool

from app.config import settings
from app.models import Base

target_metadata = Base.metadata


def _url() -> str:
    return f"sqlite:///{settings.db_path}"


def _server_default(column) -> str | None:
    """Render the model's Python default as a DDL default, if it is a constant.

    `default=` in the models is applied by SQLAlchemy when it builds the INSERT, so it
    leaves no trace in the DDL and autogenerate does not see it. SQLite refuses
    `ADD COLUMN ... NOT NULL` without a default on a table that already has rows, which
    is every table in a database that has been running for a while.
    """
    default = column.default
    if default is None or not default.is_scalar:
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def _fill_server_defaults(directives) -> None:
    """Give every added NOT NULL column a DDL default, or refuse to write the revision.

    Failing here, while the revision is being generated, is the whole point: the
    alternative is a migration that passes on an empty development database and stops
    the backend at the next start on the real one.
    """
    for directive in directives:
        if isinstance(directive, ops.AddColumnOp):
            column = directive.column
            if column.nullable or column.server_default is not None:
                continue
            rendered = _server_default(column)
            if rendered is None:
                raise ValueError(
                    f"{directive.table_name}.{column.name} is NOT NULL and has no constant "
                    "default: SQLite cannot add it to a table that already holds rows. "
                    "Give the column a default in the model, make it nullable, or write "
                    "the revision by hand with an explicit server_default."
                )
            # DefaultClause and not a bare text(): the renderer tests server_default for
            # truth, and a raw clause raises instead of answering.
            column.server_default = sa.DefaultClause(sa.text(rendered))
        elif hasattr(directive, "ops"):
            _fill_server_defaults(directive.ops)


def process_revision_directives(_context, _revision, directives) -> None:
    # A directive here is a MigrationScript, whose operations hang off upgrade_ops_list.
    # Only the upgrade side matters: a downgrade drops the column.
    for script in directives:
        for upgrade_ops in script.upgrade_ops_list:
            _fill_server_defaults(upgrade_ops.ops)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
        process_revision_directives=process_revision_directives,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                # SQLite cannot alter a column in place: batch mode copies the table,
                # rebuilds it and copies the rows back. Without this, anything beyond
                # ADD COLUMN would fail.
                render_as_batch=True,
                compare_type=True,
                process_revision_directives=process_revision_directives,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
