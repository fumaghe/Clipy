"""schema iniziale Clipy v1

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    # Schema iniziale = modelli v1. Le migrazioni successive devono essere incrementali (op.add_column ecc.).
    from app.db import models  # noqa: F401
    from app.db.base import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.db import models  # noqa: F401
    from app.db.base import Base

    Base.metadata.drop_all(bind=op.get_bind())
