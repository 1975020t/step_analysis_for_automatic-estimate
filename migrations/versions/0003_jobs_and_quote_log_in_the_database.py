"""jobs and quote log in the database

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('job_id', sa.String(length=40), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('job_id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_status'), ['status'], unique=False)

    op.create_table('quote_log',
    sa.Column('quote_no', sa.String(length=40), nullable=False),
    sa.Column('issued_at', sa.String(length=25), nullable=False),
    sa.Column('customer', sa.String(length=200), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('drawing_no', sa.String(length=120), nullable=False),
    sa.Column('quantity', sa.String(length=20), nullable=False),
    sa.Column('total', sa.String(length=20), nullable=False),
    sa.Column('is_estimate', sa.String(length=10), nullable=False),
    sa.Column('input_files', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('quote_no')
    )


def downgrade() -> None:
    op.drop_table('quote_log')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_status'))

    op.drop_table('jobs')
