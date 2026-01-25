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

    # ✅ Freelance entries table (matches your Fly query)
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


def ensure_freelance_tables() -> None:
    """
    Creates freelance tables if missing + performs a tiny migration:
    ensure freelance_entries.work_date exists.
    """
    id_col = _id_column_sql()
    money_col = "DOUBLE PRECISION" if _is_postgres() else "REAL"

    jobs_sql = f"""
    CREATE TABLE IF NOT EXISTS freelance_jobs (
        id {id_col},
        name TEXT NOT NULL,
        client TEXT
    )
    """

    entries_sql = f"""
    CREATE TABLE IF NOT EXISTS freelance_entries (
        id {id_col},
        job_id INTEGER NOT NULL,
        work_date TEXT NOT NULL,
        hours {money_col} NOT NULL,
        rate {money_col} NOT NULL,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES freelance_jobs(id)
    )
    """

    with engine.begin() as conn:
        # Create tables (safe no-ops if they exist)
        conn.execute(text(jobs_sql))
        conn.execute(text(entries_sql))

        # ---- Tiny migration for older sqlite tables that are missing work_date ----
        if engine.dialect.name == "sqlite":
            cols = conn.execute(text("PRAGMA table_info(freelance_entries)")).mappings().all()
            col_names = {c["name"] for c in cols}

            if "work_date" not in col_names:
                # Add column (SQLite only supports ADD COLUMN at end)
                conn.execute(text("ALTER TABLE freelance_entries ADD COLUMN work_date TEXT"))
                # Backfill to today's date so queries don't explode
                conn.execute(
                    text(
                        "UPDATE freelance_entries "
                        "SET work_date = date('now') "
                        "WHERE work_date IS NULL OR work_date = ''"
                    )
                )
