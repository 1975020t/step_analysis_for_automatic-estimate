"""case status phase (replaces the display group)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

BY_ROLE = {"drafting": "drafting", "issued": "waiting", "won": "production", "shipped": "closed", "done": "closed",
           "lost": "closed", "hold": "closed"}
BY_NAME = {"見積依頼受付": "drafting", "見積前": "drafting", "見積作成中": "drafting", "見積確認中": "checking",
           "見積提出済": "waiting", "受注": "production", "製造準備": "production", "製造中": "production",
           "検査": "production", "出荷待ち": "production", "出荷済": "closed", "完了": "closed", "失注": "closed",
           "保留": "closed"}
BY_GROUP = {"製造・出荷": "production", "保留・失注": "closed"}


def upgrade() -> None:
    with op.batch_alter_table('case_statuses') as batch:
        batch.add_column(sa.Column('phase', sa.String(length=20), nullable=False, server_default='drafting'))
    conn = op.get_bind()
    for sid, name, role, group in conn.execute(sa.text("SELECT id, name, role, group_name FROM case_statuses")).all():
        phase = BY_ROLE.get(role) or BY_NAME.get(name) or BY_GROUP.get(group) or "drafting"
        conn.execute(sa.text("UPDATE case_statuses SET phase = :p WHERE id = :i"), {"p": phase, "i": sid})
    with op.batch_alter_table('case_statuses') as batch:
        batch.drop_column('group_name')


def downgrade() -> None:
    with op.batch_alter_table('case_statuses') as batch:
        batch.add_column(sa.Column('group_name', sa.String(length=20), nullable=False, server_default='見積・受注'))
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE case_statuses SET group_name = '製造・出荷' WHERE phase = 'production' OR role IN ('shipped', 'done')"))
    conn.execute(sa.text("UPDATE case_statuses SET group_name = '保留・失注' WHERE role IN ('lost', 'hold')"))
    with op.batch_alter_table('case_statuses') as batch:
        batch.drop_column('phase')
