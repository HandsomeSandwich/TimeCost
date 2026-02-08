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
        rate_per_hour {num_col} NOT NULL DEFAULT 4
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
        balance {num_col} NOT NULL DEFAULT 0
    )
    """

    dinaro_chores_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_chores (
        id {id_col},
        family_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        default_hours {num_col} NOT NULL DEFAULT 0.5,
        active INTEGER NOT NULL DEFAULT 1
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
        conn.execute(text(dinaro_ledger_sql))

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

            # freelance_entries legacy patch
            cols = conn.execute(text("PRAGMA table_info(freelance_entries)")).mappings().all()
            col_names = {c["name"] for c in cols}

            if "work_date" not in col_names:
                conn.execute(text("ALTER TABLE freelance_entries ADD COLUMN work_date TEXT"))
                conn.execute(
                    text(
                        "UPDATE freelance_entries SET work_date = date('now') "
                        "WHERE work_date IS NULL OR work_date = ''"
                    )
                )

            if "hourly_rate" not in col_names and "rate" in col_names:
                # You can't rename columns in older SQLite easily without table rebuild,
                # so just keep using hourly_rate going forward.
                # (If you need a real migration later, we can do it safely.)
                pass

            # --- Migration: clams_* -> dinaro_* (one-time copy) ---
            clams_table = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='clams_families'")
            ).mappings().first()
            if clams_table:
                dinaro_count = conn.execute(
                    text("SELECT COUNT(*) AS c FROM dinaro_families")
                ).mappings().first()["c"]
                if dinaro_count == 0:
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_families (id, name, rate_per_hour) "
                            "SELECT id, name, rate_per_hour FROM clams_families"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_parents (id, family_id, name, pin_hash, pin_salt) "
                            "SELECT id, family_id, name, pin_hash, pin_salt FROM clams_parents"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_children (id, family_id, name, pin_hash, pin_salt, balance) "
                            "SELECT id, family_id, name, pin_hash, pin_salt, balance FROM clams_children"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_chores (id, family_id, title, default_hours, active) "
                            "SELECT id, family_id, title, default_hours, active FROM clams_chores"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_chore_logs "
                            "(id, child_id, chore_id, work_date, overtime_hours, requested_hours, "
                            "approved_hours, status, created_at) "
                            "SELECT id, child_id, chore_id, work_date, overtime_hours, requested_hours, "
                            "approved_hours, status, created_at FROM clams_chore_logs"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_requests "
                            "(id, child_id, item_name, item_cost_dinaro, offer_dinaro, parent_counter_dinaro, "
                            "status, parent_note, child_note, created_at, closed_at, final_dinaro) "
                            "SELECT id, child_id, item_name, item_cost_clams, offer_clams, parent_counter_clams, "
                            "status, parent_note, child_note, created_at, closed_at, final_clams "
                            "FROM clams_requests"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_goals (id, child_id, title, target_dinaro) "
                            "SELECT id, child_id, title, target_clams FROM clams_goals"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_ledger "
                            "(id, child_id, delta, reason, created_at, request_id, log_id) "
                            "SELECT id, child_id, delta, reason, created_at, request_id, log_id FROM clams_ledger"
                        )
                    )
