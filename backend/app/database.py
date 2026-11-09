"""The database connection, and how existing databases keep up with models.py.

Postgres in production (docker-compose.yml), SQLite under `npm start` and in
the tests: both through the same SQLAlchemy engine, so no code here or
elsewhere depends on which one is used.

There is no migration tool. On startup (main.py) create_all() adds new tables,
and add_missing_columns() adds new *nullable* columns to existing ones. So a
new column on a model must be nullable; renaming or removing one needs care,
which is why User.username still lives in the historical "email" column.
"""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

# SQLite refuses by default to be used from another thread than the one that
# opened it; FastAPI and the background sync both need that.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Parent of every table in models.py."""


def get_db():
    """FastAPI dependency: one database session per request, always closed.
    Routers commit themselves; code outside a request uses `with SessionLocal()`."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def add_missing_columns() -> None:
    """Tiny forward-only migration: create_all() never alters existing tables,
    so add any *nullable* column a model gained since the table was created."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}'))
