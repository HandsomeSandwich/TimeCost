import os
from sqlalchemy import create_engine, text

def get_database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        # Fly sometimes provides postgres:// which SQLAlchemy doesn't like
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    return "sqlite:///timecost.db"

engine = create_engine(
    get_database_url(),
    pool_pre_ping=True,
    future=True,
)

def get_db_connection():
    return engine.connect()

def _is_postgres() -> bool:
    return engine.dialect.name in ("postgresql", "postgres")

def init_db():
    """
    Creates tables for both SQLite and Postgres.
    Key difference:
      - SQLite: INTEGER PRIMARY KEY AUTOINCREMENT
      - Postgres: id SERIAL PRIMARY KEY
    """
    if _is_postgres():
        expenses_sql = """
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            amount DOUBLE PRECISION NOT NULL,
            category TEXT NOT NULL
        )
        """
        goals_sql = """
        CREATE TABLE IF NOT EXISTS goals (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            target DOUBLE PRECISION NOT NULL,
            current DOUBLE PRECISION NOT NULL DEFAULT 0
        )
        """
    else:
        expenses_sql = """
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL
        )
        """
        goals_sql = """
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target REAL NOT NULL,
            current REAL NOT NULL DEFAULT 0
        )
        """

    with engine.begin() as conn:
        conn.execute(text(expenses_sql))
        conn.execute(text(goals_sql))
