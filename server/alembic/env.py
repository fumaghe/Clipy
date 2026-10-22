from alembic import context
from sqlalchemy import create_engine

from app.db import models  # noqa: F401
from app.db.base import Base

config = context.config
target_metadata = Base.metadata


def run() -> None:
    url = config.get_main_option("sqlalchemy.url")
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run()
