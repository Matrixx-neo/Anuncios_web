import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "advance.db")

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (email TEXT PRIMARY KEY, name TEXT, role TEXT, credits INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS transactions
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      user_email TEXT, receipt_path TEXT, status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def create_or_update_user(email: str, name: str, default_role: str):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            db.execute("INSERT INTO users (email, name, role, credits) VALUES (?, ?, ?, ?)", (email, name, default_role, 1))
            db.commit()
            return {"email": email, "name": name, "role": default_role, "credits": 1}
        else:
            db.execute("UPDATE users SET role = ? WHERE email = ?", (default_role, email))
            db.commit()
            return dict(db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())

def get_user(email: str):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(user) if user else None

def deduct_credit(email: str):
    with get_db() as db:
        db.execute("UPDATE users SET credits = credits - 1 WHERE email = ? AND credits > 0", (email,))
        db.commit()

def add_transaction(email: str, receipt_path: str):
    with get_db() as db:
        db.execute("INSERT INTO transactions (user_email, receipt_path, status) VALUES (?, ?, ?)", (email, receipt_path, "PENDIENTE"))
        db.commit()

def get_pending_transactions():
    with get_db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM transactions WHERE status = 'PENDIENTE' ORDER BY created_at DESC")]

def approve_transaction(tx_id: int, credits_to_add: int):
    with get_db() as db:
        tx = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and tx['status'] == 'PENDIENTE':
            db.execute("UPDATE users SET credits = credits + ? WHERE email = ?", (credits_to_add, tx['user_email']))
            db.execute("UPDATE transactions SET status = 'APROBADA' WHERE id = ?", (tx_id,))
            db.commit()
            return True
        return False
