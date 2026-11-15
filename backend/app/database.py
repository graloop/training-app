"""The database connection, and how existing databases keep up with models.py.

Postgres in production (docker-compose.yml), SQLite under `npm start` and in
the tests: both through the same SQLAlchemy engine, so no code here or
elsewhere depends on which one is used.

There is no migration tool. On startup (main.py) create_all() adds new tables,
and add_missing_columns() adds new *nullable* columns to existing ones. So a
new column on a model must be nullable; renaming or removing one needs care,
which is why User.username still lives in the historical "email" column.

The one exception so far, migrate_plan_drafts(): plan_drafts went from one
draft per user to one per (user, coach), a constraint neither of the above
can change.
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


def migrate_plan_drafts(bind=None) -> bool:
    """plan_drafts from "one draft per user" (user_id unique) to "one per user and
    coach" (models.PlanDraft: coach_id, uq_plan_drafts_user_coach, uq_plan_drafts_own).
    Runs on every start and does nothing once done. True when it changed something.

    SQLite can't drop a constraint, so the table is rebuilt and its rows copied.
    Postgres drops the old constraint and adds the new column and indexes in place."""
    bind = bind or engine
    inspector = inspect(bind)
    if not inspector.has_table("plan_drafts"):
        return False
    old_unique = [
        c for c in inspector.get_unique_constraints("plan_drafts") if c["column_names"] == ["user_id"]
    ] + [
        i for i in inspector.get_indexes("plan_drafts")
        if i.get("unique") and i["column_names"] == ["user_id"] and i["name"] != "uq_plan_drafts_own"
    ]
    columns = {c["name"] for c in inspector.get_columns("plan_drafts")}
    if not old_unique and "coach_id" in columns:
        return False
    from .models import PlanDraft  # models imports this module

    table = PlanDraft.__table__
    with bind.begin() as conn:
        if bind.dialect.name == "sqlite":
            conn.execute(text("ALTER TABLE plan_drafts RENAME TO plan_drafts_old"))
            table.create(conn)
            kept = ", ".join(c.name for c in table.columns if c.name in columns)
            conn.execute(text(f"INSERT INTO plan_drafts ({kept}) SELECT {kept} FROM plan_drafts_old"))
            conn.execute(text("DROP TABLE plan_drafts_old"))
        else:
            if "coach_id" not in columns:
                conn.execute(text("ALTER TABLE plan_drafts ADD COLUMN coach_id INTEGER REFERENCES users(id)"))
            # The old UNIQUE (user_id) is a constraint and its index: dropping the constraint drops both.
            for constraint in inspect(conn).get_unique_constraints("plan_drafts"):
                if constraint["column_names"] == ["user_id"]:
                    conn.execute(text(f'ALTER TABLE plan_drafts DROP CONSTRAINT "{constraint["name"]}"'))
            for index in inspect(conn).get_indexes("plan_drafts"):
                if index.get("unique") and index["column_names"] == ["user_id"] and index["name"] != "uq_plan_drafts_own":
                    conn.execute(text(f'DROP INDEX "{index["name"]}"'))
            now = inspect(conn)
            names = {c["name"] for c in now.get_unique_constraints("plan_drafts")}
            names |= {i["name"] for i in now.get_indexes("plan_drafts")}
            for index in table.indexes:
                if index.name not in names:
                    index.create(conn)
            if "uq_plan_drafts_user_coach" not in names:
                conn.execute(text(
                    "ALTER TABLE plan_drafts ADD CONSTRAINT uq_plan_drafts_user_coach UNIQUE (user_id, coach_id)"
                ))
    return True
