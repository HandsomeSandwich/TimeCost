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

    dinaro_families_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_families (
        id {id_col},
        name TEXT,
        rate_per_hour {num_col} NOT NULL DEFAULT 4,
        family_code TEXT UNIQUE,
        class_code TEXT,
        is_classroom INTEGER NOT NULL DEFAULT 0,
        interest_rate {num_col} NOT NULL DEFAULT 0,
        interest_threshold {num_col} NOT NULL DEFAULT 100,
        tax_rate {num_col} NOT NULL DEFAULT 0
    )
    """

    dinaro_parents_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_parents (
        id {id_col},
        family_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        pin_hash TEXT NOT NULL,
        pin_salt TEXT NOT NULL
    )
    """

    dinaro_children_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_children (
        id {id_col},
        family_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        pin_hash TEXT NOT NULL,
        pin_salt TEXT NOT NULL,
        balance {num_col} NOT NULL DEFAULT 0,
        view_mode TEXT NOT NULL DEFAULT 'visual',
        last_interest_at TEXT,
        last_tax_at TEXT
    )
    """

    dinaro_chores_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_chores (
        id {id_col},
        family_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        default_hours {num_col} NOT NULL DEFAULT 0.5,
        active INTEGER NOT NULL DEFAULT 1,
        recurrence TEXT NOT NULL DEFAULT 'none',
        chore_type TEXT NOT NULL DEFAULT 'income'
    )
    """

    dinaro_chore_logs_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_chore_logs (
        id {id_col},
        child_id INTEGER NOT NULL,
        chore_id INTEGER,
        work_date TEXT NOT NULL,
        overtime_hours {num_col} NOT NULL DEFAULT 0,
        requested_hours {num_col} NOT NULL,
        approved_hours {num_col},
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL
    )
    """

    dinaro_requests_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_requests (
        id {id_col},
        child_id INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        item_cost_dinaro {num_col} NOT NULL,
        offer_dinaro {num_col} NOT NULL,
        parent_counter_dinaro {num_col},
        status TEXT NOT NULL DEFAULT 'open',
        parent_note TEXT,
        child_note TEXT,
        created_at TEXT NOT NULL,
        closed_at TEXT,
        final_dinaro {num_col}
    )
    """

    dinaro_goals_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_goals (
        id {id_col},
        child_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        target_dinaro {num_col} NOT NULL
    )
    """

    dinaro_ledger_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_ledger (
        id {id_col},
        child_id INTEGER NOT NULL,
        delta {num_col} NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL,
        request_id INTEGER,
        log_id INTEGER
    )
    """

    dinaro_spendables_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_spendables (
        id {id_col},
        family_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        cost_dinaro {num_col} NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
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

    with engine.begin() as conn:
        conn.execute(text(expenses_sql))
        conn.execute(text(goals_sql))
        conn.execute(text(freelance_entries_sql))
        conn.execute(text(personal_profiles_sql))
        conn.execute(text(dinaro_families_sql))
        conn.execute(text(dinaro_parents_sql))
        conn.execute(text(dinaro_children_sql))
        conn.execute(text(dinaro_chores_sql))
        conn.execute(text(dinaro_chore_logs_sql))
        conn.execute(text(dinaro_requests_sql))
        conn.execute(text(dinaro_goals_sql))
        conn.execute(text(email_signups_sql))
        conn.execute(text(dinaro_ledger_sql))
        conn.execute(text(dinaro_spendables_sql))
        conn.execute(text(staples_sql))

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

            # dinaro_families
            cols = conn.execute(text("PRAGMA table_info(dinaro_families)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "family_code" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN family_code TEXT"))
            if "class_code" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN class_code TEXT"))
            if "is_classroom" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN is_classroom INTEGER NOT NULL DEFAULT 0"))
            if "interest_rate" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN interest_rate DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "interest_threshold" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN interest_threshold DOUBLE PRECISION NOT NULL DEFAULT 100"))
            if "tax_rate" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN tax_rate DOUBLE PRECISION NOT NULL DEFAULT 0"))

            # dinaro_children
            cols = conn.execute(text("PRAGMA table_info(dinaro_children)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "view_mode" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN view_mode TEXT NOT NULL DEFAULT 'visual'"))
            if "last_interest_at" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_interest_at TEXT"))
            if "last_tax_at" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_tax_at TEXT"))

            # dinaro_chores
            cols = conn.execute(text("PRAGMA table_info(dinaro_chores)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "recurrence" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'"))
            if "chore_type" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN chore_type TEXT NOT NULL DEFAULT 'income'"))
        else:
            # PostgreSQL migrations
            # Check for interest_rate in dinaro_families
            res = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='dinaro_families' AND column_name='interest_rate'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN interest_rate DOUBLE PRECISION NOT NULL DEFAULT 0"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN interest_threshold DOUBLE PRECISION NOT NULL DEFAULT 100"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN tax_rate DOUBLE PRECISION NOT NULL DEFAULT 0"))
            
            # Check for family_code in dinaro_families
            res = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='dinaro_families' AND column_name='family_code'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN family_code TEXT UNIQUE"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN class_code TEXT"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN is_classroom INTEGER NOT NULL DEFAULT 0"))

            # Check for view_mode in dinaro_children
            res = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='dinaro_children' AND column_name='view_mode'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN view_mode TEXT NOT NULL DEFAULT 'visual'"))
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_interest_at TEXT"))
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_tax_at TEXT"))

            # Check for recurrence in dinaro_chores
            res = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='dinaro_chores' AND column_name='recurrence'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'"))
            
            res = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='dinaro_chores' AND column_name='chore_type'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN chore_type TEXT NOT NULL DEFAULT 'income'"))

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
