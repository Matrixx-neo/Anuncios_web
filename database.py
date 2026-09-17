import sqlite3
from datetime import datetime

DB_NAME = "advance.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    # Tabla de Usuarios
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            credits INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Tabla de Transacciones (Pagos)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            amount REAL,
            operation_code TEXT,
            voucher_b64 TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_user(email):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return user

def create_user(email, credits=1):
    conn = get_connection()
    conn.execute("INSERT OR IGNORE INTO users (email, credits) VALUES (?, ?)", (email, credits))
    conn.commit()
    conn.close()
    return get_user(email)

def update_credits(email, new_credits):
    conn = get_connection()
    conn.execute("UPDATE users SET credits = ? WHERE email = ?", (new_credits, email))
    conn.commit()
    conn.close()

def create_transaction(email, amount, operation_code, voucher_b64):
    conn = get_connection()
    conn.execute(
        "INSERT INTO transactions (email, amount, operation_code, voucher_b64) VALUES (?, ?, ?, ?)",
        (email, amount, operation_code, voucher_b64)
    )
    conn.commit()
    conn.close()

def get_pending_transactions():
    conn = get_connection()
    txs = conn.execute("SELECT * FROM transactions WHERE status = 'pending' ORDER BY created_at DESC").fetchall()
    conn.close()
    return txs

def update_transaction_status(tx_id, status):
    conn = get_connection()
    conn.execute("UPDATE transactions SET status = ? WHERE id = ?", (status, tx_id))
    if status == 'approved':
        tx = conn.execute("SELECT email, amount FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx:
            # Añadir 5 créditos por cada transacción aprobada (ajustable)
            conn.execute("UPDATE users SET credits = credits + 5 WHERE email = ?", (tx['email'],))
    conn.commit()
    conn.close()
