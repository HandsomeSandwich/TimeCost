import sqlite3

def get_db_connection():
    conn = sqlite3.connect('timecost.db')
    conn.row_factory = sqlite3.Row  # Enables dict-like access in results
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create goals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target REAL NOT NULL,
            current REAL NOT NULL
        )
    ''')

    # Create expenses table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL
        )
    ''')

    conn.commit()
    conn.close()
