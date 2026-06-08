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
    Create core + couples tables for both SQLite (local) and Postgres (Fly).
    Dinaro owns its own tables/migrations in dinaro/db.py.
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
        category TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'personal',
        owner_key TEXT,
        household_id INTEGER
    )
    """

    goals_sql = f"""
    CREATE TABLE IF NOT EXISTS goals (
        id {id_col},
        owner_key TEXT,
        name TEXT NOT NULL,
        target {num_col} NOT NULL,
        current {num_col} NOT NULL DEFAULT 0
    )
    """

    freelance_entries_sql = f"""
    CREATE TABLE IF NOT EXISTS freelance_entries (
        id {id_col},
        owner_key TEXT,
        work_date DATE NOT NULL,
        client TEXT NOT NULL,
        hours {num_col} NOT NULL,
        hourly_rate {num_col} NOT NULL,
        notes TEXT
    )
    """

    personal_profiles_sql = f"""
    CREATE TABLE IF NOT EXISTS personal_profiles (
        id {id_col},
        profile_name TEXT NOT NULL UNIQUE,
        pin_hash TEXT NOT NULL,
        pin_salt TEXT NOT NULL,
        display_name TEXT,
        currency TEXT,
        work_hours {num_col} NOT NULL DEFAULT 40,
        annual_rate {num_col},
        hourly_rate {num_col},
        pay_frequency TEXT,
        paycheck_amount {num_col},
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """

    email_signups_sql = f"""
    CREATE TABLE IF NOT EXISTS email_signups (
        id {id_col},
        email TEXT NOT NULL UNIQUE,
        source TEXT,
        signed_up_at TEXT NOT NULL
    )
    """

    staples_sql = f"""
    CREATE TABLE IF NOT EXISTS staples (
        id {id_col},
        owner_key TEXT NOT NULL,
        name TEXT NOT NULL,
        cost {num_col} NOT NULL
    )
    """

    households_sql = f"""
    CREATE TABLE IF NOT EXISTS households (
        id {id_col},
        invite_code TEXT UNIQUE
    )
    """

    household_members_sql = f"""
    CREATE TABLE IF NOT EXISTS household_members (
        id {id_col},
        household_id INTEGER NOT NULL,
        user_key TEXT NOT NULL,
        display_name TEXT
    )
    """

    # --- Couples: Making Invisible Work Visible ---
    couples_partnerships_sql = f"""
    CREATE TABLE IF NOT EXISTS couples_partnerships (
        id {id_col},
        name TEXT,
        partnership_code TEXT UNIQUE,
        currency TEXT NOT NULL DEFAULT '£',
        hourly_rate {num_col} NOT NULL DEFAULT 13.00,
        created_at TEXT NOT NULL
    )
    """

    couples_partners_sql = f"""
    CREATE TABLE IF NOT EXISTS couples_partners (
        id {id_col},
        partnership_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        pin_hash TEXT NOT NULL,
        pin_salt TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """

    couples_tasks_sql = f"""
    CREATE TABLE IF NOT EXISTS couples_tasks (
        id {id_col},
        partnership_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'Other',
        default_minutes INTEGER NOT NULL DEFAULT 30,
        active INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER,
        created_at TEXT NOT NULL
    )
    """

    couples_logs_sql = f"""
    CREATE TABLE IF NOT EXISTS couples_logs (
        id {id_col},
        partnership_id INTEGER NOT NULL,
        partner_id INTEGER NOT NULL,
        task_id INTEGER,
        custom_title TEXT,
        category TEXT NOT NULL DEFAULT 'Other',
        minutes INTEGER NOT NULL,
        work_date TEXT NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL
    )
    """

    with engine.begin() as conn:
        conn.execute(text(expenses_sql))
        conn.execute(text(goals_sql))
        conn.execute(text(freelance_entries_sql))
        conn.execute(text(personal_profiles_sql))
        conn.execute(text(email_signups_sql))
        conn.execute(text(staples_sql))
        conn.execute(text(households_sql))
        conn.execute(text(household_members_sql))
        conn.execute(text(couples_partnerships_sql))
        conn.execute(text(couples_partners_sql))
        conn.execute(text(couples_tasks_sql))
        conn.execute(text(couples_logs_sql))

        # --- SQLite-only: add missing columns on older local DBs ---
        if engine.dialect.name == "sqlite":
            # expenses: add new columns if missing
            cols = conn.execute(text("PRAGMA table_info(expenses)")).mappings().all()
            col_names = {c["name"] for c in cols}

            if "scope" not in col_names:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN scope TEXT NOT NULL DEFAULT 'personal'"))
            if "owner_key" not in col_names:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN owner_key TEXT"))
            if "household_id" not in col_names:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN household_id INTEGER"))

            # goals
            cols = conn.execute(text("PRAGMA table_info(goals)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "owner_key" not in col_names:
                conn.execute(text("ALTER TABLE goals ADD COLUMN owner_key TEXT"))

            # freelance_entries legacy patch
            cols = conn.execute(text("PRAGMA table_info(freelance_entries)")).mappings().all()
            col_names = {c["name"] for c in cols}

            if "owner_key" not in col_names:
                conn.execute(text("ALTER TABLE freelance_entries ADD COLUMN owner_key TEXT"))

            if "work_date" not in col_names:
                conn.execute(text("ALTER TABLE freelance_entries ADD COLUMN work_date TEXT"))

            if "entry_date" in col_names:
                # Migrate data from entry_date to work_date if work_date is empty
                conn.execute(
                    text(
                        "UPDATE freelance_entries SET work_date = entry_date "
                        "WHERE work_date IS NULL OR work_date = ''"
                    )
                )

            conn.execute(
                text(
                    "UPDATE freelance_entries SET work_date = date('now') "
                    "WHERE work_date IS NULL OR work_date = ''"
                )
            )
        else:
            # PostgreSQL migrations
            # Check for owner_key in goals
            res = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='goals' AND column_name='owner_key'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE goals ADD COLUMN owner_key TEXT"))

            # Check for owner_key in freelance_entries
            res = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='freelance_entries' AND column_name='owner_key'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE freelance_entries ADD COLUMN owner_key TEXT"))
