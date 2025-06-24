import os
from sqlalchemy import create_engine, text

def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///timecost.db")

    # Railway/Postgres sometimes uses postgres:// which SQLAlchemy wants as postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)

    # SQLite URL is already fine
    return url

engine = create_engine(_db_url(), future=True)

def get_db_connection():
    # returns a SQLAlchemy Connection (close it when done)
    return engine.connect()

def init_db():
    # Create tables if they don't exist
    # (This is okay for now, but later you’ll want Alembic migrations.)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target REAL NOT NULL,
                current REAL NOT NULL
            )
        """))
