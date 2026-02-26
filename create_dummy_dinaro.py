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
    conn = sqlite3.connect("timecost.db")
    cursor = conn.cursor()

    # 1. Create a Family
    pin_hash, pin_salt = _make_pin("1234")
    cursor.execute("""
        INSERT INTO dinaro_families (name, rate_per_hour, family_code, is_classroom, interest_rate, interest_threshold, tax_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("Song Family", 4.0, "DUMMY1", 0, 1.0, 10.0, 0.5))
    family_id = cursor.lastrowid

    # 2. Create a Parent
    cursor.execute("""
        INSERT INTO dinaro_parents (family_id, name, pin_hash, pin_salt)
        VALUES (?, ?, ?, ?)
    """, (family_id, "Parent One", pin_hash, pin_salt))

    # 3. Create Children
    child_pin_hash, child_pin_salt = _make_pin("0000")
    cursor.execute("""
        INSERT INTO dinaro_children (family_id, name, balance, pin_hash, pin_salt, view_mode)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (family_id, "Alice", 25.0, child_pin_hash, child_pin_salt, "visual"))
    alice_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO dinaro_children (family_id, name, balance, pin_hash, pin_salt, view_mode)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (family_id, "Bob", 15.0, child_pin_hash, child_pin_salt, "teen"))
    bob_id = cursor.lastrowid

    # 4. Create Chores
    chores = [
        ("Tidy Room", 0.5, "daily", "income"),
        ("Walk Dog", 1.0, "daily", "income"),
        ("Wash Dishes", 0.75, "daily", "income"),
        ("Internet Subscription", 2.0, "weekly", "expense")
    ]
    for title, hours, rec, ctype in chores:
        cursor.execute("""
            INSERT INTO dinaro_chores (family_id, title, default_hours, recurrence, chore_type, active)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (family_id, title, hours, rec, ctype, 1))

    # 5. Create Spendables
    spendables = [
        ("Extra Screen Time (30m)", 2.0),
        ("New Toy", 10.0),
        ("Ice Cream Trip", 5.0)
    ]
    for title, cost in spendables:
        cursor.execute("""
            INSERT INTO dinaro_spendables (family_id, title, cost_dinaro, active)
            VALUES (?, ?, ?, ?)
        """, (family_id, title, cost, 1))

    # 6. Add some Ledger Entries
    now = datetime.utcnow()
    ledger_entries = [
        (alice_id, 2.0, "Chore approved: Tidy Room", (now - timedelta(days=1)).isoformat()),
        (alice_id, 4.0, "Chore approved: Walk Dog", (now - timedelta(days=2)).isoformat()),
        (bob_id, 2.0, "Chore approved: Tidy Room", (now - timedelta(days=1)).isoformat()),
        (bob_id, -2.0, "Bought: Extra Screen Time (30m)", now.isoformat())
    ]
    for cid, delta, reason, created_at in ledger_entries:
        cursor.execute("""
            INSERT INTO dinaro_ledger (child_id, delta, reason, created_at)
            VALUES (?, ?, ?, ?)
        """, (cid, delta, reason, created_at))

    conn.commit()
    conn.close()
    print("Dummy data populated successfully!")

if __name__ == "__main__":
    populate_dummy_data()
