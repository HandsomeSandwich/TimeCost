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

    expenses_sql = f"""
    CREATE TABLE IF NOT EXISTS expenses (
        id {id_col},
        name TEXT NOT NULL,
        amount {"DOUBLE PRECISION" if _is_postgres() else "REAL"} NOT NULL,
        category TEXT NOT NULL
        -- Optional guardrails:
        -- , CHECK (amount >= 0)
    )
    """

    goals_sql = f"""
    CREATE TABLE IF NOT EXISTS goals (
        id {id_col},
        name TEXT NOT NULL,
        target {"DOUBLE PRECISION" if _is_postgres() else "REAL"} NOT NULL,
        current {"DOUBLE PRECISION" if _is_postgres() else "REAL"} NOT NULL DEFAULT 0
        -- Optional guardrails:
        -- , CHECK (target >= 0)
        -- , CHECK (current >= 0)
    )
    """

    with engine.begin() as conn:
        conn.execute(text(expenses_sql))
        conn.execute(text(goals_sql))
