import os
from sqlalchemy import create_engine, text

def get_database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    # Stable local SQLite path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return f"sqlite:///{os.path.join(base_dir, 'timecost.db')}"

engine = create_engine(
    get_database_url(),
    pool_pre_ping=True,
    future=True,
)

def get_connection():
    return engine.connect()

def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                category TEXT NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target DOUBLE PRECISION NOT NULL,
                current DOUBLE PRECISION NOT NULL DEFAULT 0
            )
        """))
