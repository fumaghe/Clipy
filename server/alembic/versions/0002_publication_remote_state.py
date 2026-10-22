"""stato remoto granulare, verifica esterna e contatori di riconciliazione sui job di pubblicazione

Revision ID: 0002
Revises: 0001

Idempotente: 0001 crea lo schema dai modelli correnti, quindi su un DB nuovo le colonne esistono già.
"""
import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"

COLUMNS = [
    ("remote_state", sa.Column("remote_state", sa.String(32), nullable=False, server_default="LOCAL")),
    ("verification", sa.Column("verification", sa.String(24), nullable=False, server_default="NONE")),
    ("verified_at", sa.Column("verified_at", sa.DateTime(), nullable=True)),
    ("send_started_at", sa.Column("send_started_at", sa.DateTime(), nullable=True)),
    ("reconcile_empty_count", sa.Column("reconcile_empty_count", sa.Integer(), nullable=False, server_default="0")),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("publication_jobs")}
    for name, col in COLUMNS:
        if name not in existing:
            op.add_column("publication_jobs", col)
    # job già esistenti: ricava lo stato remoto dallo stato locale
    op.execute("UPDATE publication_jobs SET remote_state='SCHEDULED_ON_BUFFER' WHERE status='SCHEDULED_REMOTE' AND remote_state='LOCAL'")
    op.execute("UPDATE publication_jobs SET remote_state='BUFFER_REPORTS_SENT' WHERE status='PUBLISHED' AND remote_state='LOCAL' AND simulated=0")
    op.execute("UPDATE publication_jobs SET remote_state='SENDING' WHERE status='SCHEDULING' AND remote_state='LOCAL'")


def downgrade() -> None:
    with op.batch_alter_table("publication_jobs") as b:
        for name, _ in COLUMNS:
            b.drop_column(name)
