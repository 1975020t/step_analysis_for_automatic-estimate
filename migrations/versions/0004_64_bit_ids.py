"""64-bit row ids (Aurora DSQL)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12

The ids and the columns that point at them become BIGINT, with their sequences. Only PostgreSQL changes: SQLite
keeps INTEGER (its integers are 64-bit already, and only an INTEGER PRIMARY KEY numbers itself).
"""
from alembic import op


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

COLUMNS = {  # table: the id (numbered by a sequence) first, then the columns holding ids
    'attribute_defs': ['id'], 'case_statuses': ['id'], 'categories': ['id', 'parent_id'], 'documents': ['id'],
    'drawings': ['id', 'current_revision_id'], 'partners': ['id'], 'staff': ['id'], 'templates': ['id'],
    'cases': ['id', 'status_id'], 'drawing_categories': ['drawing_id', 'category_id'],
    'drawing_memos': ['id', 'drawing_id'], 'drawing_revisions': ['id', 'drawing_id'],
    'case_status_log': ['id', 'case_id'], 'drawing_notes': ['id', 'revision_id'],
    'past_quotes': ['id', 'case_id', 'drawing_id'], 'quotes': ['id', 'case_id', 'drawing_id', 'revision_id'],
    'issued_documents': ['id', 'case_id', 'quote_id', 'template_id'], 'quote_edit_log': ['id', 'quote_id'],
}


def _change(kind: str) -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    for table, columns in COLUMNS.items():
        op.execute(f'ALTER TABLE {table} ' + ', '.join(f'ALTER COLUMN {c} TYPE {kind}' for c in columns))
        if columns[0] == 'id':
            op.execute(f"DO $$ DECLARE s text := pg_get_serial_sequence('{table}', 'id'); "
                       f"BEGIN IF s IS NOT NULL THEN EXECUTE 'ALTER SEQUENCE ' || s || ' AS {kind}'; END IF; END $$")


def upgrade() -> None:
    _change('BIGINT')


def downgrade() -> None:
    _change('INTEGER')
