import os
from sqlalchemy import create_engine, text

DEFAULT_SQLITE_URL = "sqlite:///timecost.db"


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return DEFAULT_SQLITE_URL

    # Fly sometimes provides postgres:// which SQLAlchemy doesn't like
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


engine = create_engine(
    get_database_url(),
    pool_pre_ping=True,
    future=True,
)


def get_db_connection():
    return engine.connect()


def _is_postgres() -> bool:
    return engine.dialect.name in ("postgresql", "postgres")


def _id_column_sql() -> str:
    return "SERIAL PRIMARY KEY" if _is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def init_db() -> None:
    """
    Create core tables for both SQLite (local) and Postgres (Fly).
    Canonical freelance schema:
      freelance_entries(id, work_date, client, hours, hourly_rate, notes)
    """
    id_col = _id_column_sql()
    num_col = "DOUBLE PRECISION" if _is_postgres() else "REAL"

    expenses_sql = f"""
    CREATE TABLE IF NOT EXISTS expenses (
        id {id_col},
        name TEXT NOT NULL,
        amount {num_col} NOT NULL,
        category TEXT NOT NULL
    )
    """

    goals_sql = f"""
    CREATE TABLE IF NOT EXISTS goals (
        id {id_col},
        name TEXT NOT NULL,
        target {num_col} NOT NULL,
        current {num_col} NOT NULL DEFAULT 0
    )
    """

    freelance_entries_sql = f"""
    CREATE TABLE IF NOT EXISTS freelance_entries (
        id {id_col},
        work_date DATE NOT NULL,
        client TEXT NOT NULL,
        hours {num_col} NOT NULL,
        hourly_rate {num_col} NOT NULL,
        notes TEXT
    )
    """

    with engine.begin() as conn:
        conn.execute(text(expenses_sql))
        conn.execute(text(goals_sql))
        conn.execute(text(freelance_entries_sql))

        # --- SQLite-only: add missing columns on older local DBs ---
        if engine.dialect.name == "sqlite":
            cols = conn.execute(text("PRAGMA table_info(freelance_entries)")).mappings().all()
            col_names = {c["name"] for c in cols}

            # If you have an older table, patch it forward
            if "work_date" not in col_names:
                conn.execute(text("ALTER TABLE freelance_entries ADD COLUMN work_date TEXT"))
                conn.execute(text(
                    "UPDATE freelance_entries SET work_date = date('now') "
                    "WHERE work_date IS NULL OR work_date = ''"
                ))

            if "hourly_rate" not in col_names and "rate" in col_names:
                # You can't rename columns in older SQLite easily without table rebuild,
                # so just keep using hourly_rate going forward.
                # (If you need a real migration later, we can do it safely.)
                pass
