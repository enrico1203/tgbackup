"""jobs spread over several channels

One new table, `sync_job_channels`, and one column on `file_entries` that says which channel
the parts of a file are in. Written by hand, because the column is NOT NULL with no constant
default: every existing row gets the channel of its job, which is where every file uploaded
so far actually is, and only then can the column refuse a null.

The table is rebuilt in batch mode for the NOT NULL and the foreign key. The migration
connection does not turn `foreign_keys` on, which is what keeps the drop of the old table
from cascading into `file_parts` while the rows are copied across.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-13 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sync_job_channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['sync_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'channel_id', name='uq_job_channel')
    )

    with op.batch_alter_table('file_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('channel_id', sa.Integer(), nullable=True))

    op.execute(
        "UPDATE file_entries SET channel_id = ("
        "SELECT sync_jobs.channel_id FROM sync_jobs WHERE sync_jobs.id = file_entries.job_id)"
    )
    # A row whose job is gone has no channel to be given. The cascade from the job would
    # have removed it on a connection with foreign keys on, so it is unreachable already,
    # and left here it would stop the NOT NULL below and the backend with it.
    op.execute(
        "DELETE FROM file_parts WHERE file_id IN "
        "(SELECT id FROM file_entries WHERE channel_id IS NULL)"
    )
    op.execute("DELETE FROM file_entries WHERE channel_id IS NULL")

    with op.batch_alter_table('file_entries', schema=None, recreate='always') as batch_op:
        batch_op.alter_column('channel_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            'fk_file_entries_channel', 'channels', ['channel_id'], ['id'], ondelete='CASCADE'
        )
        batch_op.create_index(
            'ix_file_channel_state_path', ['channel_id', 'state', 'rel_path'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('file_entries', schema=None) as batch_op:
        batch_op.drop_index('ix_file_channel_state_path')
        batch_op.drop_constraint('fk_file_entries_channel', type_='foreignkey')
        batch_op.drop_column('channel_id')

    op.drop_table('sync_job_channels')
