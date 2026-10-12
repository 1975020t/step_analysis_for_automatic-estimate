"""companies (tenants)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12

Every table of a company's data gets `tenant_id` (existing rows: `default`, the one company of before). Names and
numbers become unique within a company; the keyed tables (masters, counters, company details, quote log) have the
company in their primary key.
"""
import warnings

from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

SCOPED = ['staff', 'case_statuses', 'categories', 'attribute_defs', 'templates', 'drawings', 'drawing_revisions',
          'drawing_notes', 'drawing_memos', 'cases', 'case_status_log', 'quotes', 'quote_edit_log',
          'issued_documents', 'past_quotes', 'partners', 'documents']
KEYED = {'counters': 'name', 'master_materials': 'material', 'master_processes': 'process_code',
         'master_surface_treatments': 'treatment_code', 'master_pricing_policy': 'key', 'company': 'key',
         'quote_log': 'quote_no'}
UNIQUE = {  # table: (old unique columns, new constraint name, old name on PostgreSQL)
    'staff': (['name'], 'uq_staff_name', 'staff_name_key'),
    'attribute_defs': (['key'], 'uq_attribute_defs_key', 'attribute_defs_key_key'),
    'cases': (['number'], 'uq_cases_number', 'cases_number_key'),
    'quotes': (['number'], 'uq_quotes_number', 'quotes_number_key'),
    'issued_documents': (['number'], 'uq_issued_documents_number', 'issued_documents_number_key'),
    'past_quotes': (['quote_no', 'date', 'customer'], 'uq_past_quote', 'uq_past_quote'),
}
# SQLite names the unnamed constraints it reflects with this, so that they can be dropped
OLD = {'uq': 'old_%(table_name)s_%(column_0_name)s', 'pk': 'pk_%(table_name)s'}


def _tenant_column(primary_key: bool = False) -> sa.Column:
    return sa.Column('tenant_id', sa.String(length=40), server_default='default', nullable=False,
                     primary_key=primary_key)


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == 'sqlite'
    op.create_table('tenants',
                    sa.Column('tenant_id', sa.String(length=40), nullable=False),
                    sa.Column('name', sa.String(length=120), nullable=False),
                    sa.Column('created_at', sa.DateTime(), nullable=False),
                    sa.PrimaryKeyConstraint('tenant_id'))
    op.execute("INSERT INTO tenants (tenant_id, name, created_at) VALUES ('default', '', CURRENT_TIMESTAMP)")

    for table in SCOPED:
        with op.batch_alter_table(table, naming_convention=OLD if sqlite else None) as batch:
            batch.add_column(_tenant_column())
            batch.create_index(f'ix_{table}_tenant_id', ['tenant_id'], unique=False)
            if table in UNIQUE:
                columns, name, pg_name = UNIQUE[table]
                old = name if table == 'past_quotes' else (f'old_{table}_{columns[0]}' if sqlite else pg_name)
                batch.drop_constraint(old, type_='unique')
                batch.create_unique_constraint(name, ['tenant_id', *columns])

    for table, column in KEYED.items():
        if sqlite:
            with warnings.catch_warnings():  # the copy of the table is told its new primary key: expected
                warnings.simplefilter('ignore', sa.exc.SAWarning)
                with op.batch_alter_table(table, naming_convention=OLD) as batch:
                    batch.add_column(_tenant_column())
                    batch.create_primary_key(f'pk_{table}', ['tenant_id', column])
        else:
            op.add_column(table, _tenant_column())
            op.drop_constraint(f'{table}_pkey', table, type_='primary')
            op.create_primary_key(f'{table}_pkey', table, ['tenant_id', column])


def downgrade() -> None:
    raise NotImplementedError('会社IDを外す変更（データが混ざるため戻せません）')
