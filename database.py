"""
database.py — Capa de datos SQLite para AdGenius (usuarios, créditos, recargas).

⚠️ PERSISTENCIA EN RENDER (plan free): el disco es efímero. Este archivo .db
y los comprobantes subidos se BORRAN en cada redeploy/reinicio del servicio.
Válido para pruebas; antes de manejar dinero real usa un Persistent Disk
(plan pago) o una base externa (Render Postgres, Supabase, Neon...). Toda la
lógica de acceso a datos vive aquí: migrar después solo implica reescribir
estas funciones.
"""

import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "adgenius.db")
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            credits INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS recargas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            monto TEXT,
            metodo TEXT,
            creditos INTEGER NOT NULL DEFAULT 1,
            comprobante_path TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            created_at INTEGER NOT NULL,
            resuelto_at INTEGER
        )""")


def upsert_user(user_id: str, email: str, name: str, picture: str) -> sqlite3.Row:
    """Crea o actualiza el usuario tras el login. El rol se sincroniza con
    ADMIN_EMAILS en cada login (solo puede subir a admin, nunca lo baja solo)."""
    email_l = email.lower()
    role_si_admin = "admin" if email_l in ADMIN_EMAILS else None
    with get_db() as db:
        existing = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if existing:
            nuevo_role = role_si_admin or existing["role"]
            db.execute("UPDATE users SET email=?, name=?, picture=?, role=? WHERE id=?",
                       (email_l, name, picture, nuevo_role, user_id))
        else:
            db.execute(
                "INSERT INTO users (id, email, name, picture, role, credits, created_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, email_l, name, picture, role_si_admin or "user", 0, int(time.time())))
    return get_user(user_id)


def get_user(user_id: str):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def add_credits(user_id: str, cantidad: int):
    with get_db() as db:
        db.execute("UPDATE users SET credits = credits + ? WHERE id=?", (cantidad, user_id))


def consumir_credito(user_id: str) -> bool:
    with get_db() as db:
        row = db.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or row["credits"] <= 0:
            return False
        db.execute("UPDATE users SET credits = credits - 1 WHERE id=?", (user_id,))
        return True


def crear_recarga(user_id: str, monto: str, metodo: str, creditos: int, comprobante_path: str) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO recargas (user_id, monto, metodo, creditos, comprobante_path, estado, created_at) "
            "VALUES (?,?,?,?,?, 'pendiente', ?)",
            (user_id, monto, metodo, creditos, comprobante_path, int(time.time())))
        return cur.lastrowid


def listar_recargas_pendientes():
    with get_db() as db:
        return db.execute("""SELECT r.*, u.email, u.name FROM recargas r
                              JOIN users u ON u.id = r.user_id
                              WHERE r.estado='pendiente' ORDER BY r.created_at ASC""").fetchall()


def obtener_recarga(recarga_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM recargas WHERE id=?", (recarga_id,)).fetchone()


def resolver_recarga(recarga_id: int, aprobar: bool) -> bool:
    with get_db() as db:
        r = db.execute("SELECT * FROM recargas WHERE id=?", (recarga_id,)).fetchone()
        if not r or r["estado"] != "pendiente":
            return False
        nuevo_estado = "aprobado" if aprobar else "rechazado"
        db.execute("UPDATE recargas SET estado=?, resuelto_at=? WHERE id=?",
                   (nuevo_estado, int(time.time()), recarga_id))
        if aprobar:
            db.execute("UPDATE users SET credits = credits + ? WHERE id=?", (r["creditos"], r["user_id"]))
        return True
