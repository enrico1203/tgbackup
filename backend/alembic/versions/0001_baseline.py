"""Baseline: the schema as it stood when Alembic was introduced.

Databases created before Alembic are not upgraded through this revision. They are
aligned by `ensure_schema` and then stamped 0001, because their schema already is this
one. See `app/migrate.py`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('encrypted', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('telegram_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=128), nullable=False),
    sa.Column('api_id', sa.Integer(), nullable=False),
    sa.Column('api_hash_enc', sa.Text(), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('session_enc', sa.Text(), nullable=True),
    sa.Column('tg_user_id', sa.BigInteger(), nullable=True),
    sa.Column('first_name', sa.String(length=128), nullable=True),
    sa.Column('username', sa.String(length=128), nullable=True),
    sa.Column('is_premium', sa.Boolean(), nullable=False),
    sa.Column('default_part_size', sa.BigInteger(), nullable=False),
    sa.Column('max_concurrent_jobs', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('tg_id', sa.BigInteger(), nullable=False),
    sa.Column('access_hash', sa.BigInteger(), nullable=True),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('username', sa.String(length=128), nullable=True),
    sa.Column('is_private', sa.Boolean(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('participants', sa.Integer(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['telegram_accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'tg_id', name='uq_channel_account_tg')
    )
    op.create_table('sync_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('source_type', sa.String(length=16), nullable=False),
    sa.Column('local_path', sa.Text(), nullable=False),
    sa.Column('remote', sa.Text(), nullable=True),
    sa.Column('interval_hours', sa.Float(), nullable=False),
    sa.Column('scan_files_per_sec', sa.Integer(), nullable=False),
    sa.Column('part_size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('phase', sa.String(length=32), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['telegram_accounts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('file_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('rel_path', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('mtime_ns', sa.BigInteger(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('parts_total', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['sync_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'rel_path', name='uq_file_job_path')
    )
    with op.batch_alter_table('file_entries', schema=None) as batch_op:
        batch_op.create_index('ix_file_job_state', ['job_id', 'state'], unique=False)

    op.create_table('job_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('scanned', sa.Integer(), nullable=False),
    sa.Column('added', sa.Integer(), nullable=False),
    sa.Column('modified', sa.Integer(), nullable=False),
    sa.Column('removed', sa.Integer(), nullable=False),
    sa.Column('uploaded_files', sa.Integer(), nullable=False),
    sa.Column('uploaded_bytes', sa.BigInteger(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['sync_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('file_parts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('part_index', sa.Integer(), nullable=False),
    sa.Column('offset', sa.BigInteger(), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('message_id', sa.BigInteger(), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['file_entries.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('file_id', 'part_index', name='uq_part_file_index')
    )


def downgrade() -> None:
    op.drop_table('file_parts')
    op.drop_table('job_runs')
    with op.batch_alter_table('file_entries', schema=None) as batch_op:
        batch_op.drop_index('ix_file_job_state')

    op.drop_table('file_entries')
    op.drop_table('sync_jobs')
    op.drop_table('channels')
    op.drop_table('users')
    op.drop_table('telegram_accounts')
    op.drop_table('settings')
