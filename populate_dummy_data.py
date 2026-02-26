import os
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta

def _pin_hash(pin, salt):
    return hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()

def _make_pin(pin):
    salt = secrets.token_hex(8)
    return _pin_hash(pin, salt), salt

def populate_dummy_data():
    db_url = os.environ.get("DATABASE_URL", "sqlite:///timecost.db")
    
    if db_url.startswith("postgresql"):
        import psycopg2
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        placeholder = "%s"
    elif db_url.startswith("postgres://"):
        import psycopg2
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        placeholder = "%s"
    else:
        conn = sqlite3.connect("timecost.db")
        cursor = conn.cursor()
        placeholder = "?"

    # 1. Dinaro - Rose Family
    cursor.execute(f"DELETE FROM dinaro_families WHERE family_code = {placeholder}", ("ROSE",))
    
    pin_hash, pin_salt = _make_pin("1234")
    cursor.execute(f"""
        INSERT INTO dinaro_families (name, rate_per_hour, family_code, is_classroom, interest_rate, interest_threshold, tax_rate)
        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
        RETURNING id
    """, ("Rose Family", 10.0, "ROSE", 0, 2.0, 50.0, 1.0))
    family_id = cursor.fetchone()[0]

    cursor.execute(f"""
        INSERT INTO dinaro_parents (family_id, name, pin_hash, pin_salt)
        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})
    """, (family_id, "Johnny Rose", pin_hash, pin_salt))

    child_pin_hash, child_pin_salt = _make_pin("0000")
    cursor.execute(f"""
        INSERT INTO dinaro_children (family_id, name, balance, pin_hash, pin_salt, view_mode)
        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
        RETURNING id
    """, (family_id, "David", 150.0, child_pin_hash, child_pin_salt, "teen"))
    david_id = cursor.fetchone()[0]

    cursor.execute(f"""
        INSERT INTO dinaro_children (family_id, name, balance, pin_hash, pin_salt, view_mode)
        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
        RETURNING id
    """, (family_id, "Alexis", 75.0, child_pin_hash, child_pin_salt, "visual"))
    alexis_id = cursor.fetchone()[0]

    chores = [
        ("Fold the Cheese", 1.0, "none", "income"),
        ("Clean the Motel", 2.0, "daily", "income"),
        ("Organize Wigs", 0.5, "weekly", "income"),
        ("Rose Apothecary Shift", 4.0, "none", "income"),
        ("Herb Ertlinger Fruit Wine Tasting", 2.0, "none", "income")
    ]
    for title, hours, rec, ctype in chores:
        cursor.execute(f"""
            INSERT INTO dinaro_chores (family_id, title, default_hours, recurrence, chore_type, active)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
        """, (family_id, title, hours, rec, ctype, 1))

    spendables = [
        ("Designer Sweater", 50.0),
        ("New Wig for Moira", 30.0),
        ("Enchante Product", 15.0),
        ("A Little Bit Alexis Single", 5.0)
    ]
    for title, cost in spendables:
        cursor.execute(f"""
            INSERT INTO dinaro_spendables (family_id, title, cost_dinaro, active)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})
        """, (family_id, title, cost, 1))

    # 2. Personal Profile - Johnny Rose
    cursor.execute(f"DELETE FROM personal_profiles WHERE profile_name = {placeholder}", ("JohnnyRose",))
    johnny_pin_hash, johnny_pin_salt = _make_pin("1234")
    cursor.execute(f"""
        INSERT INTO personal_profiles (profile_name, pin_hash, pin_salt, display_name, currency, work_hours, annual_rate, hourly_rate, pay_frequency, paycheck_amount, created_at, updated_at)
        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
    """, ("JohnnyRose", johnny_pin_hash, johnny_pin_salt, "Johnny Rose", "$", 45, 65000, 31.25, "monthly", 4200, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))

    # 3. Expenses
    cursor.execute(f"DELETE FROM expenses WHERE owner_key = {placeholder}", ("JohnnyRose",))
    expenses = [
        ("Motel Rent", 800.0, "House & Light"),
        ("Rose Apothecary Inventory", 500.0, "Provisions"),
        ("Wig Maintenance", 150.0, "Odds & Ends"),
        ("Fruit Wine Fund", 100.0, "Provisions")
    ]
    for name, amount, cat in expenses:
        cursor.execute(f"""
            INSERT INTO expenses (name, amount, category, scope, owner_key)
            VALUES ({placeholder}, {placeholder}, {placeholder}, 'personal', {placeholder})
        """, (name, amount, cat, "JohnnyRose"))

    # 4. Freelance
    cursor.execute(f"DELETE FROM freelance_entries WHERE client = {placeholder}", ("Rosebud Motel Group",))
    freelance_entries = [
        (datetime.now().strftime("%Y-%m-%d"), "Rosebud Motel Group", 5.0, 150.0, "Consulting on expansion"),
        ((datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"), "Rosebud Motel Group", 3.0, 150.0, "Drafting business plan")
    ]
    for d, client, hours, rate, notes in freelance_entries:
        if db_url.startswith("postgresql") or db_url.startswith("postgres://"):
            cursor.execute(f"""
                INSERT INTO freelance_entries (work_date, client, hours, hourly_rate, notes)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
            """, (d, client, hours, rate, notes))
        else:
            cursor.execute(f"""
                INSERT INTO freelance_entries (work_date, entry_date, client, hours, hourly_rate, notes)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
            """, (d, d, client, hours, rate, notes))

    # 5. Goals
    cursor.execute(f"DELETE FROM goals WHERE name = {placeholder}", ("Buy the Town",))
    cursor.execute(f"""
        INSERT INTO goals (name, target, current)
        VALUES ({placeholder}, {placeholder}, {placeholder})
    """, ("Buy the Town", 1000000.0, 500.0))

    conn.commit()
    conn.close()
    print("Rose Family dummy data populated successfully!")

if __name__ == "__main__":
    populate_dummy_data()
