"""Additive upgrades for an existing database created by the initial snapshot.

SQLAlchemy's ``create_all`` only creates missing tables, so the columns added for
daily settlement are backfilled here. Statements are idempotent and Postgres-only;
fresh databases already have everything from the models.
"""

import logging

from sqlalchemy import text
from src.config.database import engine

log = logging.getLogger(__name__)

COLUMNS = (
    ('runs', 'settlement_day_id', 'INTEGER REFERENCES settlement_days(id)'),
    ('deliveries', 'settlement_day_id', 'INTEGER REFERENCES settlement_days(id)'),
    ('deliveries', 'report_day', 'VARCHAR(10)'),
    ('deliveries', 'dataset_id', 'INTEGER REFERENCES datasets(id)'),
    ('deliveries', 'export_file', 'VARCHAR(100)'),
    ('settlement_days', 'observed_dataset_id', 'INTEGER'),
    ('settlement_days', 'failed_dataset_id', 'INTEGER'),
    ('settlement_days', 'run_id', 'INTEGER'),
    ('settlement_days', 'dataset_id', 'INTEGER REFERENCES datasets(id)'),
    ('settlement_days', 'scheduled_at', 'TIMESTAMP WITHOUT TIME ZONE'),
    ('settlement_days', 'attempt', 'INTEGER DEFAULT 0 NOT NULL'),
    ('settlement_days', 'delivered_at', 'TIMESTAMP WITHOUT TIME ZONE'),
    ('settlement_days', 'detail', 'VARCHAR(500)'),
    ('settlement_days', 'updated_at', 'TIMESTAMP WITHOUT TIME ZONE'),
)

INDEXES = (
    ('ix_settlement_days_report_day', 'settlement_days', 'report_day'),
    ('ix_settlement_days_status', 'settlement_days', 'status'),
)


def upgrade():
    if engine.dialect.name != 'postgresql':
        return
    with engine.begin() as connection:
        for table, column, definition in COLUMNS:
            try:
                connection.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}'))
            except Exception:
                log.exception('Could not add %s.%s', table, column)
        for name, table, column in INDEXES:
            try:
                connection.execute(text(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})'))
            except Exception:
                log.exception('Could not create index %s', name)
