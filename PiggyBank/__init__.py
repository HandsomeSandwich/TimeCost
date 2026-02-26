from flask import Blueprint
from sqlalchemy import text

from database import engine, _id_column_sql, _is_postgres

piggybank_bp = Blueprint(
    "piggybank",
    __name__,
    template_folder="templates",
    static_folder="static",
)


def init_db() -> None:
    """
    Create core tables for both SQLite (local) and Postgres (Fly).
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

    households_sql = f"""
    CREATE TABLE IF NOT EXISTS households (
        id {id_col},
        invite_code TEXT NOT NULL
    )
    """

    household_members_sql = """
    CREATE TABLE IF NOT EXISTS household_members (
        household_id INTEGER NOT NULL,
        user_key TEXT NOT NULL,
        display_name TEXT,
        PRIMARY KEY (household_id, user_key)
    )
    """

    household_profiles_sql = f"""
    CREATE TABLE IF NOT EXISTS household_profiles (
        id {id_col},
        household_id INTEGER NOT NULL,
        user_key TEXT NOT NULL,
        net_monthly {num_col} NOT NULL DEFAULT 0,
        personal_essentials_monthly {num_col} NOT NULL DEFAULT 0,
        disposable_monthly {num_col} NOT NULL DEFAULT 0
    )
    """

    with engine.begin() as conn:
        conn.execute(text(expenses_sql))
        conn.execute(text(goals_sql))
        conn.execute(text(freelance_entries_sql))
        conn.execute(text(households_sql))
        conn.execute(text(household_members_sql))
        conn.execute(text(household_profiles_sql))

        # --- SQLite-only: patch older local DBs forward safely ---
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

            # freelance_entries legacy patch (your existing logic)
            cols2 = conn.execute(text("PRAGMA table_info(freelance_entries)")).mappings().all()
            col_names2 = {c["name"] for c in cols2}

            if "work_date" not in col_names2:
                conn.execute(text("ALTER TABLE freelance_entries ADD COLUMN work_date TEXT"))

            if "entry_date" in col_names2:
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
