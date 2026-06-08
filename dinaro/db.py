"""Dinaro's own database layer.

Dinaro owns its schema (the dinaro_* tables + push_subscriptions) here rather
than in the shared database.py, so the module can eventually be deployed as a
standalone app.

By default it shares the main application database (so existing data and local
dev keep working with no migration). Set DINARO_DATABASE_URL to point Dinaro at
its own database when running it independently.
"""
import os

from sqlalchemy import create_engine, text


def _dinaro_database_url() -> str:
    url = os.environ.get("DINARO_DATABASE_URL")
    if not url:
        return ""  # empty => share the main app engine
    # Fly sometimes provides postgres:// which SQLAlchemy doesn't like
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


_own_url = _dinaro_database_url()
if _own_url:
    # Standalone: Dinaro runs against its own database.
    engine = create_engine(_own_url, pool_pre_ping=True, future=True)
else:
    # Default: share the main application database (no data migration needed).
    from database import engine  # noqa: F401


def get_db_connection():
    return engine.connect()


def _is_postgres() -> bool:
    return engine.dialect.name in ("postgresql", "postgres")


def _id_column_sql() -> str:
    return "SERIAL PRIMARY KEY" if _is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def init_dinaro_db() -> None:
    """Create the Dinaro tables (and run column migrations) for SQLite/Postgres."""
    id_col = _id_column_sql()
    num_col = "DOUBLE PRECISION" if _is_postgres() else "REAL"

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
        tax_rate {num_col} NOT NULL DEFAULT 0,
        show_leaderboard INTEGER NOT NULL DEFAULT 0
    )
    """

    dinaro_parents_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_parents (
        id {id_col},
        family_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        pin_hash TEXT NOT NULL,
        pin_salt TEXT NOT NULL,
        link_code TEXT
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
        last_tax_at TEXT,
        approved INTEGER NOT NULL DEFAULT 1
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

    dinaro_group_rewards_sql = f"""
    CREATE TABLE IF NOT EXISTS dinaro_group_rewards (
        id {id_col},
        family_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        reward_dinaro {num_col} NOT NULL,
        condition_type TEXT NOT NULL DEFAULT 'all_complete',
        condition_chore_id INTEGER,
        condition_target INTEGER,
        condition_period TEXT NOT NULL DEFAULT 'daily',
        active INTEGER NOT NULL DEFAULT 1,
        last_awarded_at TEXT
    )
    """

    push_subscriptions_sql = f"""
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id {id_col},
        family_id INTEGER NOT NULL,
        user_type TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        endpoint TEXT NOT NULL,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """

    push_subscriptions_idx = """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_push_sub_endpoint
    ON push_subscriptions (endpoint)
    """

    with engine.begin() as conn:
        conn.execute(text(dinaro_families_sql))
        conn.execute(text(dinaro_parents_sql))
        conn.execute(text(dinaro_children_sql))
        conn.execute(text(dinaro_chores_sql))
        conn.execute(text(dinaro_chore_logs_sql))
        conn.execute(text(dinaro_requests_sql))
        conn.execute(text(dinaro_goals_sql))
        conn.execute(text(dinaro_ledger_sql))
        conn.execute(text(dinaro_spendables_sql))
        conn.execute(text(dinaro_group_rewards_sql))
        conn.execute(text(push_subscriptions_sql))
        conn.execute(text(push_subscriptions_idx))

        # --- Column migrations for older databases ---
        if engine.dialect.name == "sqlite":
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

            cols = conn.execute(text("PRAGMA table_info(dinaro_children)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "view_mode" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN view_mode TEXT NOT NULL DEFAULT 'visual'"))
            if "last_interest_at" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_interest_at TEXT"))
            if "last_tax_at" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_tax_at TEXT"))

            cols = conn.execute(text("PRAGMA table_info(dinaro_chores)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "recurrence" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'"))
            if "chore_type" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN chore_type TEXT NOT NULL DEFAULT 'income'"))

            cols = conn.execute(text("PRAGMA table_info(dinaro_parents)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "link_code" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_parents ADD COLUMN link_code TEXT"))

            cols = conn.execute(text("PRAGMA table_info(dinaro_children)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "approved" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN approved INTEGER NOT NULL DEFAULT 1"))

            cols = conn.execute(text("PRAGMA table_info(dinaro_families)")).mappings().all()
            col_names = {c["name"] for c in cols}
            if "show_leaderboard" not in col_names:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN show_leaderboard INTEGER NOT NULL DEFAULT 0"))
        else:
            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_families' AND column_name='interest_rate'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN interest_rate DOUBLE PRECISION NOT NULL DEFAULT 0"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN interest_threshold DOUBLE PRECISION NOT NULL DEFAULT 100"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN tax_rate DOUBLE PRECISION NOT NULL DEFAULT 0"))

            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_families' AND column_name='family_code'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN family_code TEXT UNIQUE"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN class_code TEXT"))
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN is_classroom INTEGER NOT NULL DEFAULT 0"))

            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_children' AND column_name='view_mode'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN view_mode TEXT NOT NULL DEFAULT 'visual'"))
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_interest_at TEXT"))
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN last_tax_at TEXT"))

            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_chores' AND column_name='recurrence'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'"))

            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_chores' AND column_name='chore_type'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_chores ADD COLUMN chore_type TEXT NOT NULL DEFAULT 'income'"))

            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_parents' AND column_name='link_code'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_parents ADD COLUMN link_code TEXT"))

            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_children' AND column_name='approved'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_children ADD COLUMN approved INTEGER NOT NULL DEFAULT 1"))

            res = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='dinaro_families' AND column_name='show_leaderboard'
            """)).mappings().first()
            if not res:
                conn.execute(text("ALTER TABLE dinaro_families ADD COLUMN show_leaderboard INTEGER NOT NULL DEFAULT 0"))
