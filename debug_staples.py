import sqlite3
import os

def check_db():
    conn = sqlite3.connect("timecost.db")
    cursor = conn.cursor()
    
    print("--- Personal Profiles ---")
    cursor.execute("SELECT id, profile_name, display_name FROM personal_profiles")
    profiles = cursor.fetchall()
    for p in profiles:
        print(p)
        
    print("\n--- Staples Table ---")
    cursor.execute("SELECT id, owner_key, name, cost FROM staples")
    staples = cursor.fetchall()
    for s in staples:
        print(s)
        
    conn.close()

if __name__ == "__main__":
    check_db()
